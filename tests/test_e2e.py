"""El pipeline completo en modo mock produce un set de preguntas con el formato esperado."""
# pylint: disable=missing-function-docstring,redefined-outer-name  # tests de pytest: el nombre ya lo explica

import pandas as pd


def test_final_questions(pipeline_out):
    """El set final tiene preguntas con su intent, peso y volumen."""
    fq = pd.read_csv(pipeline_out / "s9_output" / "final_questions.csv", keep_default_na=False)
    assert len(fq) > 0
    assert {"id", "intent", "question", "weight_pct_category", "volume"} <= set(fq.columns)
    assert fq.question.str.len().gt(0).all()
    assert (pipeline_out / "s9_output" / "results.xlsx").exists()
