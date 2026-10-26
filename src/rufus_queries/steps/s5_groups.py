"""Paso 5 · Grupos: en cada unidad del foco e intent, junta la misma necesidad dicha con otras palabras (IA).

Entrada : s3_weight/classified_norm.csv · s4_focus/focus.csv
Salida  : out/s5_groups/
    needs.csv     cada necesidad literal (o tipo de producto en G) con su grupo, volumen y ejemplos
    keywords.csv  cada keyword del foco con su unidad y su grupo

Qué entra:
- Intents: keywords con intent (kw_type = intent), salvo selection.exclude_intents (Brand Intent).
- G: keywords genéricas sin marca (kw_type = generic). Aquí cada "necesidad" es el tipo de producto
  ("coffee pods", "k cups"), y el grupo junta los nombres del mismo tipo.
Una llamada por unidad (o varias si tiene muchas líneas). Con provider "claude"/"manual" el paso se para
hasta que se respondan los prompts de out/manual/pending.
"""

import re
import sys

import pandas as pd

from rufus_queries.lib.common import PROMPTS, Config, Out, step_args
from rufus_queries.lib.llm import LLM

STEP = "s5_groups"
MAX_LINES = 250


def fmt(v):
    """Volumen abreviado (1.2M, 35K)."""
    return f"{v / 1e6:.2f}M" if v >= 1e6 else f"{v / 1e3:.0f}K"


def main(argv=None):
    """Paso 5: agrupa las necesidades equivalentes de cada unidad e intent."""
    args = step_args(__doc__.splitlines()[0], argv)
    cfg, out = Config.load(args.config), Out(args.out)
    llm = LLM(cfg, out.base)
    excl = set(cfg.selection.get("exclude_intents", ["I13"]))
    kw = out.read_csv("s3_weight", "classified_norm.csv")
    kw["volume"] = pd.to_numeric(kw.volume, errors="coerce").fillna(0)
    fo = out.read_csv("s4_focus", "focus.csv")
    fo = fo[fo.in_focus.astype(str).str.lower() == "true"]
    unit_of = {(r.category, r.code): (r.unit_id, r.unit_name) for r in fo.itertuples()}
    kw = kw[[(c, k) in unit_of for c, k in zip(kw.category, kw.code, strict=False)]].copy()
    kw["unit_id"] = [unit_of[(c, k)][0] for c, k in zip(kw.category, kw.code, strict=False)]
    kw["unit_name"] = [unit_of[(c, k)][1] for c, k in zip(kw.category, kw.code, strict=False)]

    use = kw[((kw.kw_type == "intent") & ~kw.intent.isin(excl)) | (kw.kw_type == "generic")].copy()
    use["block"] = use.intent.where(use.kw_type == "intent", "G")
    use["need_key"] = use.need.where(use.kw_type == "intent", use.keyword)
    keys = ["category", "unit_id", "unit_name", "block", "need_key"]
    # ejemplos: las 4 keywords con más volumen de cada necesidad (vectorizado: ordenar una vez y quedarse con head(4))
    ex = (
        use.sort_values("volume", ascending=False)
        .groupby(keys, sort=False)
        .head(4)
        .groupby(keys)
        .keyword.agg("; ".join)
        .rename("examples")
    )
    needs = (
        use.groupby(keys)
        .agg(volume=("volume", "sum"), n_kw=("keyword", "size"))
        .join(ex)
        .reset_index()
        .sort_values(["category", "unit_id", "block", "volume"], ascending=[True, True, True, False])
    )
    needs["nid"] = [f"N{i:05d}" for i in range(len(needs))]

    tpl = (PROMPTS / "group.md").read_text(encoding="utf-8")
    pat = re.compile(r"^(N\d{5})\s*\|\s*(.+)$")
    label = {}
    llm.expect(
        sum(-(-len(g) // MAX_LINES) for _, g in needs.groupby(["category", "unit_id", "unit_name"])),
        "Grouping needs (same need, other words)",
    )
    prompts = []
    for (cat, _, uname), g in needs.groupby(["category", "unit_id", "unit_name"]):
        system = tpl.replace("{category}", cat).replace("{unit}", uname)
        for i in range(0, len(g), MAX_LINES):
            part = g.iloc[i : i + MAX_LINES]
            prompts.append(
                (
                    system,
                    "\n".join(
                        f"[{blk}] {r.nid} | {r.need_key} | {fmt(r.volume)} | e.g. {r.examples}"
                        for blk, r in zip(part.block, part.itertuples(), strict=False)
                    ),
                )
            )
    for raw in llm.map("group", prompts):  # en paralelo con Bedrock
        for line in (raw or "").splitlines():
            m = pat.match(line.strip())
            if m:
                label[m.group(1)] = m.group(2).strip().lower()
    if llm.pending:
        print(
            f"[{STEP}] modo {llm.provider}: {len(llm.pending)} prompts pendientes en {out.base / 'manual' / 'pending'}"
        )
        sys.exit(3)  # 3 = esperando respuestas (modo claude/manual)
    needs["group"] = [label.get(n, k) for n, k in zip(needs.nid, needs.need_key, strict=False)]
    # un grupo nunca cruza bloques (intent) ni unidades: la clave del grupo los incluye
    needs["group_id"] = needs.category + "|" + needs.unit_id + "|" + needs.block + "|" + needs.group
    out.write_csv(STEP, "needs.csv", needs)
    gk = dict(
        zip(zip(needs.category, needs.unit_id, needs.block, needs.need_key, strict=False), needs.group, strict=False)
    )
    use["group"] = [
        gk.get(t, n)
        for t, n in zip(
            zip(use.category, use.unit_id, use.block, use.need_key, strict=False), use.need_key, strict=False
        )
    ]
    out.write_csv(STEP, "keywords.csv", use)
    for cat, g in needs.groupby("category"):
        print(f"[{STEP}] {cat}: {len(g)} necesidades → {g.group_id.nunique()} grupos en {g.unit_id.nunique()} unidades")
    llm.report(STEP)


if __name__ == "__main__":
    main()
