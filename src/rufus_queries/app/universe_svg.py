"""Gráfico isométrico del universo de una categoría: una celda por hoja de Amazon, altura = search volume.

Se genera como SVG (nítido y con información al pasar el ratón) a partir de los ficheros de una ejecución:
s3_weight/leaf_weights.csv (volumen por hoja) y s4_focus/focus.csv (qué hojas están en el foco).
"""
# pylint: disable=invalid-name  # geometría isométrica: P, T, W, H y A-D son los nombres de las fórmulas

import html
import math

import pandas as pd

from rufus_queries.app.assets import asset

C30, S30 = math.cos(math.pi / 6), math.sin(math.pi / 6)
PALETTE = [
    ("#D5FFBA", "#A1E335"),
    ("#FFE8B2", "#FFC200"),
    ("#E8DEFC", "#B57DFF"),
    ("#FFCFED", "#FF87D4"),
    ("#D9F0FF", "#5AB4F0"),
    ("#FFE0CC", "#FF9A5C"),
    ("#E3F5EE", "#3DBB8E"),
    ("#EDEDED", "#9A9A9A"),
]


def _shade(hexc, f):
    h = hexc.lstrip("#")
    r, g, b = (int(int(h[i : i + 2], 16) * f) for i in (0, 2, 4))
    return f"#{r:02x}{g:02x}{b:02x}"


def _fmt(v):
    return f"{v / 1e6:.1f}M" if v >= 1e6 else (f"{v / 1e3:.0f}K" if v >= 1e3 else f"{v:.0f}")


def _attr(v) -> str:
    return html.escape(str(v), quote=True)


