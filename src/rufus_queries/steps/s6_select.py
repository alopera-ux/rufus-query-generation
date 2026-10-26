"""Paso 6 · Seleccionar: peso de cada grupo, ranking de intents (G incluido) y elección de las preguntas.

Entrada : s5_groups/{needs,keywords}.csv · s4_focus/units.csv · s3_weight/classified_norm.csv · config.selection
Salida  : out/s6_select/
    groups_scored.csv   cada grupo: volumen, % en su unidad, peso (% de la categoría), ligado a marca
    intent_ranking.csv  por categoría: peso de cada intent (suma de sus grupos) y si se elige
    selection.csv       los grupos elegidos
    briefs.json         una ficha por pregunta: producto, necesidad, keywords, peso

Fórmulas (todo con search volume real):
- base de la unidad = volumen de keywords con intent (sin Brand Intent) + genéricas sin marca
- % del grupo en su unidad = volumen del grupo / base de la unidad
- peso del grupo = % de la unidad en la categoría × % del grupo en su unidad   (→ % de la categoría)
- peso del intent = suma de sus grupos; se eligen los selection.n_intents primeros (G compite como uno más)
- por intent: los N grupos con más peso (questions_per_intent, o questions_per_intent_override[intent]),
  con tope por unidad e intent y por unidad en el set, y solo grupos con al menos min_group_weight_pct
  (% de la categoría). Los intents elegidos toman sus grupos por turnos (uno cada vez, en orden de ranking):
  así el tope por unidad no deja sin preguntas a un intent elegido porque los anteriores agotaron sus hojas.
  Los huecos que queden se completan con el siguiente intent del ranking y, si aún faltan, ya sin topes por hoja:
  primero los grupos con más peso de los intents elegidos que no llegaron a su cuota, luego los de cualquier
  intent. No se repite una necesidad contenida en otra de la misma hoja e intent ("powder" / "powder packets").
  El set llega al total mientras haya grupos con al menos min_group_weight_pct
- grupo ligado a marca (se excluye): ≤ max_brand_families familias de marca y < min_nonbrand_share de volumen
  sin marca; no se aplica en brand_bound_skip_intents (Compatibility: se puede nombrar el dispositivo)
- unidades con menos de min_intent_keywords_per_leaf keywords en la base no aportan grupos (muy pocos datos)
"""

import re

import pandas as pd

from rufus_queries.lib.common import Config, Out, step_args
from rufus_queries.lib.intents import load_intents

STEP = "s6_select"
STOP = {"for", "with", "and", "the", "a", "an", "of", "in", "to"}


def toks(s):
    """Palabras de un texto en minúsculas, sin palabras vacías."""
    return [t for t in re.findall(r"[a-z0-9%]+", s.lower()) if t not in STOP]


def product_term(group_kw: pd.DataFrame, needs: list, unit_generic: pd.DataFrame, unit_name: str) -> tuple[str, str]:
    """Producto de la ficha: lo que queda de la keyword sin marca más buscada del grupo al quitar la necesidad,
    si es un tipo de producto de la unidad (una genérica de la unidad, o 2+ palabras contenidas en una); si no, la
    genérica que se llama como la hoja ("sports drinks" en Sports drinks) o, si no la hay, la genérica sin marca más
    buscada de la unidad (la más buscada puede ser un producto concreto: "pickle juice" en Sports drinks); si no hay
    genéricas, el nombre de la hoja."""

    def sing(ws):
        return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in ws}

    generic = unit_generic.sort_values("volume", ascending=False).keyword.tolist()
    named = [gk for gk in generic if sing(toks(gk)) == sing(toks(unit_name))]
    fallback = (named or generic or [unit_name.lower()])[0]
    nb = group_kw[group_kw.brand == ""].sort_values("volume", ascending=False)
    if nb.empty:
        return fallback, "unit_generic"

    def cut(text, ns):
        for x in sorted(ns, key=len, reverse=True):
            if x:
                text = re.sub(r"(?<![a-z0-9])" + re.escape(x) + r"(?![a-z0-9])", " ", text)
        return " ".join(toks(text))

    def valid(rest):
        # vale si es una genérica de la unidad, o si tiene 2+ palabras contenidas en una genérica ("coffee k cups");
        # una palabra suelta ("instant", "pods", "shake") es un trozo, no un producto
        rt = sing(rest.split())
        exact = any(rt == sing(toks(gk)) for gk in generic)
        return bool(rest) and (exact or (len(rt) >= 2 and any(rt <= sing(toks(gk)) for gk in generic)))

    top = nb.keyword.iloc[0]
    # primero quitando todas las necesidades; si no queda un producto, quitando cada una por separado
    # (una necesidad mal recortada, "bottles 24 pack", se comería parte del producto: "water bottles 24 pack")
    for rest in [cut(top, needs)] + [cut(top, [x]) for x in needs]:
        if valid(rest):
            return rest, "residual"
    return fallback, "unit_generic"


