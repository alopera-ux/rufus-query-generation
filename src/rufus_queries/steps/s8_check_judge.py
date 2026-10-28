"""Paso 8 · Controlar y juzgar: controles automáticos + juez a ciegas (IA) + elección final con variedad de estructuras.

Entrada : s7_generate/candidates.json · s6_select/briefs.json · s3_weight/classified_norm.csv · prompts/judge.md
Salida  : out/s8_check_judge/
    candidates_checked.csv  cada candidata: controles (ok / motivo) y notas del juez
    chosen.csv              la pregunta elegida por ficha

Controles: contiene producto (palabra principal) y necesidad (alguna forma literal), sin marcas de la categoría,
sin frases prohibidas, entre min_words y max_words, sin "it/this/these".
Juez: otra llamada, opciones barajadas con semilla fija; puntúa 5 criterios de 1 a 5.
Variedad: se mide sobre la plantilla de la pregunta (el texto sin las palabras del producto ni de la necesidad):
"What body wash should I buy?" y "What vitamin c serum should I buy?" son la misma plantilla "what should i buy",
aunque empiecen distinto. Antes se miraban las dos primeras palabras y el producto las hacía parecer distintas.
Elección: la mejor nota entre las que pasan los controles (si ninguna versión de una ficha los pasa, la mejor nota
entre las que solo fallan producto/necesidad literal, marcada en reason; con marca, frase prohibida, longitud o
referencia nunca); para que no salgan todas iguales, no se repite
el mismo comienzo (dos primeras palabras) más de generation.max_same_structure veces en la categoría si hay
otra candidata con nota a menos de generation.diversity_tolerance de la mejor.
"""

import json
import random
import re
import sys

import pandas as pd

from rufus_queries.lib.common import PROMPTS, Config, Out, chunks, step_args
from rufus_queries.lib.llm import LLM, parse_json

STEP = "s8_check_judge"
BATCH = 12  # fichas por llamada al juez (cada una con hasta 6 versiones)
STOP = {"a", "an", "the", "for", "of", "with", "and"}


def template(text: str, brief: dict) -> str:
    """Plantilla de una pregunta: sus palabras sin las del producto, la necesidad ni sus formas literales."""
    words = set()
    for t in [brief.get("product", ""), brief.get("need", ""), *brief.get("literal_needs", [])]:
        for w in re.findall(r"[a-z0-9%]+", str(t).lower()):
            words |= {w, w + "s", w + "es", w[:-1] if w.endswith("s") else w}
    return " ".join(w for w in re.findall(r"[a-z0-9%']+", text.lower()) if w not in words)


def word_in(w: str, text: str) -> bool:
    """¿Aparece w como palabra (o expresión) completa en text?"""
    return re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text) is not None


def brands_for(brands: list, brief: dict) -> list:
    """Marcas que no puede mencionar la pregunta de una ficha. Una marca que forma parte de su producto o necesidad se
    usa ahí como nombre común ("food thermos"): el paso 6 eligió ese término de keywords sin marca."""
    own = f"{brief['product']} {brief['need']}".lower()
    return [w for w in brands if not word_in(w, own)]


def fallback_pick(g: pd.DataFrame):
    """Versión de una ficha sin ninguna válida: la de mejor nota entre las que solo fallan el control literal de
    producto o necesidad (nunca marca, frase prohibida, longitud o referencia). None si no hay."""
    g = g[g.fails.map(lambda f: set(str(f).split(";")) <= {"product", "need"})]
    if g.empty:
        return None
    return g.assign(judge=g.judge.fillna(0)).sort_values(["judge", "words"], ascending=[False, True]).iloc[0]


def has(term, text):
    """True si todas las palabras de term (sin vacías, singular o plural) aparecen en text."""
    toks = [t for t in re.findall(r"[a-z0-9%]+", term.lower()) if t not in STOP]

    def tok_in(t):
        stem = t[:-1] if t.endswith("s") and len(t) > 3 else t
        return re.search(r"(?<![a-z0-9])" + re.escape(stem) + r"(s|es)?(?![a-z0-9])", text) is not None

    return bool(toks) and all(tok_in(t) for t in toks)


