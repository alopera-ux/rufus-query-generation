"""Paso 7 · Generar: varias versiones de cada ficha (misma pregunta, otra estructura de la familia de su intent) (IA).

Entrada : s6_select/briefs.json · prompts/generate.md · config.generation
Salida  : out/s7_generate/candidates.json   [{id, category, candidates:[{s, text}]}]

generation.candidates_per_group versiones por ficha (6 por defecto); el paso 8 elige una. Las fichas de cada categoría
van en lotes de como mucho BATCH para que la respuesta no se corte. Con provider "claude"/"manual" el paso se para
hasta que se respondan los prompts de out/manual/pending.
"""

import json
import sys

from rufus_queries.lib.common import PROMPTS, Config, Out, chunks, step_args
from rufus_queries.lib.llm import LLM, parse_json

STEP = "s7_generate"
BATCH = 12  # fichas por llamada


def main(argv=None):
    """Paso 7: varias versiones por ficha."""
    args = step_args(__doc__.splitlines()[0], argv)
    cfg, out = Config.load(args.config), Out(args.out)
    gc = cfg.generation
    llm = LLM(cfg, out.base)
    briefs = out.read_json("s6_select", "briefs.json")
    system = (
        (PROMPTS / "generate.md")
        .read_text(encoding="utf-8")
        .replace("{min_words}", str(gc.get("min_words", 4)))
        .replace("{max_words}", str(gc.get("max_words", 12)))
        .replace("{n_candidates}", str(n_cand := int(gc.get("candidates_per_group", 6))))
    )
    keep = ["id", "intent", "intent_name", "product", "need", "literal_needs", "top_keywords"]
    cats = list(dict.fromkeys(b["category"] for b in briefs))
    by_cat = {cat: [{k: b[k] for k in keep} for b in briefs if b["category"] == cat] for cat in cats}
    jobs = [(cat, part) for cat in cats for part in chunks(by_cat[cat], BATCH)]
    llm.expect(len(jobs), f"Writing {n_cand} versions per brief")
    raws = llm.map(
        "generate",
        [(system, f"Category: {cat}\n\n" + json.dumps(part, ensure_ascii=False, indent=1)) for cat, part in jobs],
    )

    def collect(jobs, raws):
        res, miss = [], []
        for (cat, bc), raw in zip(jobs, raws, strict=False):
            data = parse_json(raw) if raw else None
            got = {d.get("id"): d.get("candidates", []) for d in (data or []) if isinstance(d, dict)}
            for b in bc:
                c = [x for x in got.get(b["id"], []) if isinstance(x, dict) and x.get("text")]
                if not c:
                    miss.append((cat, b))
                res.append({"id": b["id"], "category": cat, "candidates": c})
        return res, miss

    result, miss = collect(jobs, raws)
    if miss and not llm.pending:  # 2.ª vuelta: fichas sin versiones (respuesta rota o incompleta)
        print(f"[{STEP}] {len(miss)} fichas sin versiones: se vuelven a pedir")
        jobs2 = [
            (cat, part)
            for cat in dict.fromkeys(c for c, _ in miss)
            for part in chunks([b for c, b in miss if c == cat], BATCH)
        ]
        raws2 = llm.map(
            "generate",
            [(system, f"Category: {cat}\n\n" + json.dumps(part, ensure_ascii=False, indent=1)) for cat, part in jobs2],
        )
        again = {(r["category"], r["id"]): r for r in collect(jobs2, raws2)[0] if r["candidates"]}
        result = [again.get((r["category"], r["id"]), r) for r in result]
    missing = [(r["category"], r["id"]) for r in result if not r["candidates"]]
    if llm.pending:
        print(
            f"[{STEP}] modo {llm.provider}: {len(llm.pending)} prompts pendientes en {out.base / 'manual' / 'pending'}"
        )
        sys.exit(3)  # 3 = esperando respuestas (modo claude/manual)
    out.write_json(STEP, "candidates.json", result)
    n = sum(len(r["candidates"]) for r in result)
    print(
        f"[{STEP}] {len(result)} fichas · {n} candidatas"
        + (f" · {len(missing)} fichas sin candidatas: {missing[:5]}" if missing else "")
    )
    llm.report(STEP)


if __name__ == "__main__":
    main()