def near_dup(key: tuple, group: str, unit_id: str, picked: set) -> bool:
    """True si en la misma hoja e intent ya hay un grupo cuya necesidad contiene a esta o al revés
    ("powder" y "powder packets"): serían la misma pregunta."""
    mine = {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in toks(group)}
    for *k, g, u in picked:
        if tuple(k) == key and u == unit_id:
            other = {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in toks(g)}
            if mine and other and (mine <= other or other <= mine):
                return True
    return False


def take_groups(cand: pd.DataFrame, n_q: int, key: tuple, state: dict, caps: tuple) -> int:
    """Toma hasta n_q grupos de cand (ya ordenados por peso) respetando los topes por hoja.

    caps = (máx. por hoja en este intent, máx. por hoja en el set); state guarda used/picked/sel de la categoría
    y per_ui (grupos por hoja de cada intent, acumulado entre llamadas). Devuelve cuántos grupos ha tomado."""
    cap_ui, cap_u = caps
    used, picked, sel = state["used"], state["picked"], state["sel"]
    per_ui, k = state.setdefault("per_ui", {}), 0
    for r in cand.itertuples():
        if per_ui.get((key, r.unit_id), 0) >= cap_ui or used.get(r.unit_id, 0) >= cap_u:
            continue
        if (*key, r.group, r.unit_id) in picked or near_dup(key, r.group, r.unit_id, picked):
            continue
        picked.add((*key, r.group, r.unit_id))
        sel.append(r._asdict())
        per_ui[(key, r.unit_id)] = per_ui.get((key, r.unit_id), 0) + 1
        used[r.unit_id] = used.get(r.unit_id, 0) + 1
        k += 1
        if k == n_q:
            break
    return k


def fill_rest(el: pd.DataFrame, short: int, cat: str, state: dict, first=()) -> list:
    """Último relleno, sin topes por hoja: los grupos con más peso de el (ya ordenados) que no estén elegidos,
    antes los de los intents de first (los elegidos que no llegaron a su cuota). Devuelve sus intents."""
    picked, sel, used, added = state["picked"], state["sel"], state["used"], []
    el = pd.concat([el[el.block.isin(first)], el[~el.block.isin(first)]])
    for r in el.itertuples():
        if len(added) >= short:
            break
        if (cat, r.block, r.group, r.unit_id) in picked or near_dup((cat, r.block), r.group, r.unit_id, picked):
            continue
        picked.add((cat, r.block, r.group, r.unit_id))
        sel.append(r._asdict())
        used[r.unit_id] = used.get(r.unit_id, 0) + 1
        added.append(r.block)
    return added


