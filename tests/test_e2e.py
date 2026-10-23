"""El pipeline completo en modo mock produce un set de preguntas con el formato esperado."""
# pylint: disable=missing-function-docstring,redefined-outer-name  # tests de pytest: el nombre ya lo explica

import json

import pandas as pd


def test_final_questions(pipeline_out):
    """El set final tiene preguntas con su intent, peso y volumen."""
    fq = pd.read_csv(pipeline_out / "s9_output" / "final_questions.csv", keep_default_na=False)
    assert len(fq) > 0
    assert {"id", "intent", "question", "weight_pct_category", "volume"} <= set(fq.columns)
    assert fq.question.str.len().gt(0).all()
    assert (pipeline_out / "s9_output" / "results.xlsx").exists()


def test_six_versions_per_question(pipeline_out):
    cands = json.loads((pipeline_out / "s7_generate" / "candidates.json").read_text(encoding="utf-8"))
    assert {len(c["candidates"]) for c in cands} == {6}
    chosen = pd.read_csv(pipeline_out / "s8_check_judge" / "chosen.csv", keep_default_na=False)
    fq = pd.read_csv(pipeline_out / "s9_output" / "final_questions.csv", keep_default_na=False)
    assert len(chosen) == len(fq) == len(cands)  # una pregunta por ficha, como antes
    assert (chosen.n_versions == 6).all()
    assert chosen.opening.value_counts().max() <= 2  # no más de 2 preguntas con el mismo comienzo