def build_svg(
    lw: pd.DataFrame,
    focus_codes: set,
    max_labels: int = 15,
    cat_full: str = "",
    badges: dict | None = None,
    merges: list | None = None,
) -> str:
    """Relieve isométrico de las hojas de UNA categoría (SVG).

    lw: hojas con columnas code, node_path, leaf_name, total, pct.
    merges: [(["L24", "L26"], "motivo"), ...] hojas hermanas fusionadas en una unidad."""
    lw = lw.copy()
    lw["total"] = pd.to_numeric(lw.total, errors="coerce").fillna(0)
    lw["pct"] = pd.to_numeric(lw.pct, errors="coerce").fillna(0)
    cat = " > ".join(lw.node_path.iloc[0].split(" > ")[:2])
    lw["branch"] = lw.node_path.apply(
        lambda p: p.split(" > ")[2] if len(p.split(" > ")) > 3 else "Other (directly in the category)"
    )
    br = lw.groupby("branch").total.sum().sort_values(ascending=False)
    color = {b: PALETTE[i % len(PALETTE)] for i, b in enumerate(br.index)}
    lw["border"] = lw.branch.map({b: i for i, b in enumerate(br.index)})
    lw = lw.sort_values(["border", "total"], ascending=[True, False]).reset_index(drop=True)

    n = len(lw)
    nx = max(4, math.ceil(math.sqrt(n * 1.5)))
    ny = max(3, math.ceil(n / nx))
    cells = sorted(((x, y) for x in range(nx) for y in range(ny)), key=lambda c: (-(c[0] + c[1]), c[0]))[:n]
    lw["x"], lw["y"] = [c[0] for c in cells], [c[1] for c in cells]

    vmax = lw.total.max() or 1
    hmax = max(2.2, min(nx, ny) * 0.55)
    top = lw[lw.total > 0].sort_values("total", ascending=False).head(max_labels)
    num = {c: i + 1 for i, c in enumerate(top.code)}
    lname = dict(zip(lw.code, lw.leaf_name, strict=False))
    mate = {}
    for codes, why in merges or []:
        for c in codes:
            mate[c] = ([lname.get(o, o) for o in codes if o != c], why)

    def P(x, y, z=0.0):
        return ((x - y) * C30, (x + y) * S30 + z)

    xs = [P(0, ny)[0], P(nx, 0)[0]]
    y_lo, y_hi = -0.3, P(nx, ny, hmax)[1] + 0.3
    W = 760
    sc = W / (xs[1] - xs[0] + 0.6)
    H = (y_hi - y_lo) * sc

    def T(pt):
        return f"{(pt[0] - xs[0] + 0.3) * sc:.1f},{(y_hi - pt[1]) * sc:.1f}"

    out, late, tc = [], [], {}
    for x in range(nx):
        for y in range(ny):
            pts = " ".join(T(P(*c)) for c in ((x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)))
            out.append(f"<polygon points='{pts}' fill='#16300E' stroke='#0A1B04' stroke-width='1'/>")
    g = 0.09
    for r in lw.assign(d=lw.x + lw.y).sort_values(["d", "x"], ascending=[False, True]).itertuples():
        tint, strong = color[r.branch]
        foc = r.code in focus_codes
        z = max(hmax * r.total / vmax, 0.02)
        x0, y0, x1, y1 = r.x + g, r.y + g, r.x + 1 - g, r.y + 1 - g
        A, B, C, D = (x0, y0), (x1, y0), (x1, y1), (x0, y1)
        op = "1" if foc else "0.55"
        A_ = _attr
        data = (
            f"data-code='{A_(r.code)}' data-name='{A_(r.leaf_name)}' data-branch='{A_(r.branch)}' "
            f"data-vol='{_fmt(r.total)}' data-pct='{r.pct:.1f}' data-n='{int(getattr(r, 'n_kw', 0) or 0)}' "
            f"data-focus='{1 if foc else 0}'"
        )
        if r.code in mate:
            data += f" data-merged='{A_(' + '.join(mate[r.code][0]))}'"
        tc[r.code] = T(P(r.x + 0.5, r.y + 0.5, z))

        def face(pts, fill, T=T):
            return (
                f"<polygon points='{' '.join(T(p) for p in pts)}' fill='{fill}' "
                "stroke='#0A1B04' stroke-width='0.5' stroke-opacity='0.6'/>"
            )

        out.append(
            f"<g opacity='{op}' class='bar' {data}>"
            + face([P(*A), P(*D), P(*D, z), P(*A, z)], _shade(strong, 0.82))
            + face([P(*A), P(*B), P(*B, z), P(*A, z)], _shade(strong, 0.68))
            + face([P(*A, z), P(*B, z), P(*C, z), P(*D, z)], tint)
        )
        if r.code in num:
            cx, cy = T(P(r.x + 0.5, r.y + 0.5, z)).split(",")
            out.append(
                f"<text x='{cx}' y='{float(cy) + 3.5:.1f}' text-anchor='middle' font-size='{max(8, min(12, sc * 0.35)):.0f}' "
                f"font-weight='700' fill='#0A1B04'>{num[r.code]}</text>"
            )
        if badges and badges.get(r.code):
            bx, by = T(P(r.x + 0.5, r.y + 0.5, z)).split(",")
            bx, by = float(bx) + sc * 0.42, float(by) - sc * 0.42
            late.append(
                f"<g class='qbadge'><circle cx='{bx:.1f}' cy='{by:.1f}' r='9' fill='#A1E335' stroke='#0A1B04' stroke-width='1.5'/>"
                f"<text x='{bx:.1f}' y='{by:.1f}' dominant-baseline='central' text-anchor='middle' font-size='10.5' font-weight='700' fill='#0A1B04'>"
                f"{badges[r.code]}</text></g>"
            )
        out.append("</g>")
    links = []
    for codes, why in merges or []:
        pts = [tc[c] for c in codes if c in tc]
        if len(pts) < 2:
            continue
        xy = [tuple(map(float, p.split(","))) for p in pts]
        mx, my = sum(p[0] for p in xy) / len(xy), sum(p[1] for p in xy) / len(xy) - 16
        tip = html.escape(" + ".join(lname.get(c, c) for c in codes) + f" · merged siblings: {why}")
        links.append(
            f"<g class='mlink'><title>{tip}</title><polyline points='{' '.join(pts)}' fill='none' stroke='#FFFFFF' "
            f"stroke-width='2' stroke-dasharray='5 4'/>"
            + "".join(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4' fill='#FFFFFF' stroke='#0A1B04'/>" for x, y in xy)
            + f"<rect x='{mx - 30:.1f}' y='{my - 9:.1f}' width='60' height='18' rx='9' fill='#FFFFFF'/>"
            f"<text x='{mx:.1f}' y='{my:.1f}' dominant-baseline='central' text-anchor='middle' font-size='10.5' "
            f"font-weight='700' fill='#0A1B04'>siblings</text></g>"
        )
    out += links + late
    svg = (
        f"<svg viewBox='0 0 {W} {H:.0f}' role='img' aria-label='Universe of {html.escape(cat)}' "
        f"style='width:100%;height:auto;font-family:inherit'>{''.join(out)}</svg>"
    )

    # leyenda: ramas y top hojas
    leg_b = "".join(
        f"<div class='lg'><i style='background:{color[b][0]};border-color:{color[b][1]}'></i>"
        f"<span>{html.escape(b)}</span><b>{_fmt(v)}</b></div>"
        for b, v in br.items()
        if v > 0
    )
    leg_t = "".join(
        f"<div class='lt'><em>{num[r.code]}</em><span>{html.escape(r.leaf_name)}"
        f"{'' if r.code in focus_codes else ' <small>outside focus</small>'}"
        f"{' <small class=sib>sibling of ' + html.escape(' + '.join(mate[r.code][0])) + '</small>' if r.code in mate else ''}"
        f"</span><b>{r.pct:.1f}%</b></div>"
        for r in top.itertuples()
    )
    leg_m = "".join(
        f"<div class='lm'><span>{html.escape(' + '.join(lname.get(c, c) for c in codes))}</span>"
        f"<small>{html.escape(why)}</small></div>"
        for codes, why in (merges or [])
    )
    if leg_m:
        leg_t += f"<h4>Merged siblings</h4>{leg_m}"
    nz = int((lw.total > 0).sum())
    n_foc = len(focus_codes & set(lw.code))
    all_in = n_foc >= nz  # sin foco del 80 % (desde oct-2026) cuentan todas las hojas con búsquedas
    stats = (
        f"{n} leaves · {nz} with keywords"
        + ("" if all_in else f" · {n_foc} in focus")
        + f" · {_fmt(lw.total.sum())} searches"
    )
    faded = "Faded: leaves without searches." if all_in else "Faded: outside the focus."
    return (
        f"<div class='uni' data-cat='{html.escape(cat_full or cat, quote=True)}'><div class='uni-chart'>{svg}"
        f"<p class='hint'>{stats}. Each cell is an Amazon leaf; bar height is its search volume. "
        f"{faded} Hover a bar to see the leaf, click it to see its keywords.</p></div>"
        f"<div class='uni-leg'><h4>Branches</h4>{leg_b}<h4>Top leaves by volume</h4>{leg_t}</div></div>"
    )


UNI_CSS = asset("universe.css")
