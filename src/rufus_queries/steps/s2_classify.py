"""Paso 2 · Clasificar: cada keyword → categoría (1 de 325) → nodo de su árbol, con intent, necesidad y marca (IA).

Entrada : s1_universe/{categories,nodes}.csv · inputs.keywords (keyword + volumen) · inputs.intents
Salida  : out/s2_classify/
    keywords.csv         keywords normalizadas (kid, keyword, volume)
    category_assign.csv  2a · kid → categoría (o XX = ninguna)
    nodes_map.csv        códigos locales de cada categoría (L.. hojas, P.. ramas) → ruta del árbol
    classified.csv       2b · solo categorías en alcance: categoría, code, node_path, intent, need, brand
    summary.csv          keywords y volumen por categoría

2a se hace para TODAS las keywords (hace falta para saber cuáles caen en cada categoría).
2b se hace solo para las categorías en alcance (config scope.categories_regex).
Las respuestas del LLM se guardan en caché (out/cache): repetir el paso no vuelve a pagar llamadas.
"""

import re
import sys
import time
from pathlib import Path

import pandas as pd

from rufus_queries.lib.common import PROMPTS, Config, Out, step_args
from rufus_queries.lib.intents import load_intents
from rufus_queries.lib.llm import LLM

STEP = "s2_classify"
KW_COLS = ["keyword", "keyword_text", "seed_description", "search_term", "query"]
VOL_COLS = ["volume", "search_volume", "keyword_volume"]
PRECEDENCE = ["I04", "I08", "I03", "I01", "I07", "I02", "I06", "I05", "I10", "I14", "I12", "I09", "I11", "I15", "I13"]


# ── entradas ───────────────────────────────────────────────────────────────


def load_keywords(path: Path, sample=None) -> pd.DataFrame:
    """Keywords y volumen normalizados (minúsculas, sin duplicados), con id estable kid."""
    df = pd.read_excel(path) if path.suffix.lower() in (".xlsx", ".xlsm") else pd.read_csv(path, encoding="utf-8-sig")
    cols = {c.strip().lower(): c for c in df.columns}
    kcol = next((cols[c] for c in KW_COLS if c in cols), None)
    vcol = next((cols[c] for c in VOL_COLS if c in cols), None)
    if kcol is None or vcol is None:
        raise ValueError(
            f"El fichero de keywords necesita una columna de keyword y otra de volumen (tiene {list(df.columns)})"
        )
    out = pd.DataFrame(
        {
            "keyword": df[kcol].astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True),
            "volume": pd.to_numeric(df[vcol], errors="coerce").fillna(0),
        }
    )
    out = out[out.keyword.str.len() > 0].groupby("keyword", as_index=False).volume.sum()
    if sample:
        out = out.sort_values("volume", ascending=False).head(int(sample))  # muestra = las más buscadas
    out = out.sort_values("keyword").reset_index(drop=True)  # orden alfabético: variantes de una marca juntas
    out.insert(0, "kid", [f"K{i:06d}" for i in range(len(out))])
    return out


def category_lines(cats: pd.DataFrame, nodes: pd.DataFrame, n_examples: int):
    """Líneas 'Cxxx | categoría | ejemplos' para el prompt 2a y el mapa código → categoría."""
    lines, code_of = [], {}
    for i, c in enumerate(cats.category, 1):
        code = f"C{i:03d}"
        code_of[code] = c
        kids = nodes[nodes.parent_path == c].sort_values("n_leaves", ascending=False).node_path
        ex = ", ".join(k.split(" > ")[-1] for k in kids.head(n_examples))
        lines.append(f"{code} | {c} | e.g. {ex}")
    return "\n".join(lines), code_of


def category_nodes(category: str, nodes: pd.DataFrame) -> pd.DataFrame:
    """Nodos (hojas L y ramas P) de una categoría con su código para el prompt 2b."""
    sub = nodes[nodes.node_path.str.startswith(category + " > ")].copy()
    sub["rel"] = sub.node_path.str[len(category) + 3 :]
    leaves = sub[sub.is_leaf].sort_values("rel").reset_index(drop=True)
    inner = sub[~sub.is_leaf].sort_values("rel").reset_index(drop=True)
    w = max(2, len(str(max(len(leaves), len(inner), 1))))
    leaves["code"] = [f"L{i:0{w}d}" for i in range(1, len(leaves) + 1)]
    inner["code"] = [f"P{i:0{w}d}" for i in range(1, len(inner) + 1)]
    m = pd.concat([leaves, inner])[["code", "node_path", "rel", "is_leaf"]]
    m.insert(0, "category", category)
    return m


