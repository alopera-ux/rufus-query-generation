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
- por intent: los N grupos con más peso (questions_per_intent, o questions_per_intent_override[intent]) entre
  todas las hojas de la categoría (min_group_weight_pct, por defecto 0, deja fuera grupos por debajo de ese % de la
  categoría; con 0,1 las categorías grandes y repartidas como Kitchen se quedaban sin 5 grupos por intent). Los intents
  elegidos toman sus grupos por turnos (uno cada vez, en orden de ranking). Sin topes por hoja: cuántas preguntas
  salen de cada hoja lo decide el peso de sus grupos (en las pruebas, los topes fijos cambiaban grupos grandes por
  otros 10–35 veces más pequeños). Los topes siguen disponibles como opción (cap_per_leaf_per_intent,
  cap_per_leaf_in_set); si se activan y un intent elegido no llega a su cuota por ellos, la completa sin topes
  antes que dar el hueco a otro intent.
  Si los intents elegidos no tienen grupos suficientes, los huecos van a los siguientes intents del ranking.
  Ningún intent pasa nunca de su cuota (questions_per_intent): si no hay grupos, el set se queda con menos.
  No se repite una necesidad contenida en otra de la misma hoja e intent ("powder" / "powder packets").
- marca: una keyword sin marca que contiene una marca de la categoría ("airpods", "earpods") cuenta como de marca,
  igual que en el control de marcas del paso 8; así no se eligen fichas cuya pregunta tendría que nombrar la marca
- grupo ligado a marca (se excluye): ≤ max_brand_families familias de marca y < min_nonbrand_share de volumen
  sin marca; no se aplica en brand_bound_skip_intents (Compatibility: se puede nombrar el dispositivo)
- grupos cuya necesidad es el nombre de otro nodo de la categoría ("dry" en Wet > Canned) no se preguntan: la
  pregunta mezclaría dos tipos de producto ("dry canned cat food")
- grupos cuya keyword principal no está escrita en alfabeto latino ("耳机") no se preguntan (sí cuentan para el
  peso): la pregunta se escribe en inglés
