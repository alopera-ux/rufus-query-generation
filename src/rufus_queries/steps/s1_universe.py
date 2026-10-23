"""Paso 1 · Universo: el árbol completo de Amazon US aplanado.

Entrada : config inputs.tree (us_2026-09.xlsx, columna breadcrumb por hoja)
Salida  : out/s1_universe/
    leaves.csv      una fila por hoja: leaf_id, node_id, breadcrumb, sector, category, depth, leaf_name
    nodes.csv       todos los nodos (internos y hojas): node_path, parent_path, depth, n_leaves, is_leaf
    categories.csv  una fila por categoría (Sector > Category): n_leaves, in_scope

La categoría es el 2º nivel del árbol (Sector > Category): es la unidad de las 25 preguntas.
scope.categories_regex limita qué categorías se procesan en los pasos siguientes (el universo se guarda entero).
"""

import difflib
import re
import sys
from pathlib import Path

import pandas as pd

from rufus_queries.lib.common import Config, Out, step_args

STEP = "s1_universe"


def load_tree(path: Path) -> pd.DataFrame:
    """Lee el árbol (columna breadcrumb) y devuelve una fila por hoja con sector y categoría."""
    df = pd.read_excel(path, dtype=str) if path.suffix.lower() in (".xlsx", ".xlsm") else pd.read_csv(path, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    if "breadcrumb" not in df.columns:
        raise ValueError(f"El árbol necesita la columna 'breadcrumb' (tiene {list(df.columns)})")
    parts = df["breadcrumb"].str.split(">").apply(lambda p: [x.strip() for x in p if x.strip()])
    parts = parts.apply(lambda p: p[1:] if p and p[0].lower() == "home" else p)
    out = pd.DataFrame(
        {
            "node_id": df.get("node_id", pd.Series([""] * len(df))).fillna(""),
            "breadcrumb": parts.apply(" > ".join),
            "sector": parts.str[0],
            "category": parts.apply(lambda p: " > ".join(p[:2])),
            "depth": parts.str.len(),
            "leaf_name": parts.str[-1],
        }
    )
    out = out[out.depth >= 2].drop_duplicates("breadcrumb").sort_values("breadcrumb").reset_index(drop=True)
    out.insert(0, "leaf_id", [f"L{i:05d}" for i in range(len(out))])
    return out


def build_nodes(leaves: pd.DataFrame) -> pd.DataFrame:
    """Todos los nodos del árbol (internos y hojas) con su número de hojas."""
    count = {}
    for b in leaves.breadcrumb:
        p = b.split(" > ")
        for i in range(1, len(p) + 1):
            k = " > ".join(p[:i])
            count[k] = count.get(k, 0) + 1
    leaf_set = set(leaves.breadcrumb)
    rows = [
        {
            "node_path": k,
            "parent_path": " > ".join(k.split(" > ")[:-1]),
            "depth": len(k.split(" > ")),
            "n_leaves": n,
            "is_leaf": k in leaf_set,
        }
        for k, n in count.items()
    ]
    return pd.DataFrame(rows).sort_values("node_path").reset_index(drop=True)


def main(argv=None):
    """Paso 1: aplana el árbol y marca las categorías en alcance."""
    args = step_args(__doc__.splitlines()[0], argv)
    cfg, out = Config.load(args.config), Out(args.out)
    tree_path = cfg.input_path("tree")
    print(f"[{STEP}] árbol: {tree_path.name}")

    leaves = load_tree(tree_path)
    excl = set(cfg.scope.get("exclude_sectors") or [])
    leaves = leaves[~leaves.sector.isin(excl)].reset_index(drop=True)
    nodes = build_nodes(leaves)

    cats = leaves.groupby(["sector", "category"]).size().rename("n_leaves").reset_index()
    rx = cfg.scope.get("categories_regex")
    cats["in_scope"] = cats.category.apply(lambda c: bool(re.search(rx, c, re.I)) if rx else True)

    out.write_csv(STEP, "leaves.csv", leaves)
    out.write_csv(STEP, "nodes.csv", nodes)
    out.write_csv(STEP, "categories.csv", cats)

    print(
        f"[{STEP}] {leaves.sector.nunique()} sectores · {len(cats)} categorías · {len(leaves):,} hojas · "
        f"{len(nodes):,} nodos"
    )
    print(f"[{STEP}] hojas por categoría: mediana {int(cats.n_leaves.median())}, máx {cats.n_leaves.max()}")
    sc = cats[cats.in_scope]
    if sc.empty:
        near = difflib.get_close_matches(str(rx).strip("^$\"'"), cats.category.tolist(), n=5, cutoff=0.3)
        sys.exit(
            f"[{STEP}] ERROR: ninguna categoría coincide con scope.categories_regex = {rx!r}. "
            f"¿Quizá: {near}? (sin comillas; formato 'Sector > Category')"
        )
    print(f"[{STEP}] en alcance ({rx or 'todas'}): {len(sc)} categorías · {sc.n_leaves.sum():,} hojas")
    for r in sc.head(10).itertuples():
        print(f"         - {r.category}: {r.n_leaves} hojas")


if __name__ == "__main__":
    main()