# ── llamadas ───────────────────────────────────────────────────────────────


def batched(df, n):
    """Trocea un DataFrame en lotes de n filas."""
    for i in range(0, len(df), n):
        yield df.iloc[i : i + n]


def ask_many(llm, stage, jobs: list, pattern: re.Pattern) -> dict:
    """jobs = [(system, lote)]. Lanza todos los lotes (en paralelo con Bedrock) y devuelve {kid: campos}.
    Las keywords que no vuelvan en la respuesta se piden una segunda vez, en un lote con solo las que faltan."""
    found = {}
    todo, got = list(jobs), []
    for rnd in range(2):
        if rnd:  # segunda vuelta: en cada lote, solo las keywords que no volvieron
            todo = [(sy, b[~b.kid.isin(ok)]) for (sy, b), ok in zip(todo, got, strict=False)]
            todo = [(sy, b) for sy, b in todo if not b.empty]
        if not todo:
            break
        raws = llm.map(stage, [(sy, "\n".join(f"{r.kid}\t{r.keyword}" for r in b.itertuples())) for sy, b in todo])
        got = []
        for (_, b), raw in zip(todo, raws, strict=False):
            ids, ok = set(b.kid), set()
            for line in (raw or "").splitlines():
                m = pattern.match(line.strip())
                if m and m.group(1) in ids:
                    found[m.group(1)] = m.groups()[1:]
                    ok.add(m.group(1))
            got.append(ok)
    return found


# ── paso ───────────────────────────────────────────────────────────────────


