"""Paso 4 · Foco: por categoría, las unidades (hojas o hermanas juntadas) que entran en la selección.

Por defecto focus.threshold = 1.0: entran todas las hojas con keywords y la selección (paso 6) decide por peso.
Antes era 0.8 (las hojas del 80% del volumen), pero dejaba fuera grupos con más demanda que preguntas elegidas
(p. ej. "headphones for school" 1,45% frente a preguntas de 0,10%). Se puede bajar para análisis.

Entrada : s3_weight/leaf_weights.csv, classified_norm.csv · s2_classify/nodes_map.csv
Salida  : out/s4_focus/
    merges.csv   hojas hermanas juntadas (mismo producto para el comprador) y el motivo
    units.csv    unidades del corte (una hoja, o varias hermanas juntadas): volumen, %, acumulado, in_focus
    focus.csv    cada hoja con su unidad y si está en el foco
    summary.csv  por categoría: unidades, unidades en foco, % del volumen

1. Hojas hermanas (IA, focus.merge_siblings): para cada rama con 2+ hojas con keywords (y alguna ≥ min_leaf_pct),
   el LLM ve las hojas y sus keywords principales y dice si alguna es el MISMO producto. Solo si es muy claro;
   por defecto no se junta nada. El universo y el dashboard siguen con las hojas de Amazon: la fusión solo
   cuenta para el corte y los pasos siguientes.
2. Corte: unidades ordenadas por volumen; entran mientras el acumulado ANTERIOR sea < focus.threshold (la que
   cruza el umbral entra). Opcional focus.min_leaf_share: entra también cualquier unidad con al menos ese %.
"""

import re
import sys

import pandas as pd

from rufus_queries.lib.common import PROMPTS, Config, Out, step_args
from rufus_queries.lib.llm import LLM

STEP = "s4_focus"


def fmt(v):
    """Volumen abreviado (1.2M, 35K)."""
    return f"{v / 1e6:.2f}M" if v >= 1e6 else f"{v / 1e3:.0f}K"


class LeafKw:  # pylint: disable=too-few-public-methods
    """Keywords de una hoja ordenadas por volumen, calculadas solo cuando se piden."""

    def __init__(self, kw, idx):
        self.kw, self.idx = kw, idx

    def get(self, key):
        """Keywords de la hoja key ordenadas por volumen (vacío si no tiene)."""
        ix = self.idx.get(key)
        return self.kw.iloc[0:0] if ix is None else self.kw.iloc[ix].sort_values("volume", ascending=False)


def sibling_jobs(cat, g, kw_by_leaf, parent_of, mc, tpl):
    """Prompts para revisar hojas hermanas de una categoría: [(códigos válidos, system, user)]."""
    g = g.assign(parent=g.code.map(lambda c: parent_of.get((cat, c))))
    jobs = []
    for par, sib in g.groupby("parent"):
        sib = sib[sib.n_kw > 0]
        if len(sib) < 2 or sib.pct.max() < float(mc.get("min_leaf_pct", 0.5)):
            continue
        lines = []
        for r in sib.sort_values("total", ascending=False).itertuples():
            k = kw_by_leaf.get((cat, r.code))
            pref = k[k.kw_type.isin(["generic", "intent"])]
            top = (pref if len(pref) >= 3 else k).head(int(mc.get("top_keywords", 10)))
            tops = "; ".join(f"{x} ({fmt(v)})" for x, v in zip(top.keyword, top.volume, strict=False))
            lines.append(f"{r.code} | {r.leaf_name} | {r.pct:.1f}% of category | top: {tops}")
        jobs.append((set(sib.code), tpl.replace("{category}", cat), f"Parent branch: {par}\n\n" + "\n".join(lines)))
    return jobs


def parse_merges(raw, valid):
    """Grupos de hojas hermanas a fusionar a partir de la respuesta de la IA (solo códigos válidos)."""
    merges = []
    for line in (raw or "NONE").splitlines():
        m = re.match(r"^\s*([LP]\d+(?:\s*\+\s*[LP]\d+)+)\s*\|\s*(.*)$", line.strip())
        if m:
            codes = {c.strip() for c in m.group(1).split("+")}
            if codes <= valid and len(codes) >= 2:
                merges.append((codes, m.group(2).strip()))
    return merges


