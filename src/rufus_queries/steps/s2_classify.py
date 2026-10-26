"""Paso 2 · Clasificar: cada keyword → categoría (1 de 325) → nodo de su árbol, con intent, necesidad y marca (IA).

Entrada : s1_universe/{categories,nodes}.csv · inputs.keywords (keyword + volumen) · inputs.intents
          inputs.keyword_leaves (opcional): keyword → leaf principal según los resultados orgánicos de Amazon y,
          si trae la columna category_share, el % de esos resultados que cae en la categoría de esa leaf
Salida  : out/s2_classify/
    keywords.csv         keywords normalizadas (kid, keyword, volume)
    category_assign.csv  2a · kid → categoría (o XX = ninguna)
    nodes_map.csv        códigos locales de cada categoría (L.. hojas, P.. ramas, con su slug) → ruta del árbol
    classified.csv       2b · solo categorías en alcance: categoría, code, node_path, intent, need, brand
    summary.csv          keywords y volumen por categoría

Con category_share y classify.weight_by_category_share: true, el volumen de cada keyword se multiplica por ese %
(la parte de la demanda que es de la categoría: "lemon" busca zumo solo en parte). Por defecto (false) se usa el
search volume tal cual. El volumen original queda siempre en search_volume.
Con inputs.keyword_leaves, las keywords que no están en ese fichero se descartan (classify.classify_unmapped: false,
por defecto) o pasan por 2a con IA (true). Las que están ya traen su categoría y no pasan por 2a.
En 2b, a esas keywords se les pasa su leaf: la IA pone intent, necesidad y marca; solo puede cambiar el nodo a NX
(no es un producto de la categoría) o a una rama que contiene esa leaf (genéricas como "coffee" → rama Coffee).
2b se hace solo para las categorías en alcance (config scope.categories_regex).
Las respuestas del LLM se guardan en caché (llm.cache_dir): repetir el paso no vuelve a pagar llamadas.

En los prompts, categorías y nodos se identifican con un slug legible ("beverages", "energy-drinks") y no con un
número correlativo: con códigos C158/C159/C160 los modelos pequeños se equivocaban de vecino (bebidas → panadería).
La respuesta se traduce al código interno; un slug que no existe se corrige si se parece mucho a uno válido y,
si no, la keyword se vuelve a preguntar.
"""

import difflib
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
LEAF_COLS = ["top_leaf", "leaf_category", "leaf", "node_path"]
SHARE_COL = "category_share"
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
            "keyword": norm_keyword(df[kcol]),
            "volume": pd.to_numeric(df[vcol], errors="coerce").fillna(0),
        }
    )
    out = out[out.keyword.str.len() > 0].groupby("keyword", as_index=False).volume.sum()
    if sample:
        out = out.sort_values("volume", ascending=False).head(int(sample))  # muestra = las más buscadas
    out = out.sort_values("keyword").reset_index(drop=True)  # orden alfabético: variantes de una marca juntas
    out.insert(0, "kid", [f"K{i:06d}" for i in range(len(out))])
    return out


def norm_keyword(s: pd.Series) -> pd.Series:
    """Misma normalización de keywords en todas las entradas: minúsculas, espacios simples."""
    return s.astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)


def load_keyword_leaves(path: Path, node_paths: set) -> pd.DataFrame:
    """keyword → leaf (y share 0–100 si el fichero trae category_share) de inputs.keyword_leaves, indexado por
    keyword. Se ignoran las filas cuya leaf no está en el árbol."""
    df = pd.read_excel(path) if path.suffix.lower() in (".xlsx", ".xlsm") else pd.read_csv(path, encoding="utf-8-sig")
    cols = {c.strip().lower(): c for c in df.columns}
    kcol = next((cols[c] for c in KW_COLS if c in cols), None)
    lcol = next((cols[c] for c in LEAF_COLS if c in cols), None)
    if kcol is None or lcol is None:
        raise ValueError(f"keyword_leaves necesita una columna de keyword y otra de leaf (tiene {list(df.columns)})")
    share = pd.to_numeric(df[cols[SHARE_COL]], errors="coerce") if SHARE_COL in cols else float("nan")
    m = pd.DataFrame({"keyword": norm_keyword(df[kcol]), "leaf": df[lcol].astype(str).str.strip(), "share": share})
    m = m[m.leaf.isin(node_paths)].drop_duplicates("keyword")
    return m.set_index("keyword")


