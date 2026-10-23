"""Paso 7 · Generar: 3 candidatas por ficha, cada una con una estructura de la familia de su intent (IA).

Entrada : s6_select/briefs.json · prompts/generate.md · config.generation
Salida  : out/s7_generate/candidates.json   [{id, category, candidates:[{s, text}]}]

Una llamada por categoría (sus 25 fichas). Con provider "claude"/"manual" el paso se para hasta que se respondan
los prompts de out/manual/pending.
"""

import json
import sys

from rufus_queries.lib.common import PROMPTS, Config, Out, step_args
from rufus_queries.lib.llm import LLM, parse_json

STEP = "s7_generate"


def main(argv=None):
    """Paso 7: tres candidatas por ficha."""
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
    )
    keep = ["id", "intent", "intent_name", "product", "need", "literal_needs", "top_keywords"]
    result, missing = [], []
    llm.expect(len(dict.fromkeys(b["category"] for b in briefs)), "Writing 3 candidates per brief")
    cats = list(dict.fromkeys(b["category"] for b in briefs))
    by_cat = {cat: [{k: b[k] for k in keep} for b in briefs if b["category"] == cat] for cat in cats}
    raws = llm.map(
        "generate",
        [(system, f"Category: {cat}\n\n" + json.dumps(by_cat[cat], ensure_ascii=False, indent=1)) for cat in cats],
    )
    for cat, raw in zip(cats, raws, strict=False):
        bc = by_cat[cat]
        data = parse_json(raw) if raw else None
        got = {d.get("id"): d.get("candidates", []) for d in (data or []) if isinstance(d, dict)}
        for b in bc:
            c = [x for x in got.get(b["id"], []) if isinstance(x, dict) and x.get("text")]
            if not c:
                missing.append((cat, b["id"]))
            result.append({"id": b["id"], "category": cat, "candidates": c})
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
    print(f"[{STEP}] LLM: {llm.stats}")


if __name__ == "__main__":
    main()