- unidades con menos de min_intent_keywords_per_leaf keywords en la base no aportan grupos (muy pocos datos)
"""

import json
import re
import sys

import pandas as pd

from rufus_queries.lib.common import PROMPTS, Config, Out, step_args
from rufus_queries.lib.intents import load_intents
from rufus_queries.lib.llm import LLM, parse_json
from rufus_queries.steps.s2_classify import name_key

STEP = "s6_select"
STOP = {"for", "with", "and", "the", "a", "an", "of", "in", "to"}


def need_names_other_node(g: pd.DataFrame, nm: pd.DataFrame) -> pd.Series:
    """True si la necesidad del grupo es el nombre de OTRO nodo de la categoría (no el suyo ni una rama que lo
    contiene): "dry" en Wet > Canned. Mezcla dos tipos de producto en una pregunta ("dry canned cat food")."""
    leaf_path = {(r.category, r.code): r.node_path for r in nm.itertuples()}
    names = {}
    for r in nm.itertuples():
        names.setdefault((r.category, name_key(r.node_path.split(" > ")[-1])), []).append(r.node_path)

    def other(r):
        if r.block == "G" or not r.group:
            return False
        own = leaf_path.get((r.category, r.unit_id), "")
        paths = names.get((r.category, name_key(r.group)), [])
        return any(not (own == p or own.startswith(p + " > ")) for p in paths)

    return pd.Series([other(r) for r in g.itertuples()], index=g.index, dtype=bool)


def is_latin(text: str) -> bool:
    """True si el texto solo usa alfabeto latino (con acentos), números y signos: 'café' sí, '耳机' no."""
    return all(c.isascii() or ("\u00c0" <= c <= "\u024f") for c in str(text))


def toks(s):
    """Palabras de un texto en minúsculas, sin palabras vacías."""
    return [t for t in re.findall(r"[a-z0-9%]+", s.lower()) if t not in STOP]


def product_term(group_kw: pd.DataFrame, needs: list, unit_generic: pd.DataFrame, unit_name: str) -> tuple[str, str]:
    """Producto de la ficha: lo que queda de la keyword sin marca más buscada del grupo al quitar la necesidad,
    si es un tipo de producto de la unidad (una genérica de la unidad, o 2+ palabras contenidas en una). Si no,
    entre la genérica que se llama como la hoja ("sports drinks"; la más buscada puede ser un producto concreto,
    "pickle juice") y la genérica más buscada (sin las que ya llevan la necesidad: "gaming pc" para "gaming"), la
    que comparte más palabras con las keywords del grupo (sin marcas
    ni necesidad); empate → la que se llama como la hoja. Así una hoja "Canned" no hace de "canned" el producto de
    "kitten wet food" (→ "wet cat food"), y en Serums sigue siendo "serum". Sin genéricas, el nombre de la hoja.
    Si al quitarle las palabras que nadie del grupo busca queda otra genérica de la unidad, se usa esa."""

    def sing(ws):
        return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in ws}

    generic = unit_generic.sort_values("volume", ascending=False).keyword.tolist()
    # una genérica que junta dos tipos de producto ("disinfectant sprays & liquids") es la etiqueta de la hoja
    generic = [gk for gk in generic if not re.search(r"&| and ", gk)] or generic
    named = [gk for gk in generic if sing(toks(gk)) == sing(toks(unit_name))]

    def cut(text, ns):
        for x in sorted(ns, key=len, reverse=True):
            if x:
                text = re.sub(r"(?<![a-z0-9])" + re.escape(x) + r"(?![a-z0-9])", " ", text)
        return " ".join(toks(text))

    need_w = sing(toks(needs[0])) if needs else set()
    # una genérica que ya lleva la necesidad ("gaming pc" para "gaming") daría una pregunta redundante
    clean = [gk for gk in generic if not (need_w and need_w <= sing(toks(gk)))]
    named = [gk for gk in named if gk in clean] or ([] if clean else named)
    cands = list(dict.fromkeys([*named[:1], *(clean or generic)[:1]])) or [unit_name.lower()]
    words = set()
    for kw, br in zip(group_kw.keyword, group_kw.brand.fillna(""), strict=False):
        words |= sing(toks(cut(kw, [*needs, br])))
    fallback = max(cands, key=lambda gk: len(sing(toks(gk)) & words))  # empate → el primero (el de la hoja)
    # la genérica puede llevar palabras que nadie del grupo busca ("on ear headphones" para "wired headphones for
    # kids"): si quitándolas queda otra genérica de la unidad con el mismo núcleo ("headphones"), el producto es esa
    # ("drain opener" no pasa a "drain": pierde el núcleo)
    said = sing(t for kw in group_kw.keyword for t in toks(kw))
    fw = toks(fallback)
    kept = sing(w for w in fw if sing([w]) <= said)
    shorter = [gk for gk in generic if fw and sing(fw[-1:]) <= kept and sing(toks(gk)) == kept]
    if shorter and not sing(fw) <= said:
        fallback = " ".join(re.findall(r"[a-z0-9%]+", shorter[0].lower()))
    nb = group_kw[group_kw.brand == ""].sort_values("volume", ascending=False)
    if nb.empty:
        return fallback, "unit_generic"

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
    ("powder" y "powder packets"), o si en la misma hoja, en cualquier intent, ya está la misma necesidad escrita
    de otra forma ("anti theft" / "anti-theft"): serían la misma pregunta."""
    mine = {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in toks(group)}
    flat = re.sub(r"[^a-z0-9]", "", group.lower())
    for k0, _b, g, u in picked:
        if k0 == key[0] and u == unit_id and flat and re.sub(r"[^a-z0-9]", "", g.lower()) == flat:
            return True
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


def mark_brands(kws: pd.DataFrame, all_kw: pd.DataFrame | None = None) -> pd.DataFrame:
    """Keywords sin marca que contienen una marca de su categoría (como palabra) pasan a tenerla, con su familia:
    "airpods" cuando otra keyword de la categoría (all_kw: todas, no solo las del foco) trae la marca "airpods".
    Es el mismo criterio que el control de marcas del paso 8."""
    kws = kws.copy()
    kws["brand"] = kws.brand.fillna("").astype(str)
    kws["brand_family"] = kws.brand_family.fillna("").astype(str)
    src = kws if all_kw is None else all_kw.assign(brand=all_kw.brand.fillna("").astype(str))
    for cat, g in kws.groupby("category"):
        a = src[(src.category == cat) & (src.brand != "")]
        fam = a.groupby("brand").brand_family.first().fillna("").to_dict() if "brand_family" in a else {}
        fam |= {b: fam.get(b, "") for b in a.brand.unique()}
        brands = sorted((b for b in fam if b != "amazon"), key=len, reverse=True)
        if not brands:
            continue
        rx = re.compile(r"(?<![a-z0-9])(" + "|".join(map(re.escape, brands)) + r")(?![a-z0-9])")
        for i in g.index[g.brand == ""]:
            m = rx.search(kws.at[i, "keyword"])
            if m:
                kws.at[i, "brand"] = m.group(1)
                kws.at[i, "brand_family"] = fam[m.group(1)] or m.group(1)
    return kws


