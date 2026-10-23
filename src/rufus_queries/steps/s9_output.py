"""Paso 9 · Salida: set final de preguntas por categoría + Excel con todos los pasos.

Entrada : s8_check_judge/{chosen,candidates_checked}.csv · s6_select/* · s4_focus/* · s3_weight/* · s2_classify/*
Salida  : out/s9_output/
    final_questions.csv   una fila por pregunta: categoría, intent, unidad, producto, necesidad, pregunta, peso, volumen
    results.xlsx          Resumen · Preguntas · Foco · Ranking intents · Grupos · Candidatas · Clasificación · Ajustes
"""

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from rufus_queries.lib.common import Config, Out, step_args

STEP = "s9_output"
F, FB, FT = Font(name="Arial", size=10), Font(name="Arial", size=10, bold=True), Font(name="Arial", size=14, bold=True)
HDR = PatternFill("solid", fgColor="D5FFBA")


def add_sheet(wb, title, df):
    """Añade una hoja con cabecera formateada y anchos de columna."""
    ws = wb.create_sheet(title[:31])
    ws.append(list(df.columns))
    for r in df.itertuples(index=False):
        ws.append([None if (isinstance(v, float) and pd.isna(v)) else v for v in r])
    for c in ws[1]:
        c.font, c.fill, c.alignment = FB, HDR, Alignment(wrap_text=True, vertical="top")
    if len(df) <= 20000:  # dar formato celda a celda a cientos de miles de filas tarda minutos y no aporta
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.font = F
    for i, col in enumerate(df.columns, 1):
        w = int(df[col].astype(str).str.len().quantile(0.9)) if len(df) else 10
        ws.column_dimensions[get_column_letter(i)].width = min(max(len(str(col)), w) + 2, 60)
    ws.freeze_panes = "A2"


def main(argv=None):
    """Paso 9: final_questions.csv y results.xlsx."""
    args = step_args(__doc__.splitlines()[0], argv)
    cfg, out = Config.load(args.config), Out(args.out)
    briefs = pd.DataFrame(out.read_json("s6_select", "briefs.json"))
    ch = out.read_csv("s8_check_judge", "chosen.csv")
    fin = briefs.merge(ch[["category", "id", "question", "structure", "judge"]], on=["category", "id"], how="left")
    fin["top_keywords"] = fin.top_keywords.apply(lambda x: "; ".join(x[:4]) if isinstance(x, list) else x)
    fin = fin[
        [
            "category",
            "id",
            "intent",
            "intent_name",
            "unit",
            "product",
            "need",
            "question",
            "structure",
            "judge",
            "weight_pct_category",
            "volume",
            "top_keywords",
        ]
    ]
    out.write_csv(STEP, "final_questions.csv", fin)

    units = out.read_csv("s4_focus", "units.csv")
    ranks = out.read_csv("s6_select", "intent_ranking.csv")
    groups = out.read_csv("s6_select", "groups_scored.csv")
    cands = out.read_csv("s8_check_judge", "candidates_checked.csv")
    kw = out.read_csv("s3_weight", "classified_norm.csv")
    fixes = out.read_csv("s3_weight", "fixes.csv")
    merges = out.read_csv("s4_focus", "merges.csv")

    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    lines = [
        ("Preguntas para Rufus / Alexa for Shopping", FT),
        (f"Keywords: {cfg['inputs']['keywords']} · periodo del volumen: {cfg['inputs'].get('volume_period', '?')}", F),
        (
            f"Foco {cfg.focus.get('threshold')} · {cfg.selection.get('n_intents')} intents × "
            f"{cfg.selection.get('questions_per_intent')} preguntas · "
            f"G compite como intent: {cfg.selection.get('generic_as_intent')}",
            F,
        ),
        (f"Proveedor IA: {cfg.llm.get('provider')}", F),
        ("", F),
    ]
    for cat, g in fin.groupby("category"):
        u = units[units.category == cat]
        f = u[u.in_focus.astype(str).str.lower() == "true"]
        r = ranks[(ranks.category == cat) & ranks.chosen.astype(str).isin(["True", "filler"])]
        lines += [
            (cat, FB),
            (f"  {len(f)} de {len(u)} unidades en el foco = {f.pct.astype(float).sum():.1f}% del volumen", F),
            ("  Intents: " + " · ".join(f"{x.intent} {x.intent_name} {x.share_pct}%" for x in r.itertuples()), F),
            (f"  {g.question.notna().sum()} preguntas · nota media del juez {g.judge.mean():.2f}", F),
            ("", F),
        ]
    for i, (t, f) in enumerate(lines, 1):
        ws.cell(row=i, column=1, value=t).font = f
    ws.column_dimensions["A"].width = 130

    add_sheet(wb, "Preguntas", fin)
    add_sheet(wb, "Foco (unidades)", units)
    add_sheet(wb, "Ranking intents", ranks)
    add_sheet(wb, "Grupos", groups.drop(columns=[c for c in ["Index"] if c in groups.columns]))
    add_sheet(wb, "Candidatas", cands)
    add_sheet(wb, "Clasificación", kw.sort_values("volume", ascending=False))
    add_sheet(wb, "Ajustes", fixes)
    add_sheet(wb, "Hojas juntadas", merges)
    p = out.path(STEP, "results.xlsx")
    wb.save(p)
    print(f"[{STEP}] {len(fin)} preguntas en {fin.category.nunique()} categorías → {p}")
    for r in fin.itertuples():
        print(f"         {r.id:<6} {r.question}")


if __name__ == "__main__":
    main()