def category_of(path: str, categories: list) -> str:
    """Categoría (de las de s1) a la que pertenece una ruta del árbol; '' si ninguna."""
    return next((c for c in sorted(categories, key=len, reverse=True) if path.startswith(c + " > ")), "")


def slugify(text: str) -> str:
    """'Bottled beverages, water and drink mixes' → 'bottled-beverages-water-and-drink-mixes'."""
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def unique_slugs(options: list[list[str]]) -> list[str]:
    """Un slug único por elemento: la primera opción libre de su lista (p. ej. [nombre, padre-nombre]);
    si todas están cogidas, la última con un número."""
    used, out = set(), []
    for opts in options:
        cand = next((o for o in opts if o not in used), None)
        if cand is None:
            k = 2
            while f"{opts[-1]}-{k}" in used:
                k += 1
            cand = f"{opts[-1]}-{k}"
        used.add(cand)
        out.append(cand)
    return out


def resolve_slug(raw: str, valid: dict, fixed=("XX", "N0", "NX")):
    """Slug de la respuesta → valor del mapa. Acepta pequeñas variaciones; None si no se reconoce."""
    if raw.upper() in fixed:
        return raw.upper()
    key = slugify(raw)
    if key in valid:
        return valid[key]
    close = difflib.get_close_matches(key, list(valid), n=1, cutoff=0.88)
    return valid[close[0]] if close else None


def category_lines(cats: pd.DataFrame, nodes: pd.DataFrame, n_examples: int):
    """Líneas 'slug | categoría | ejemplos' para el prompt 2a y el mapa slug → categoría."""
    names = list(cats.category)
    slugs = unique_slugs([[slugify(c.split(" > ")[-1]), slugify(c)] for c in names])
    lines, code_of = [], {}
    for slug, c in zip(slugs, names, strict=True):
        code_of[slug] = c
        kids = nodes[nodes.parent_path == c].sort_values("n_leaves", ascending=False).node_path
        ex = ", ".join(k.split(" > ")[-1] for k in kids.head(n_examples))
        lines.append(f"{slug} | {c} | e.g. {ex}")
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
    m = pd.concat([leaves, inner])[["code", "node_path", "rel", "is_leaf"]].reset_index(drop=True)
    parts = m.rel.str.split(" > ")
    m["slug"] = unique_slugs([[slugify(p[-1]), slugify(" ".join(p[-2:])), slugify(" ".join(p))] for p in parts])
    m.insert(0, "category", category)
    return m


def node_lines(nm: pd.DataFrame) -> str:
    """Líneas 'slug | ruta [branch]' de los nodos de una categoría para el prompt 2b."""
    return "\n".join(f"{r.slug} | {r.rel}{'' if r.is_leaf else ' [branch]'}" for r in nm.itertuples())


# ── llamadas ───────────────────────────────────────────────────────────────


def batched(df, n):
    """Trocea un DataFrame en lotes de n filas."""
    for i in range(0, len(df), n):
        yield df.iloc[i : i + n]


def name_key(text: str) -> frozenset:
    """Palabras de un nombre en singular y sin vacías: 'Juices' y 'juice' → {'juice'}."""
    ws = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(w[:-1] if w.endswith("s") and len(w) > 3 else w for w in ws if w not in ("and", "of", "the"))


def final_code(code: str, known_leaf: str, path_of: dict, code_of_path: dict, keyword: str = "") -> str:
    """Nodo final de una keyword en 2b. Con leaf de keyword_leaves, la IA solo puede decir NX (o no responder, ??)
    o subir a una rama que contiene esa leaf: una genérica como "coffee" o "juice" es de toda la rama, no de su
    leaf más vista. Además, una keyword que es justo el nombre de una rama de esa leaf ("coffee" → Coffee,
    "juice" → Juices) va a la rama aunque la IA no lo haya hecho. Cualquier otra respuesta se sustituye por la leaf."""
    if not known_leaf or code in ("NX", "??") or known_leaf.startswith(path_of.get(code, "\0") + " > "):
        return code
    for c, p in path_of.items():
        if known_leaf.startswith(p + " > ") and keyword and name_key(p.split(" > ")[-1]) == name_key(keyword):
            return c
    return code_of_path[known_leaf]