def fill_rest(el: pd.DataFrame, short: int, cat: str, state: dict, room: dict) -> list:
    """Último relleno, sin topes por hoja: los grupos con más peso de el (ya ordenados) que no estén elegidos, solo
    de los intents con hueco en room ({intent: preguntas que le faltan para su cuota}, en orden de preferencia:
    primero se agota el hueco del primero). Ningún intent pasa de su cuota. Devuelve los intents añadidos."""
    picked, sel, used, added = state["picked"], state["sel"], state["used"], []
    el = pd.concat([el[el.block == b] for b in room] or [el.iloc[0:0]])
    for r in el.itertuples():
        if len(added) >= short:
            break
        if room.get(r.block, 0) <= 0:
            continue
        if (cat, r.block, r.group, r.unit_id) in picked or near_dup((cat, r.block), r.group, r.unit_id, picked):
            continue
        room[r.block] -= 1
        picked.add((cat, r.block, r.group, r.unit_id))
        sel.append(r._asdict())
        used[r.unit_id] = used.get(r.unit_id, 0) + 1
        added.append(r.block)
    return added


MAX_REVIEW_ROUNDS = 4  # vueltas de revisión: cada una solo revisa las fichas nuevas (límite técnico, no de calidad)
REVIEW_COLS = ["category", "id", "intent", "unit", "group", "product", "need", "weight_pct_category"]


def brief_key(b: dict) -> tuple:
    """Identidad de una ficha entre vueltas (el id cambia si se sustituye otra)."""
    return (b["category"], b["unit_id"], b["intent"], b["group"])


def select_groups(g: pd.DataFrame, names: dict, prm: dict, rejected: set, verbose: bool = True):
    """Ranking de intents y elección de grupos por categoría. rejected = grupos que la revisión con IA descartó: no
    cuentan como candidatos, pero sí para el ranking de intents (su demanda es real)."""
    ranks, sel, picked = [], [], set()
    n_int, per, over, caps, min_w = prm["n_int"], prm["per"], prm["over"], prm["caps"], prm["min_w"]
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
        el = el[[(cat, u, b, gr) not in rejected for u, b, gr in zip(el.unit_id, el.block, el.group, strict=True)]]
        used, short = {}, 0
        state = {"used": used, "picked": picked, "sel": sel}
        quota = {b: int(over.get(b, per)) for b in chosen}
        got = dict.fromkeys(chosen, 0)
        while True:  # por turnos: cada intent elegido toma su siguiente mejor grupo
            took = 0
            for b in chosen:
                if got[b] < quota[b]:
                    k = take_groups(el[el.block == b], 1, (cat, b), state, caps)
                    got[b] += k
                    took += k
            if not took:
                break
        # un intent elegido que no llegó a su cuota por los topes por hoja la completa antes de dar el hueco a otro
        room = {b: quota[b] - got[b] for b in chosen if got[b] < quota[b]}
        if room:
            for b in fill_rest(el, sum(room.values()), cat, state, room):
                got[b] += 1
        for b in chosen:
            if got[b] < quota[b]:
                short += quota[b] - got[b]
                if verbose:
                    print(f"[{STEP}] {cat} · {b}: {got[b]} de {quota[b]} grupos (resto bajo {min_w}%)")
        # huecos que quedan → siguientes intents del ranking, para mantener el total de preguntas
        fillers = [x for x in ir.index if x not in chosen]
        for b in fillers:
            if short <= 0:
                break
            k = take_groups(el[el.block == b], min(short, int(over.get(b, per))), (cat, b), state, caps)
            got[b] = k
            if k:
                for x in ranks:
                    if x["category"] == cat and x["intent"] == b:
                        x["chosen"] = "filler"
            short -= k
        if short > 0:
            room = {b: int(over.get(b, per)) - got.get(b, 0) for b in fillers}
            added = fill_rest(el, short, cat, state, room)
            for x in ranks:
                if x["category"] == cat and x["intent"] in added and x["chosen"] is False:
                    x["chosen"] = "filler"
    return pd.DataFrame(ranks), pd.DataFrame(sel).drop(columns=["Index"], errors="ignore")