def main(argv=None):
    """Paso 2: clasifica cada keyword (categoría, nodo, intent, necesidad, marca)."""
    args = step_args(__doc__.splitlines()[0], argv)
    cfg, out = Config.load(args.config), Out(args.out)
    llm = LLM(cfg, out.base)
    cc = cfg.get("classify", {})
    bs_cat, bs_leaf = int(cc.get("batch_category", 250)), int(cc.get("batch_leaf", 150))

    cats = out.read_csv("s1_universe", "categories.csv")
    nodes = out.read_csv("s1_universe", "nodes.csv")
    nodes["is_leaf"] = nodes.is_leaf.astype(str).str.lower() == "true"
    intents = load_intents(cfg.input_path("intents"))
    kw = load_keywords(cfg.input_path("keywords"), cc.get("sample"))
    out.write_csv(STEP, "keywords.csv", kw)
    n_a = -(-len(kw) // bs_cat)
    print(f"[{STEP}] {len(kw):,} keywords · {kw.volume.sum():,.0f} de volumen · proveedor {llm.provider}")

    # 2a · keyword → categoría
    cat_text, code_of = category_lines(cats, nodes, int(cc.get("examples_per_category", 8)))
    sys_a = (PROMPTS / "classify_category.md").read_text(encoding="utf-8").replace("{categories}", cat_text)
    pat_a = re.compile(r"^(K\d+)\|(C\d{3}|XX)$")
    print(f"[{STEP}] 2a · categoría: {n_a} lotes de {bs_cat} keywords")
    llm.expect(n_a, "Keyword → category")
    t0 = time.time()
    assign = ask_many(llm, "classify_category", [(sys_a, b) for b in batched(kw, bs_cat)], pat_a)
    print(f"         {n_a} lotes en {time.time() - t0:,.0f}s ({llm.workers} en paralelo)")
    kw["cat_code"] = kw.kid.map(lambda k: assign.get(k, ("??",))[0])
    kw["category"] = kw.cat_code.map(lambda c: code_of.get(c, c))
    out.write_csv(STEP, "category_assign.csv", kw)
    if llm.pending:
        print(f"[{STEP}] modo manual: {len(llm.pending)} prompts pendientes en {out.base / 'manual' / 'pending'}")
        sys.exit(3)  # 3 = esperando respuestas (modo claude/manual)

    # 2b · dentro de cada categoría en alcance → nodo, intent, necesidad, marca
    scope = cats[cats.in_scope.astype(str).str.lower() == "true"].category.tolist()
    names = {i.code: i.name for i in intents}
    intents_text = "\n".join(i.prompt_line() for i in intents)
    prec_text = " > ".join(f"{c} {names.get(c, '')}" for c in cc.get("precedence", PRECEDENCE))
    tpl = (PROMPTS / "classify_leaf.md").read_text(encoding="utf-8")
    pat_b = re.compile(r"^(K\d+)\|([LP]\d+|N0|NX)\|(I\d{2}|G)?\|([^|]*)\|([^|]*)$")
    maps, rows = [], []
    llm.expect(sum(-(-int((kw.category == c).sum()) // bs_leaf) for c in scope), "Keyword → leaf, intent and brand")
    jobs, per_cat = [], []
    for c in scope:
        ck = kw[kw.category == c]
        nm = category_nodes(c, nodes)
        maps.append(nm)
        node_text = "\n".join(f"{r.code} | {r.rel}" for r in nm.itertuples())
        sys_b = (
            tpl.replace("{category}", c)
            .replace("{nodes}", node_text)
            .replace("{intents}", intents_text)
            .replace("{precedence}", prec_text)
        )
        print(f"[{STEP}] 2b · {c}: {len(ck):,} keywords · {len(nm)} nodos · {-(-len(ck) // bs_leaf)} lotes")
        jobs += [(sys_b, b) for b in batched(ck, bs_leaf)]
        per_cat.append((c, ck, nm))
    t0 = time.time()
    res = ask_many(llm, "classify_leaf", jobs, pat_b)  # todas las categorías a la vez
    if jobs:
        print(f"         {len(jobs)} lotes en {time.time() - t0:,.0f}s ({llm.workers} en paralelo)")
    for c, ck, nm in per_cat:
        path_of, leaf_of = dict(zip(nm.code, nm.node_path, strict=False)), dict(zip(nm.code, nm.is_leaf, strict=False))
        for r in ck.itertuples():
            code, intent, need, brand = res.get(r.kid, ("??", "", "", ""))
            intent, need = intent or "", (need or "").strip().lower()
            if code in ("NX", "??"):
                intent, need = "", ""
            rows.append(
                {
                    "kid": r.kid,
                    "keyword": r.keyword,
                    "volume": r.volume,
                    "category": c,
                    "code": code,
                    "node_path": path_of.get(code, ""),
                    "is_leaf": leaf_of.get(code, False),
                    "intent": intent,
                    "need": need,
                    "need_literal": (need == "" or need in r.keyword),
                    "brand": (brand or "").strip().lower(),
                }
            )
    if maps:
        out.write_csv(STEP, "nodes_map.csv", pd.concat(maps))
    cl = pd.DataFrame(
        rows,
        columns=[
            "kid",
            "keyword",
            "volume",
            "category",
            "code",
            "node_path",
            "is_leaf",
            "intent",
            "need",
            "need_literal",
            "brand",
        ],
    )
    out.write_csv(STEP, "classified.csv", cl)

    s = kw.groupby("category").agg(keywords=("kid", "size"), volume=("volume", "sum")).reset_index()
    s["pct_volume"] = (s.volume / s.volume.sum() * 100).round(2)
    out.write_csv(STEP, "summary.csv", s.sort_values("volume", ascending=False))
    print(
        f"[{STEP}] sin categoría (XX): {(kw.category == 'XX').mean():.1%} · "
        f"sin respuesta (??): {(kw.category == '??').sum()}"
    )
    if len(cl):
        prod = cl[~cl.code.isin(["NX", "N0", "??"])]
        print(
            f"[{STEP}] en alcance: {len(cl):,} keywords → {len(prod):,} de la categoría "
            f"({prod.volume.sum():,.0f} de volumen) · NX {(cl.code == 'NX').sum():,} · "
            f"necesidad no literal {(~cl.need_literal).sum()}"
        )
    print(f"[{STEP}] LLM: {llm.stats}")


if __name__ == "__main__":
    main()
