"""Control de calidad de un run: alertas por categoría para revisar solo lo que hace falta.

Todo sale de los ficheros del run (preguntas, fichas, unidades, ranking de intents, revisión y uso), sin umbrales
a ojo: las referencias son la cuota pedida (n_intents × questions_per_intent) y el reparto proporcional a la demanda.

  error   faltan preguntas: menos que la cuota, o fichas sin pregunta
  warning un intent elegido no llenó su cuota; una hoja con demanda para ≥1 pregunta se queda sin ninguna; una hoja
          con el doble de su parte proporcional y ≥2 preguntas de más; una pregunta con nota del juez < 4
  info    fichas corregidas o sustituidas por la revisión con IA
"""

import json
import math
from pathlib import Path

import pandas as pd

LEVELS = ["error", "warning", "info"]
SUMMARY_COLS = [
    "category",
    "status",
    "questions",
    "target",
    "intents_full",
    "judge_mean",
    "judge_min",
    "weight_pct",
    "fixed_by_ai",
    "replaced_by_ai",
    "gallagher",
]
ALERT_COLS = ["category", "level", "check", "detail"]


def _csv(p: Path) -> pd.DataFrame:
    return pd.read_csv(p, keep_default_na=False) if p.exists() else pd.DataFrame()


def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def targets(selection: dict) -> tuple[int, int, dict]:
    """(intents, preguntas por intent, ajustes por intent) de la configuración del run."""
    over = {k: int(v) for k, v in (selection.get("questions_per_intent_override") or {}).items()}
    return int(selection.get("n_intents", 5)), int(selection.get("questions_per_intent", 5)), over


def cost(out_base: Path) -> float:
    """Coste estimado en $ de las llamadas a la IA del run (usage.csv; 0 si no hay)."""
    u = _csv(Path(out_base) / "usage.csv")
    return float(_num(u.cost_usd).fillna(0).sum()) if "cost_usd" in u else 0.0


def review(out_base: Path) -> pd.DataFrame:
    """Fichas corregidas o sustituidas por la revisión con IA (vacío si no hubo revisión)."""
    rev = _csv(Path(out_base) / "s6_select" / "review.csv")
    if rev.empty:
        return rev
    fix = rev.get("fix", pd.Series("", index=rev.index)).astype(str).str.strip()
    ok = rev.ok.astype(str).str.lower() == "true"
    rev = rev.assign(
        action=["fixed" if f else ("accepted" if o else "replaced") for f, o in zip(fix, ok, strict=True)],
        reason=rev.reason.astype(str).str.replace(r"^corregida:\s*", "", regex=True),  # la acción ya lo dice
    )
    return rev[rev.action != "accepted"].reset_index(drop=True)


def report(out_base: Path, selection: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(resumen por categoría, alertas). Vacíos si el run no ha llegado al paso 9."""
    o = Path(out_base)
    fq = _csv(o / "s9_output" / "final_questions.csv")
    if fq.empty:
        return pd.DataFrame(columns=SUMMARY_COLS), pd.DataFrame(columns=ALERT_COLS)
    bp = o / "s6_select" / "briefs.json"
    briefs = pd.DataFrame(json.loads(bp.read_text(encoding="utf-8"))) if bp.exists() else pd.DataFrame()
    units = _csv(o / "s4_focus" / "units.csv")
    ranks = _csv(o / "s6_select" / "intent_ranking.csv")
    rev = review(o)
    n_int, per, over = targets(selection)
    rows, alerts = [], []

    def alert(cat, level, check, detail):
        alerts.append({"category": cat, "level": level, "check": check, "detail": detail})

    for cat, q in fq.groupby("category", sort=False):
        has_q = q.question.astype(str).str.strip() != ""
        chosen = (
            ranks[(ranks.category == cat) & (ranks.chosen.astype(str) == "True")].intent.tolist() if len(ranks) else []
        )
        # la cuota pedida, aunque la categoría tenga menos intents con demanda que n_intents
        target = sum(over.get(i, per) for i in chosen) + per * max(0, n_int - len(chosen))
        n_q = int(has_q.sum())
        if n_q < target:
            alert(cat, "error", "Fewer questions than the target", f"{n_q} of {target}")
        for r in q[~has_q].itertuples():
            alert(cat, "error", "Brief without a question", f"{r.id} {r.product} | {r.need}")
        by_int = q[has_q].groupby("intent").size()
        full = 0
        for i in chosen:
            got, want = int(by_int.get(i, 0)), over.get(i, per)
            full += got >= want
            if got < want:
                name = q[q.intent == i].intent_name.iloc[0] if (q.intent == i).any() else i
                alert(cat, "warning", "Intent below its quota", f"{name}: {got} of {want} (rest went to other intents)")
        judge = _num(q.judge)
        for r in q[judge < 4].itertuples():
            alert(cat, "warning", "Low judge score", f"{r.judge}: {r.question}")

        # reparto por hojas: preguntas de cada unidad frente a su parte proporcional de la demanda
        gal = float("nan")
        u = units[units.category == cat] if len(units) else units
        if len(u) and len(briefs) and "unit_id" in briefs:
            cnt = briefs[briefs.category == cat].groupby("unit_id").size()
            pct = _num(u.pct).fillna(0)
            got = u.unit_id.map(cnt).fillna(0)
            fair = pct / 100 * n_q
            for name, p, g in zip(u.unit_name, pct, got, strict=True):
                if p / 100 * n_q >= 1 and g == 0:
                    alert(cat, "warning", "High-demand leaf without a question", f"{name} ({p:.1f}% of the demand)")
            for name, f, g in zip(u.unit_name, fair, got, strict=True):
                if g > 2 * f and g - f >= 2:
                    alert(
                        cat, "warning", "Leaf with many questions", f"{name}: {int(g)} questions, its share is {f:.1f}"
                    )
            if got.sum():
                gal = math.sqrt(0.5 * float(((pct - got / got.sum() * 100) ** 2).sum()))

        rc = rev[rev.category == cat] if len(rev) else rev
        n_fix = int((rc.action == "fixed").sum()) if len(rc) else 0
        n_rep = int((rc.action == "replaced").sum()) if len(rc) else 0
        if n_fix:
            alert(cat, "info", "Briefs fixed by the AI review", f"{n_fix}: see the AI review")
        if n_rep:
            alert(cat, "info", "Briefs replaced by the AI review", f"{n_rep}: see the AI review")
        lv = [a["level"] for a in alerts if a["category"] == cat]
        rows.append(
            {
                "category": cat,
                "status": "error" if "error" in lv else ("warning" if "warning" in lv else "ok"),
                "questions": n_q,
                "target": target,
                "intents_full": f"{full} of {len(chosen)}",
                "judge_mean": round(float(judge.mean()), 2) if judge.notna().any() else None,
                "judge_min": float(judge.min()) if judge.notna().any() else None,
                "weight_pct": round(float(_num(q.weight_pct_category).fillna(0).sum()), 1),
                "fixed_by_ai": n_fix,
                "replaced_by_ai": n_rep,
                "gallagher": round(gal, 1) if not math.isnan(gal) else None,
            }
        )
    al = pd.DataFrame(alerts, columns=ALERT_COLS)
    if len(al):
        al = al.assign(_o=al.level.map(LEVELS.index)).sort_values(["category", "_o"], kind="stable").drop(columns="_o")
    return pd.DataFrame(rows, columns=SUMMARY_COLS), al.reset_index(drop=True)