def make_briefs(sel: pd.DataFrame, kws: pd.DataFrame, names: dict) -> list:
    """Una ficha por grupo elegido: producto, necesidad, keywords y peso."""
    briefs = []
    if sel.empty:
        return briefs
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
                    "unit_id": r.unit_id,
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
    return briefs


def valid_fix(fix, brief: dict):
    """Corrección propuesta por el revisor, solo si sale de las keywords reales de la ficha: cada palabra del producto
    y de la necesidad tiene que estar en sus top_keywords (no se inventa nada), y la necesidad tiene que ser la
    misma o una de sus variantes (si no, la ficha cambiaría de intent). En G la necesidad queda vacía."""
    if not isinstance(fix, dict):
        return None
    product = re.sub(r"\s+", " ", str(fix.get("product") or "")).strip().lower()
    need = "" if brief["intent"] == "G" else re.sub(r"\s+", " ", str(fix.get("need") or "")).strip().lower()

    def sing(ws):
        return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in ws}

    have = sing(t for kw in brief.get("top_keywords", []) for t in toks(kw))
    if not product or not sing(toks(product)) <= have or (need and not sing(toks(need)) <= have):
        return None
    if brief["intent"] != "G" and not need:
        return None  # sin necesidad dejaría de ser una ficha de su intent
    own = sing(t for x in [brief.get("need", ""), *brief.get("literal_needs", [])] for t in toks(x))
    if need and not sing(toks(need)) <= own:
        return None  # la necesidad es la que pone a la ficha en su intent: se corrige el producto, no la necesidad
    return {"product": product, "need": need}


def fix_duplicates(fix: dict, brief: dict, briefs: list) -> bool:
    """¿La corrección deja la ficha igual que otra del mismo intent y categoría?"""
    return any(
        x is not brief
        and x["category"] == brief["category"]
        and x["intent"] == brief["intent"]
        and (x["product"].lower(), x["need"].lower()) == (fix["product"], fix["need"])
        for x in briefs
    )


def apply_fixes(briefs: list, fixes: dict) -> list:
    """Aplica a las fichas las correcciones aceptadas en la revisión (producto y necesidad)."""
    for b in briefs:
        fix = fixes.get(brief_key(b))
        if fix:
            if fix["need"] and fix["need"] != b["need"]:
                b["literal_needs"] = [fix["need"], *[x for x in b["literal_needs"] if x != fix["need"]]]
            b |= {"product": fix["product"], "need": fix["need"], "product_source": "review_fix"}
    return briefs


