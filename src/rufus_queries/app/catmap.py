"""Mapa de categorías (treemap) con navegación sector → categoría → ramas/hojas.

Se usa en dos sitios:
- New run: muestra solo el subárbol de las categorías elegidas (tamaño = nº de hojas, aún no hay volumen).
- Página de una ejecución con varias categorías: el subárbol de esa ejecución con su volumen real (paso 3).
Los datos son una lista plana, una fila por categoría, para poder filtrar en el navegador.
"""

import pandas as pd

from rufus_queries.app.assets import asset


def _branch(path: str) -> str:
    p = path.split(" > ")
    return p[2] if len(p) > 3 else "(directly in the category)"


def rows_from_tree(leaves: pd.DataFrame) -> list:
    """leaves: salida de load_tree (breadcrumb, sector, category). Tamaño = nº de hojas por rama."""
    out = []
    lv = leaves.assign(
        branch=leaves.breadcrumb.apply(
            lambda b: b.split(" > ")[2] if len(b.split(" > ")) > 2 else "(directly in the category)"
        )
    )
    for (sec, cat), g in lv.groupby(["sector", "category"]):
        out.append(
            {"s": sec, "c": cat, "n": len(g), "b": [{"name": b, "value": len(x)} for b, x in g.groupby("branch")]}
        )
    return out


def rows_from_run(lw: pd.DataFrame, focus: set) -> list:
    """lw: s3_weight/leaf_weights.csv. Tamaño = volumen real de búsqueda; marca las hojas en el foco."""
    lw = lw.copy()
    lw["total"] = pd.to_numeric(lw.total, errors="coerce").fillna(0)
    lw["pct"] = pd.to_numeric(lw.pct, errors="coerce").fillna(0)
    out = []
    for cat, g in lw.groupby("category", sort=False):
        g = g.assign(branch=g.node_path.apply(_branch))
        br = []
        for b, x in g[g.total > 0].groupby("branch"):
            br.append(
                {
                    "name": b,
                    "leaves": [
                        {
                            "name": r.leaf_name,
                            "code": r.code,
                            "value": float(r.total),
                            "pct": float(r.pct),
                            "focus": (cat, r.code) in focus,
                        }
                        for r in x.itertuples()
                    ],
                }
            )
        out.append({"s": cat.split(" > ")[0], "c": cat, "n": len(g), "vol": float(g.total.sum()), "b": br})
    return out


CSS = asset("catmap.css")

JS = asset("catmap.js")