def main(argv=None):
    """Paso 8: controles, juez a ciegas y elección final con variedad."""
    args = step_args(__doc__.splitlines()[0], argv)
    cfg, out = Config.load(args.config), Out(args.out)
    gc = cfg.generation
    llm = LLM(cfg, out.base)
    briefs = {(b["category"], b["id"]): b for b in out.read_json("s6_select", "briefs.json")}
    cands = out.read_json("s7_generate", "candidates.json")
    kw = out.read_csv("s3_weight", "classified_norm.csv")
    banned = [p.lower() for p in gc.get("banned_phrases", [])]
    lo, hi = int(gc.get("min_words", 4)), int(gc.get("max_words", 12))

    # ── controles ───────────────────────────────────────────────────────
    rows = []
    # marcas de cada categoría, calculadas una vez (antes se recalculaban para cada ficha)
    brands_of = {
        cat: sorted({x for x in g.brand if x and x != "amazon"}, key=len, reverse=True)
        for cat, g in kw.groupby("category")
    }
    for c in cands:
        b = briefs[(c["category"], c["id"])]
        brands = brands_of.get(c["category"], [])
        head = b["product"].split()[-1] if b["product"] else ""
        needs = [b["need"], *b.get("literal_needs", [])] if b["need"] else []
        brands_b = brands_for(brands, b)
        for x in c["candidates"]:
            t = " " + x["text"].lower() + " "
            fails = []
            if head and not has(head, t):
                fails.append("product")
            if needs and not any(has(n, t) for n in needs if n):
                fails.append("need")
            if b["intent"] != "I07":
                hit = next((w for w in brands_b if word_in(w, t)), None)
                if hit:
                    fails.append(f"brand:{hit}")
            if any(p in t for p in banned) or re.search(r"\b20\d\d\b", t):
                fails.append("banned")
            n = len(x["text"].split())
            if not lo <= n <= hi:
                fails.append(f"len{n}")
            if re.search(r"\b(it|this|these)\b", t):
                fails.append("reference")
            rows.append(
                {
                    "category": c["category"],
                    "id": c["id"],
                    "intent": b["intent"],
                    "s": x.get("s", ""),
                    "text": x["text"],
                    "words": n,
                    "ok": not fails,
                    "fails": ";".join(fails),
                }
            )
    d = pd.DataFrame(rows)

    # ── juez a ciegas ───────────────────────────────────────────────────
    system = (PROMPTS / "judge.md").read_text(encoding="utf-8")
    rnd = random.Random(11)
    key, scores, item_of = {}, {}, {}
    jobs = []
    for cat, dc in d.groupby("category", sort=False):
        items = []
        for i, g in dc.groupby("id", sort=False):
            opts = g.text.tolist()
            rnd.shuffle(opts)
            key[(cat, i)] = {str(j + 1): t for j, t in enumerate(opts)}
            b = briefs[(cat, i)]
            item_of[(cat, i)] = {
                "id": i,
                "intent": b["intent_name"],
                "product": b["product"],
                "need": b["need"],
                "options": {str(j + 1): t for j, t in enumerate(opts)},
            }
            items.append(item_of[(cat, i)])
        jobs += [(cat, json.dumps(part, ensure_ascii=False, indent=1)) for part in chunks(items, BATCH)]
    llm.expect(len(jobs), "Blind judge scoring the versions")
    for rnd_ in range(2):  # 2.ª vuelta: solo las fichas que se quedaron sin nota (respuesta rota o incompleta)
        if rnd_:
            judged = {(c, i) for c, i, _ in scores}
            todo = [k for k in item_of if k not in judged]
            if not todo or llm.pending:
                break
            print(f"[{STEP}] {len(todo)} fichas sin nota del juez: se vuelven a pedir")
            jobs = [
                (cat, json.dumps(part, ensure_ascii=False, indent=1))
                for cat in dict.fromkeys(c for c, _ in todo)
                for part in chunks([item_of[k] for k in todo if k[0] == cat], BATCH)
            ]
        raws = llm.map("judge", [(system, us) for _, us in jobs])  # en paralelo con Bedrock
        for (cat, _), raw in zip(jobs, raws, strict=False):
            for it in parse_json(raw) or []:
                if not isinstance(it, dict):
                    continue
                for opt, sc in (it.get("scores") or {}).items():
                    t = key.get((cat, it.get("id")), {}).get(str(opt))
                    if t and isinstance(sc, dict) and sc:
                        scores[(cat, it["id"], t)] = (sum(sc.values()) / len(sc), it.get("reason", ""))
    if llm.pending:
        print(
            f"[{STEP}] modo {llm.provider}: {len(llm.pending)} prompts pendientes en {out.base / 'manual' / 'pending'}"
        )
        sys.exit(3)  # 3 = esperando respuestas (modo claude/manual)
    d["judge"] = [scores.get((c, i, t), (None, ""))[0] for c, i, t in zip(d.category, d.id, d.text, strict=False)]
    d["reason"] = [scores.get((c, i, t), (None, ""))[1] for c, i, t in zip(d.category, d.id, d.text, strict=False)]
    out.write_csv(STEP, "candidates_checked.csv", d)

    # ── elección con variedad de comienzos ─────────────────────────────
    max_same, tol = int(gc.get("max_same_structure", 2)), float(gc.get("diversity_tolerance", 0.5))
    chosen = []
    for cat, dc in d.groupby("category", sort=False):
        dc = dc[dc.ok].copy()
        dc["opening"] = [template(t, briefs[(cat, i)]) for t, i in zip(dc.text, dc.id, strict=True)]
        dc["shape"] = dc.intent + ":" + dc.s.astype(str)  # estructura de la familia de su intent
        dc["judge"] = dc.judge.fillna(0)
        count = {}
        # primero las fichas con menos alternativas buenas, para no quedarse sin opciones
        order = dc.groupby("id").judge.apply(lambda s: (s >= s.max() - tol).sum()).sort_values().index
        for i in order:
            g = dc[dc.id == i].sort_values(["judge", "words"], ascending=[False, True])
            best = g.judge.iloc[0]
            # variedad sobre el comienzo de la pregunta en todo el set (no por intent)
            pick = next(
                (r for r in g.itertuples() if r.judge >= best - tol and count.get(r.opening, 0) < max_same),
                next(g.itertuples()),
            )
            count[pick.opening] = count.get(pick.opening, 0) + 1
            chosen.append(
                {
                    "category": cat,
                    "id": i,
                    "question": pick.text,
                    "structure": pick.s,
                    "judge": round(pick.judge, 2),
                    "best_judge": round(best, 2),
                    "opening": pick.opening,
                    "n_versions": len(g),
                    "reason": pick.reason,
                }
            )
        lost = [i for i in d[d.category == cat].id.unique() if i not in set(dc.id)]
        for i in lost:  # ninguna versión pasa: la mejor que solo falla producto/necesidad literal
            g = d[(d.category == cat) & (d.id == i)]
            pick = fallback_pick(g)
            if pick is None:
                continue
            chosen.append(
                {
                    "category": cat,
                    "id": i,
                    "question": pick.text,
                    "structure": pick.s,
                    "judge": round(pick.judge, 2),
                    "best_judge": round(pick.judge, 2),
                    "opening": template(pick.text, briefs[(cat, i)]),
                    "n_versions": len(g),
                    "reason": f"[sin control: {pick.fails}] {pick.reason}",
                }
            )
        if lost:
            print(f"[{STEP}] ⚠️  {cat}: fichas sin versión que pase los controles: {lost} (se usa la mejor nota)")
    ch = pd.DataFrame(chosen)
    out.write_csv(STEP, "chosen.csv", ch)
    for cat, g in ch.groupby("category"):
        print(
            f"[{STEP}] {cat}: {len(g)} preguntas · controles {d[d.category == cat].ok.mean():.0%} ok · "
            f"nota media {g.judge.mean():.2f} · plantillas distintas {g.opening.nunique()}"
        )
    llm.report(STEP)


if __name__ == "__main__":
    main()
