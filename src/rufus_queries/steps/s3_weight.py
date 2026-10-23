"""Paso 3 · Pesar: volumen por hoja (suma del search volume), con normalizaciones y reparto de genéricas de rama.

Entrada : s2_classify/classified.csv, nodes_map.csv
Salida  : out/s3_weight/
    classified_norm.csv  keywords de producto (sin NX ni ??) con kw_type (generic / brand_only / intent),
                         brand_family y code final
    leaf_weights.csv     por categoría y hoja: volumen propio, reparto de ramas, total y % de la categoría
    fixes.csv            cada cambio automático (regla, keyword, antes → después)

Reglas automáticas (generales, sin listas por categoría):
1. Familia de marca: "alani nu" → alani, "gatorade zero" → gatorade (la marca más corta que es prefijo),
   más los alias de config weight.brand_aliases.
2. Línea de marca sin azúcar ("diet coke", "red bull sugar free"): marca + necesidad de weight.brand_line_needs → G.
3. Ancla de marca: las keywords que son solo una marca van a la hoja donde esa familia de marca tiene más volumen,
   pero solo si esa hoja reúne al menos weight.anchor_min_share (60%) del volumen de la marca. Si la marca vende
   en varias hojas (p. ej. un café en cápsulas, molido y grano) se respeta lo que decidió el LLM,
   y nunca se mueve una keyword que el LLM puso en una rama (P..).
4. Genéricas de rama (código P.., p. ej. "coffee", "tea"): su volumen se reparte entre las hojas de esa rama en
   proporción al volumen propio de cada hoja (a partes iguales si ninguna tiene volumen).
"""

import re

import pandas as pd

from rufus_queries.lib.common import Config, Out, step_args

STEP = "s3_weight"
DEFAULT_LINE_NEEDS = ["diet", "zero", "zero sugar", "sugar free", "sugarfree", "sugar-free"]


def brand_families(brands, aliases: dict) -> dict:
    """Familia de cada marca: la marca más corta de la que es prefijo ('coke zero' → 'coke'), más los alias."""
    fam = {}
    for b in sorted({x for x in brands if x}, key=len):
        root = next((r for r in set(fam.values()) if b.startswith(r + " ")), None)
        fam[b] = root or b
    for b, a in (aliases or {}).items():
        fam[b] = a
    return fam