def main(argv=None):
    """Paso 4: fusiona hermanas equivalentes y corta las unidades que suman el umbral del foco."""
    args = step_args(__doc__.splitlines()[0], argv)
    cfg, out = Config.load(args.config), Out(args.out)
    fc = cfg.get("focus", {})
    th = float(fc.get("threshold", 1.0)) * 100
    min_share = fc.get("min_leaf_share")
    mc = fc.get("merge_siblings", {"enabled": True})
    lw = out.read_csv("s3_weight", "leaf_weights.csv")
    lw = lw[pd.to_numeric(lw.total) > 0].copy()
    kw = out.read_csv("s3_weight", "classified_norm.csv")
    nm = out.read_csv("s2_classify", "nodes_map.csv")
    tpl = (PROMPTS / "merge_siblings.md").read_text(encoding="utf-8")
    llm = LLM(cfg, out.base) if mc.get("enabled", True) else None

    # 1 · hojas hermanas: se preparan todos los prompts de todas las categorías y se lanzan juntos
    merges_of = {}
    if llm:
        # keywords de cada hoja ordenadas por volumen (mismo orden que antes: así los prompts y la caché no cambian)
        idx = kw.groupby(["category", "code"], sort=False).indices
        kw_by_leaf = LeafKw(kw, idx)
        parent_of = {(r.category, r.code): r.node_path.rsplit(" > ", 1)[0] for r in nm.itertuples()}
        jobs = [
            (cat, *j) for cat, g in lw.groupby("category") for j in sibling_jobs(cat, g, kw_by_leaf, parent_of, mc, tpl)
        ]
        llm.expect(len(jobs), "Checking sibling leaves")
        raws = llm.map("merge_siblings", [(sy, us) for _, _, sy, us in jobs])
        if llm.pending:
            print(f"[{STEP}] modo manual: {len(llm.pending)} prompts pendientes en {out.base / 'manual' / 'pending'}")
            sys.exit(3)  # 3 = esperando respuestas (modo claude/manual)
        for (cat, valid, _, _), raw in zip(jobs, raws, strict=False):
            merges_of.setdefault(cat, []).extend(parse_merges(raw, valid))

    all_merges, units_all, focus_all, summ = [], [], [], []
    for cat, g in lw.groupby("category"):
        g = g.copy()
        unit = {c: c for c in g.code}
        merges = merges_of.get(cat, [])
        for codes, why in merges:
            root = unit[sorted(codes)[0]]
            olds = {unit[c] for c in codes}
            for k_, v in unit.items():
                if v in olds:
                    unit[k_] = root
            names = " + ".join(g[g.code.isin(codes)].sort_values("total", ascending=False).leaf_name)
            all_merges.append({"category": cat, "codes": "+".join(sorted(codes)), "leaves": names, "reason": why})
        g["unit_id"] = g.code.map(unit)

        # 2 · corte por unidades
        u = (
            g.groupby("unit_id")
            .agg(total=("total", "sum"), pct=("pct", "sum"), n_kw=("n_kw", "sum"), n_leaves=("code", "size"))
            .reset_index()
        )
        names = g.sort_values("total", ascending=False).groupby("unit_id").leaf_name.agg(" + ".join)
        u["unit_name"] = u.unit_id.map(names)
        u = u.sort_values("total", ascending=False).reset_index(drop=True)
        u["rank"] = range(1, len(u) + 1)
        u["cum"] = u.pct.cumsum()
        u["in_focus"] = u.cum.shift(fill_value=0) < th
        u["reason"] = u.in_focus.map({True: "cut", False: ""})
        if min_share is not None:
            extra = (~u.in_focus) & (u.pct >= float(min_share))
            u.loc[extra, ["in_focus", "reason"]] = [True, "min_share"]
        u.insert(0, "category", cat)
        units_all.append(u)
        focus_all.append(g.merge(u[["unit_id", "unit_name", "rank", "in_focus"]], on="unit_id"))

        f = u[u.in_focus]
        summ.append(
            {
                "category": cat,
                "units": len(u),
                "merged": len(merges),
                "focus_units": len(f),
                "focus_pct": round(f.pct.sum(), 1),
            }
        )
        print(
            f"[{STEP}] {cat}: {len(f)} de {len(u)} unidades = {f.pct.sum():.1f}% del volumen"
            + (f" · {len(merges)} fusiones de hermanas" if merges else " · sin fusiones")
        )
        for codes, why in merges:
            print(f"         + junta {' + '.join(g[g.code.isin(codes)].leaf_name)}: {why}")
        for r in f.itertuples():
            print(
                f"         {r.rank:>2}. {r.unit_name:<45} {r.pct:5.1f}%  acum {r.cum:5.1f}%"
                + ("  (mín.)" if r.reason == "min_share" else "")
            )
        nxt = u[~u.in_focus].head(3)
        if len(nxt):
            print("         fuera: " + " · ".join(f"{r.unit_name} {r.pct:.1f}%" for r in nxt.itertuples()))

    out.write_csv(STEP, "merges.csv", pd.DataFrame(all_merges, columns=["category", "codes", "leaves", "reason"]))
    out.write_csv(STEP, "units.csv", pd.concat(units_all))
    out.write_csv(STEP, "focus.csv", pd.concat(focus_all))
    out.write_csv(STEP, "summary.csv", pd.DataFrame(summ))
    if llm:
        llm.report(STEP)


if __name__ == "__main__":
    main()