def main(argv=None):
    """Paso 6: pesa los grupos, ordena los intents y elige las fichas de cada categoría."""
    args = step_args(__doc__.splitlines()[0], argv)
    cfg, out = Config.load(args.config), Out(args.out)
    sc = cfg.selection
    names = {i.code: i.name for i in load_intents(cfg.input_path("intents"))}
    names["G"] = "Generic (product type)"
    out.read_csv("s5_groups", "needs.csv")
    kws = out.read_csv("s5_groups", "keywords.csv")
    kws["volume"] = pd.to_numeric(kws.volume, errors="coerce").fillna(0)
    units = out.read_csv("s4_focus", "units.csv")
    unit_pct = {(r.category, r.unit_id): float(r.pct) for r in units.itertuples()}
    excl = set(sc.get("exclude_intents", ["I13"]))
    if not sc.get("generic_as_intent", True):
        excl.add("G")
    bb = sc.get("brand_bound", {})
    max_fam, min_nb = int(bb.get("max_brand_families", 1)), float(bb.get("min_nonbrand_share", 0.25))
    skip_bb = set(sc.get("brand_bound_skip_intents", ["I07"]))
    min_kw = int(sc.get("min_intent_keywords_per_leaf", 5))

    # ── peso de cada grupo ──────────────────────────────────────────────
    base = (
        kws.groupby(["category", "unit_id"]).agg(base_vol=("volume", "sum"), base_n=("keyword", "size")).reset_index()
    )
    keys = ["category", "unit_id", "unit_name", "block", "group"]
    kws["vol_nb"] = kws.volume.where(kws.brand.fillna("") == "", 0)  # volumen sin marca
    kws["bf"] = kws.brand_family.replace("", pd.NA)  # nunique ignora los vacíos
    top = (
        kws.sort_values("volume", ascending=False)
        .groupby(keys, sort=False)
        .head(6)
        .groupby(keys)
        .keyword.agg("; ".join)
        .rename("top_kw")
    )
    g = (
        kws.groupby(keys)
        .agg(
            volume=("volume", "sum"),
            n_kw=("keyword", "size"),
            vol_nobrand=("vol_nb", "sum"),
            n_brands=("bf", "nunique"),
        )
        .join(top)
        .reset_index()
        .merge(base, on=["category", "unit_id"])
    )
    g["unit_pct"] = [unit_pct.get((c, u), 0.0) for c, u in zip(g.category, g.unit_id, strict=False)]
    g["share_in_unit"] = g.volume / g.base_vol
    g["score"] = g.unit_pct * g.share_in_unit
    g["nonbrand_share"] = g.vol_nobrand / g.volume
    g["brand_bound"] = (g.n_brands <= max_fam) & (g.nonbrand_share < min_nb) & ~g.block.isin(skip_bb)
    g["few_keywords"] = g.base_n < min_kw
    g["eligible"] = ~g.brand_bound & ~g.few_keywords & ~g.block.isin(excl)
    g["intent_name"] = g.block.map(names)
    g = g.sort_values(["category", "score"], ascending=[True, False])
    out.write_csv(STEP, "groups_scored.csv", g)

    # ── ranking de intents y selección ──────────────────────────────────
    n_int = int(sc.get("n_intents", 5))
    per = int(sc.get("questions_per_intent", 5))
    over = sc.get("questions_per_intent_override", {}) or {}
    cap_ui, cap_u = int(sc.get("cap_per_leaf_per_intent", 2)), int(sc.get("cap_per_leaf_in_set", 4))
    min_w = float(sc.get("min_group_weight_pct", 0.1))
    ranks, sel, picked = [], [], set()
    for cat, gc in g.groupby("category"):
        el = gc[gc.eligible]
        ir = el.groupby("block").score.sum().sort_values(ascending=False)
        chosen = list(ir.index[:n_int])
        tot = ir.sum() or 1
        for i, (b, s) in enumerate(ir.items(), 1):
            ranks.append(
                {
                    "category": cat,
                    "rank": i,
                    "intent": b,
                    "intent_name": names.get(b, b),
                    "score": round(s, 4),
                    "share_pct": round(s / tot * 100, 1),
                    "chosen": b in chosen,
                }
            )
        el = el[el.score >= min_w]
        used, short = {}, 0

        state = {"used": used, "picked": picked, "sel": sel}

        quota = {b: int(over.get(b, per)) for b in chosen}
        got = dict.fromkeys(chosen, 0)
        while True:  # por turnos: cada intent elegido toma su siguiente mejor grupo
            took = 0
            for b in chosen:
                if got[b] < quota[b]:
                    k = take_groups(el[el.block == b], 1, (cat, b), state, (cap_ui, cap_u))
                    got[b] += k
                    took += k
            if not took:
                break
        for b in chosen:
            if got[b] < quota[b]:
                short += quota[b] - got[b]
                print(f"[{STEP}] {cat} · {b}: {got[b]} de {quota[b]} grupos (resto bajo {min_w}% o por topes)")
        # huecos que quedan → siguientes intents del ranking, para mantener el total de preguntas
        for b in [x for x in ir.index if x not in chosen]:
            if short <= 0:
                break
            k = take_groups(el[el.block == b], short, (cat, b), state, (cap_ui, cap_u))
            if k:
                print(f"[{STEP}] {cat} · {b}: +{k} grupos para completar el set")
                for x in ranks:
                    if x["category"] == cat and x["intent"] == b:
                        x["chosen"] = "filler"
            short -= k
        if short > 0:
            added = fill_rest(el, short, cat, state, first=[b for b in chosen if got[b] < quota[b]])
            if added:
                print(f"[{STEP}] {cat}: +{len(added)} grupos con más peso sin topes por hoja para completar el set")
            for x in ranks:
                if x["category"] == cat and x["intent"] in added and x["chosen"] is False:
                    x["chosen"] = "filler"
    ranks = pd.DataFrame(ranks)
    sel = pd.DataFrame(sel).drop(columns=["Index"], errors="ignore")
    out.write_csv(STEP, "intent_ranking.csv", ranks)
    out.write_csv(STEP, "selection.csv", sel)

    # ── fichas ──────────────────────────────────────────────────────────
    briefs = []
    # índices precalculados: filtrar 200k filas por cada ficha era lo más lento del paso
    by_group = kws.groupby(["category", "unit_id", "block", "group"]).indices
    by_unit_g = kws[kws.block == "G"].groupby(["category", "unit_id"]).indices
    kg = kws[kws.block == "G"]
    empty = kws.iloc[0:0]
    for (cat, b), s in sel.groupby(["category", "block"], sort=False):
        for i, r in enumerate(s.itertuples(), 1):
            ix = by_group.get((cat, r.unit_id, b, r.group))
            gk = kws.iloc[ix] if ix is not None else empty
            iu = by_unit_g.get((cat, r.unit_id))
            ug = kg.iloc[iu] if iu is not None else empty
            lits = gk.groupby("need_key").volume.sum().sort_values(ascending=False).index.tolist()
            if b == "G":
                product, src, need = lits[0], "generic", ""
            else:
                product, src = product_term(gk, lits, ug, r.unit_name)
                need = lits[0]
            briefs.append(
                {
                    "id": f"{b}-{i}",
                    "category": cat,
                    "intent": b,
                    "intent_name": names.get(b, b),
                    "unit": r.unit_name,
                    "group": r.group,
                    "product": product,
                    "product_source": src,
                    "need": need,
                    "literal_needs": lits[:8] if b != "G" else [],
                    "top_keywords": r.top_kw.split("; "),
                    "volume": int(r.volume),
                    "weight_pct_category": round(r.score, 3),
                }
            )
    out.write_json(STEP, "briefs.json", briefs)

    for cat, rc in ranks.groupby("category"):
        print(
            f"[{STEP}] {cat}: intents elegidos "
            + " · ".join(
                f"{r.intent} {r.intent_name} {r.share_pct}%" for r in rc[rc.chosen.isin([True, "filler"])].itertuples()
            )
        )
        bc = [b for b in briefs if b["category"] == cat]
        print(
            f"         {len(bc)} fichas · producto por regla: "
            f"{sum(b['product_source'] == 'residual' for b in bc)} del propio grupo, "
            f"{sum(b['product_source'] == 'unit_generic' for b in bc)} genérica de la unidad"
        )
        for b in bc:
            print(
                f"         {b['id']:<6} {b['unit'][:32]:<32} {b['product']:<28} {b['need']:<20} "
                f"{b['weight_pct_category']:.2f}%"
            )


if __name__ == "__main__":
    main()