def main(argv=None):
    """Paso 3: volumen por hoja con las cuatro correcciones de marca y genéricas."""
    args = step_args(__doc__.splitlines()[0], argv)
    cfg, out = Config.load(args.config), Out(args.out)
    wc = cfg.get("weight", {})
    cl = out.read_csv("s2_classify", "classified.csv")
    nm = out.read_csv("s2_classify", "nodes_map.csv")
    nm["is_leaf"] = nm.is_leaf.astype(str).str.lower() == "true"
    fixes = []

    k = cl[~cl.code.isin(["NX", "??"])].copy()
    k["volume"] = pd.to_numeric(k.volume, errors="coerce").fillna(0)
    for c in ("intent", "need", "brand"):
        k[c] = k[c].fillna("").astype(str)

    # 1 · familias de marca
    fam = brand_families(k.brand, wc.get("brand_aliases", {}))
    k["brand_family"] = k.brand.map(lambda b: fam.get(b, b))

    # 2 · líneas de marca sin azúcar → G
    line_needs = set(wc.get("brand_line_needs", DEFAULT_LINE_NEEDS))
    m = (k.brand != "") & k.need.isin(line_needs) & (k.intent != "G")
    for r in k[m].itertuples():
        fixes.append(
            {
                "rule": "línea de marca → G",
                "category": r.category,
                "keyword": r.keyword,
                "before": f"{r.intent} {r.need}",
                "after": "G",
            }
        )
    k.loc[m, ["intent", "need"]] = ["G", ""]

    # 3 · anclas de marca para keywords que son solo la marca
    def is_brand_only(r):
        rest = re.sub(r"\s+", " ", r.keyword.replace(r.brand, " ")).strip() if r.brand else r.keyword
        return r.brand != "" and r.intent == "G" and rest == ""

    k["brand_only"] = [is_brand_only(r) for r in k.itertuples()]
    leafy = k[k.code.str.startswith("L") & (k.brand_family != "")]
    bv = leafy.groupby(["category", "brand_family", "code"]).volume.sum().reset_index()
    bv["share"] = bv.volume / bv.groupby(["category", "brand_family"]).volume.transform("sum")
    top = bv.sort_values("volume", ascending=False).drop_duplicates(["category", "brand_family"])
    min_share = float(wc.get("anchor_min_share", 0.6))  # solo si la marca vende claramente en una hoja
    anchor = {(r.category, r.brand_family): r.code for r in top.itertuples() if r.share >= min_share}
    path_of = {(r.category, r.code): r.node_path for r in nm.itertuples()}
    for i, r in k[k.brand_only & k.code.str.startswith("L")].iterrows():  # una rama (P..) elegida por el LLM se respeta
        a = anchor.get((r.category, r.brand_family))
        if a and a != r.code:
            fixes.append(
                {"rule": "ancla de marca", "category": r.category, "keyword": r.keyword, "before": r.code, "after": a}
            )
            k.loc[i, "code"], k.loc[i, "node_path"], k.loc[i, "is_leaf"] = a, path_of.get((r.category, a), ""), True

    k["kw_type"] = [
        "brand_only" if b else ("generic" if i == "G" else ("intent" if i else "other"))
        for b, i in zip(k.brand_only, k.intent, strict=False)
    ]
    k.loc[(k.kw_type == "generic") & (k.brand != ""), "kw_type"] = "brand_generic"  # marca + tipo de producto
    out.write_csv(STEP, "classified_norm.csv", k)

    # 4 · volumen por hoja + reparto de ramas
    rows = []
    for cat, g in k[k.code != "N0"].groupby("category"):
        leaves = nm[(nm.category == cat) & nm.is_leaf]
        own = g[g.code.str.startswith("L")].groupby("code").volume.sum()
        nkw = g[g.code.str.startswith("L")].groupby("code").size()
        add = pd.Series(0.0, index=leaves.code)
        for p, vol in g[g.code.str.startswith("P")].groupby("code").volume.sum().items():
            ppath = nm[(nm.category == cat) & (nm.code == p)].node_path.iloc[0]
            kids = leaves[leaves.node_path.str.startswith(ppath + " > ")].code.tolist()
            if not kids:
                continue
            base = own.reindex(kids).fillna(0)
            share = base / base.sum() if base.sum() > 0 else pd.Series(1 / len(kids), index=kids)
            add[kids] += vol * share
        df = leaves[["category", "code", "node_path"]].copy()
        df["leaf_name"] = df.node_path.str.split(" > ").str[-1]
        df["n_kw"] = df.code.map(nkw).fillna(0).astype(int)
        df["own_volume"] = df.code.map(own).fillna(0)
        df["from_nodes"] = df.code.map(add).fillna(0)
        df["total"] = df.own_volume + df.from_nodes
        df["pct"] = df.total / df.total.sum() * 100 if df.total.sum() else 0.0
        rows.append(df.sort_values("total", ascending=False))
    lw = pd.concat(rows) if rows else pd.DataFrame()
    out.write_csv(STEP, "leaf_weights.csv", lw)
    out.write_csv(STEP, "fixes.csv", pd.DataFrame(fixes, columns=["rule", "category", "keyword", "before", "after"]))

    for cat, g in lw.groupby("category"):
        kk = k[k.category == cat]
        t = kk.volume.sum() or 1
        share = kk.groupby("kw_type").volume.sum() / t * 100
        print(
            f"[{STEP}] {cat}: {int((g.n_kw > 0).sum())}/{len(g)} hojas con keywords · {g.total.sum():,.0f} de volumen "
            f"(+{(kk.code == 'N0').sum()} keywords N0 fuera)"
        )
        print("         tipo de keyword (% volumen): " + " · ".join(f"{t_} {v:.1f}%" for t_, v in share.items()))
    print(
        f"[{STEP}] cambios automáticos: "
        + ", ".join(f"{r} {n}" for r, n in pd.Series([f["rule"] for f in fixes]).value_counts().items())
    )


if __name__ == "__main__":
    main()