def user_line(r) -> str:
    """'kid<TAB>keyword', y '<TAB>leaf' cuando la keyword trae su leaf de keyword_leaves."""
    hint = getattr(r, "hint", "")
    return f"{r.kid}\t{r.keyword}" + (f"\t{hint}" if hint else "")


def ask_many(llm, stage, jobs: list, pattern: re.Pattern) -> dict:
    """jobs = [(system, lote, resolve)]. Lanza todos los lotes (en paralelo con Bedrock) y devuelve {kid: campos}.
    resolve(campos) traduce la respuesta (slug → código) y devuelve None si no es válida.
    Las keywords que no vuelvan, o vuelvan con un código inválido, se piden una segunda vez en un lote aparte."""
    found = {}
    todo, got = [(j[0], j[1], j[2] if len(j) > 2 else None) for j in jobs], []
    for rnd in range(2):
        if rnd:  # segunda vuelta: en cada lote, solo las keywords que no volvieron bien
            todo = [(sy, b[~b.kid.isin(ok)], rs) for (sy, b, rs), ok in zip(todo, got, strict=False)]
            todo = [(sy, b, rs) for sy, b, rs in todo if not b.empty]
        if not todo:
            break
        raws = llm.map(stage, [(sy, "\n".join(map(user_line, b.itertuples()))) for sy, b, _ in todo])
        got = []
        for (_, b, rs), raw in zip(todo, raws, strict=False):
            ids, ok = set(b.kid), set()
            for line in (raw or "").splitlines():
                m = pattern.match(line.strip())
                if m and m.group(1) in ids:
                    fields = m.groups()[1:] if rs is None else rs(m.groups()[1:])
                    if fields is not None:
                        found[m.group(1)] = fields
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
    print(f"[{STEP}] {len(kw):,} keywords · {kw.volume.sum():,.0f} de volumen · proveedor {llm.provider}")

    known = pd.DataFrame(columns=["leaf", "share"])
    kl = cfg["inputs"].get("keyword_leaves")
    if kl and cfg.input_path("keyword_leaves").exists():
        known = load_keyword_leaves(cfg.input_path("keyword_leaves"), set(nodes.node_path))
    elif kl:
        print(f"[{STEP}] no está {cfg.input_path('keyword_leaves')}: todas las keywords se clasifican con IA")
    kw["known_leaf"] = kw.keyword.map(known.leaf).fillna("")
    cat_names = list(cats.category)
    leaf_cat = {lf: category_of(lf, cat_names) for lf in set(kw.known_leaf) if lf}
    kw["known_cat"] = kw.known_leaf.map(lambda lf: leaf_cat.get(lf, ""))
    kw.loc[kw.known_cat == "", "known_leaf"] = ""
    if len(known):
        k = kw.known_cat != ""
        keep = bool(cc.get("classify_unmapped", False))
        print(
            f"[{STEP}] keyword_leaves: {k.sum():,} keywords ({kw.volume[k].sum() / max(kw.volume.sum(), 1):.1%} "
            f"del volumen) traen categoría y leaf · las otras {(~k).sum():,} "
            + ("se clasifican con IA" if keep else "se descartan")
        )
        if not keep:
            kw = kw[k].reset_index(drop=True)
        share = kw.keyword.map(known.share).where(kw.known_cat != "")
        if not cc.get("weight_by_category_share", False):
            share = share * float("nan")  # solo search volume
        kw.insert(3, "search_volume", kw.volume)
        kw["volume"] = (kw.volume * share.fillna(100) / 100).round(1)
        if share.notna().any():
            print(
                f"[{STEP}] category_share: el volumen útil es el {kw.volume.sum() / kw.search_volume.sum():.1%} "
                "del volumen de búsqueda (parte de la demanda que es de la categoría)"
            )
    ask = kw[kw.known_cat == ""]
    n_a = -(-len(ask) // bs_cat)

    # 2a · keyword → categoría
    cat_text, code_of = category_lines(cats, nodes, int(cc.get("examples_per_category", 8)))
    sys_a = (PROMPTS / "classify_category.md").read_text(encoding="utf-8").replace("{categories}", cat_text)
    pat_a = re.compile(r"^(K\d+)\|\s*([A-Za-z0-9][A-Za-z0-9 _-]*?)\s*$")
    slug_ok = {k: k for k in code_of}

    def res_a(g):
        code = resolve_slug(g[0], slug_ok, fixed=("XX",))
        return None if code is None else (code,)

    print(f"[{STEP}] 2a · categoría: {n_a} lotes de {bs_cat} keywords")
    llm.expect(n_a, "Keyword → category")
    t0 = time.time()
    assign = ask_many(llm, "classify_category", [(sys_a, b, res_a) for b in batched(ask, bs_cat)], pat_a)
    print(f"         {n_a} lotes en {time.time() - t0:,.0f}s ({llm.workers} en paralelo)")
    slug_of = {c: s for s, c in code_of.items()}
    kw["cat_code"] = kw.kid.map(lambda k: assign.get(k, ("??",))[0])
    kw.loc[kw.known_cat != "", "cat_code"] = kw.known_cat.map(slug_of)
    kw["category"] = kw.cat_code.map(lambda c: code_of.get(c, c))
    kw["cat_source"] = (kw.known_cat != "").map({True: "keyword_leaves", False: "ia"})
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
    pat_b = re.compile(r"^(K\d+)\|\s*([A-Za-z0-9][A-Za-z0-9 _-]*?)\s*\|(I\d{2}|G)?\|([^|]*)\|([^|]*)$")
    maps, rows = [], []
    llm.expect(sum(-(-int((kw.category == c).sum()) // bs_leaf) for c in scope), "Keyword → leaf, intent and brand")
    jobs, per_cat = [], []
    for c in scope:
        ck = kw[kw.category == c]
        nm = category_nodes(c, nodes)
        maps.append(nm)
        node_text = node_lines(nm)
        sys_b = (
            tpl.replace("{category}", c)
            .replace("{nodes}", node_text)
            .replace("{intents}", intents_text)
            .replace("{precedence}", prec_text)
        )
        print(f"[{STEP}] 2b · {c}: {len(ck):,} keywords · {len(nm)} nodos · {-(-len(ck) // bs_leaf)} lotes")
        code_by_slug = dict(zip(nm.slug, nm.code, strict=True))
        ck = ck.assign(hint=ck.known_leaf.map(dict(zip(nm.node_path, nm.slug, strict=True))).fillna(""))

        def res_b(g, table=code_by_slug):
            code = resolve_slug(g[0], table, fixed=("N0", "NX"))
            return None if code is None else (code, *g[1:])

        jobs += [(sys_b, b, res_b) for b in batched(ck, bs_leaf)]
        per_cat.append((c, ck, nm))
    t0 = time.time()
    res = ask_many(llm, "classify_leaf", jobs, pat_b)  # todas las categorías a la vez
    if jobs:
        print(f"         {len(jobs)} lotes en {time.time() - t0:,.0f}s ({llm.workers} en paralelo)")
    for c, ck, nm in per_cat:
        path_of, leaf_of = dict(zip(nm.code, nm.node_path, strict=False)), dict(zip(nm.code, nm.is_leaf, strict=False))
        code_of_path = dict(zip(nm.node_path, nm.code, strict=True))
        for r in ck.itertuples():
            code, intent, need, brand = res.get(r.kid, ("??", "", "", ""))
            code = final_code(code, r.known_leaf, path_of, code_of_path, r.keyword)
            intent, need = intent or "", (need or "").strip().lower()
            if code in ("NX", "??"):
                intent, need = "", ""
            rows.append(
                {
                    "kid": r.kid,
                    "keyword": r.keyword,
                    "volume": r.volume,
                    "search_volume": getattr(r, "search_volume", r.volume),
                    "category": c,
                    "code": code,
                    "node_path": path_of.get(code, ""),
                    "is_leaf": leaf_of.get(code, False),
                    "intent": intent,
                    "need": need,
                    "need_literal": (need == "" or need in r.keyword),
                    "brand": (brand or "").strip().lower(),
                    "leaf_source": "keyword_leaves" if r.known_leaf else "ia",
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
            "search_volume",
            "category",
            "code",
            "node_path",
            "is_leaf",
            "intent",
            "need",
            "need_literal",
            "brand",
            "leaf_source",
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
    llm.report(STEP)


if __name__ == "__main__":
    main()
