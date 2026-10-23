"""Ejecuta el pipeline paso a paso.

  uv run rufus-run              # todos los pasos
  uv run rufus-run --from 3     # desde el paso 3
  uv run rufus-run --only 1     # solo el paso 1
  uv run rufus-run --config mi_config.json --out out/prueba

Modo "claude" (sin Bedrock): un paso con IA escribe sus prompts en <out>/manual/pending y para (código 3).
Pide a Claude que los responda (escribe <out>/manual/responses/<nombre>.txt) y relanza con --from <paso>.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from rufus_queries.lib.common import ROOT

STEPS = [
    "s1_universe",
    "s2_classify",
    "s3_weight",
    "s4_focus",
    "s5_groups",
    "s6_select",
    "s7_generate",
    "s8_check_judge",
    "s9_output",
]


def status(out, **kw):
    """Escribe <out>/_status.json con el estado de la ejecución (lo lee la app)."""
    p = Path(out) / "_status.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({**kw, "time": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False), encoding="utf-8")


def main(argv=None):
    """Ejecuta los pasos pedidos (--from/--to/--only), cada uno en su propio proceso."""
    ap = argparse.ArgumentParser(description="Ejecuta los pasos del pipeline.")
    ap.add_argument("--from", dest="start", type=int, default=1)
    ap.add_argument("--to", dest="end", type=int, default=len(STEPS))
    ap.add_argument("--only", type=int)
    ap.add_argument("--config")
    ap.add_argument("--out", default=str(ROOT / "out"))
    a = ap.parse_args(argv)
    lo, hi = (a.only, a.only) if a.only else (a.start, a.end)
    extra = ["--out", a.out] + (["--config", a.config] if a.config else [])
    for i in range(lo, hi + 1):
        name = STEPS[i - 1]
        print(f"\n=== {i}. {name} ===", flush=True)
        (Path(a.out) / "_progress.json").unlink(missing_ok=True)
        status(a.out, step=i, name=name, state="running", step_started=time.time())
        t0 = time.time()
        r = subprocess.run([sys.executable, "-m", f"rufus_queries.steps.{name}", *extra], check=False)
        print(f"--- {i}. {name}: {time.time() - t0:,.1f}s", flush=True)
        if r.returncode == 3:
            pend = len(list((Path(a.out) / "manual" / "pending").glob("*.md")))
            status(a.out, step=i, name=name, state="waiting", pending=pend)
            print(
                f"\nPENDIENTE: {pend} prompts en {Path(a.out) / 'manual' / 'pending'}. "
                f"Cuando estén respondidos: uv run rufus-run --from {i}"
                + (f" --config {a.config}" if a.config else ""),
                flush=True,
            )
            sys.exit(3)
        if r.returncode != 0:
            status(a.out, step=i, name=name, state="error")
            sys.exit(f"Paso {i} ({name}) terminó con error.")
    status(a.out, step=hi, name=STEPS[hi - 1], state="done")
    print("\nHecho.", flush=True)


if __name__ == "__main__":
    main()