def review_briefs(llm, system: str, briefs: list, context: list | None = None) -> dict:
    """Revisión con IA: {(categoría, id): (ok, motivo, corrección o None)}. Una llamada por categoría.
    Fichas por revisar: en las vueltas 2+ solo las nuevas,
    junto con las ya aceptadas como contexto (para que pueda
    ver duplicados), marcadas con "already_accepted"."""
    cats = list(dict.fromkeys(b["category"] for b in briefs))
    keep = ["id", "unit", "intent_name", "product", "need", "top_keywords"]

    def item(b, accepted=False):
        it = {k: b[k] for k in keep} | {"weight": b["weight_pct_category"]}
        if accepted:
            return it | {"already_accepted": True}
        # palabras del producto que no aparece en ninguna keyword: pista de que el producto puede no ser el que buscan
        said = {t.rstrip("s") for kw in b["top_keywords"] for t in toks(kw)}
        unsaid = [w for w in toks(b["product"]) if w.rstrip("s") not in said]
        return it | {"product_words_not_searched": unsaid} if unsaid else it

    jobs = [
        (
            cat,
            [item(b) for b in briefs if b["category"] == cat]
            + [item(b, True) for b in context or [] if b["category"] == cat],
        )
        for cat in cats
    ]
    llm.expect(len(jobs), "AI review of the briefs")
    raws = llm.map(
        "review",
        [(system, f"Category: {cat}\n\n" + json.dumps(items, ensure_ascii=False, indent=1)) for cat, items in jobs],
    )
    out = {}
    for (cat, _), raw in zip(jobs, raws, strict=False):
        for it in parse_json(raw) or []:
            if isinstance(it, dict) and "id" in it:
                ok = it.get("ok", True) is not False and str(it.get("ok", True)).lower() != "false"
                fix = it.get("fix") if isinstance(it.get("fix"), dict) else None
                out[(cat, it["id"])] = (ok, str(it.get("reason", "")).strip(), fix)
    return out


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
    kws = mark_brands(kws, out.read_csv("s3_weight", "classified_norm.csv"))
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
    g["foreign_script"] = ~g.top_kw.fillna("").str.split("; ").str[0].map(is_latin)
    g["need_is_other_node"] = need_names_other_node(g, out.read_csv("s2_classify", "nodes_map.csv"))
    g["eligible"] = ~g.brand_bound & ~g.few_keywords & ~g.block.isin(excl) & ~g.foreign_script & ~g.need_is_other_node
    g["intent_name"] = g.block.map(names)
    g = g.sort_values(["category", "score"], ascending=[True, False])
    out.write_csv(STEP, "groups_scored.csv", g)

    # ── ranking, selección, fichas y revisión con IA ────────────────────
    params = {
        "n_int": int(sc.get("n_intents", 5)),
        "per": int(sc.get("questions_per_intent", 5)),
        "over": sc.get("questions_per_intent_override", {}) or {},
        "caps": tuple(
            float("inf") if sc.get(k) is None else int(sc[k])
            for k in ("cap_per_leaf_per_intent", "cap_per_leaf_in_set")
        ),
        "min_w": float(sc.get("min_group_weight_pct") or 0.0),
    }
    llm = LLM(cfg, out.base)
    review_on = bool(sc.get("review", True))
    system = (PROMPTS / "review.md").read_text(encoding="utf-8")
    rejected, reviews, fixes = set(), {}, {}
    for rnd in range(MAX_REVIEW_ROUNDS):
        ranks, sel = select_groups(g, names, params, rejected, verbose=rnd == 0)
        briefs = apply_fixes(make_briefs(sel, kws, names), fixes)
        todo = [b for b in briefs if brief_key(b) not in reviews] if review_on else []
        if not todo:
            break
        accepted = [b for b in briefs if brief_key(b) in reviews]  # contexto: ya aceptadas en vueltas anteriores
        verdicts = review_briefs(llm, system, todo, accepted)
        if llm.pending:
            print(
                f"[{STEP}] modo {llm.provider}: {len(llm.pending)} prompts pendientes en "
                f"{out.base / 'manual' / 'pending'}"
            )
            sys.exit(3)  # 3 = esperando respuestas (modo claude/manual)
        n_fix = 0
        for b in todo:
            ok, reason, fix = verdicts.get((b["category"], b["id"]), (True, "sin respuesta del revisor", None))
            row = {**{k: b[k] for k in REVIEW_COLS}, "ok": ok, "reason": reason, "round": rnd + 1, "fix": ""}
            fix = valid_fix(fix, b) if not ok else None
            if fix and fix_duplicates(fix, b, briefs):
                fix = None  # la corrección duplicaría otra ficha: mejor sustituir por el siguiente grupo
            if fix:  # la demanda es real y el revisor dice qué buscan: se corrige la ficha en vez de descartarla
                fixes[brief_key(b)] = fix
                row |= {"ok": True, "fix": f"{fix['product']} | {fix['need']}", "reason": f"corregida: {reason}"}
                n_fix += 1
            reviews[brief_key(b)] = row
        new = {brief_key(b) for b in todo if not reviews[brief_key(b)]["ok"]}
        if new or n_fix:
            print(
                f"[{STEP}] revisión {rnd + 1}: {n_fix} fichas corregidas · {len(new)} rechazadas "
                "(se sustituyen por el siguiente grupo)"
            )
        if not new:
            briefs = apply_fixes(briefs, fixes)
            break
        rejected |= new
    else:  # se acabaron las vueltas con rechazos nuevos: el último set sale sin revisar sus sustitutas
        ranks, sel = select_groups(g, names, params, rejected, verbose=False)
        briefs = apply_fixes(make_briefs(sel, kws, names), fixes)
    out.write_csv(STEP, "intent_ranking.csv", ranks)
    out.write_csv(STEP, "selection.csv", sel)
    out.write_json(STEP, "briefs.json", briefs)
    out.write_csv(
        STEP,
        "review.csv",
        pd.DataFrame(list(reviews.values()), columns=[*REVIEW_COLS, "ok", "reason", "fix", "round"]),
    )

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

    llm.report(STEP)


if __name__ == "__main__":
    main()
