"""El pipeline completo en modo mock produce un set de preguntas con el formato esperado."""
# pylint: disable=missing-function-docstring,redefined-outer-name  # tests de pytest: el nombre ya lo explica

import json
import shutil

import pandas as pd

from rufus_queries.steps import s2_classify


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


def test_keyword_leaves(pipeline_out):
    """Las keywords de keyword_leaves no pasan por 2a y conservan su leaf en 2b."""
    ca = pd.read_csv(pipeline_out / "s2_classify" / "category_assign.csv", keep_default_na=False)
    src = dict(zip(ca.keyword, ca.cat_source, strict=True))
    assert src["coffee pods"] == src["almonds"] == "keyword_leaves"
    assert src["soda"] == "ia"  # su leaf no está en el árbol
    cl = pd.read_csv(pipeline_out / "s2_classify" / "classified.csv", keep_default_na=False).set_index("keyword")
    assert cl.loc["coffee pods", "node_path"].endswith("Coffee > Single-serve capsules and pods")
    assert cl.loc["coffee pods", "leaf_source"] == "keyword_leaves"
    assert cl.loc["coffee pods", "volume"] == cl.loc["coffee pods", "search_volume"] / 2  # category_share 50
    assert "almonds" not in cl.index  # Snacks está fuera del alcance (Beverages)


def test_only_keyword_leaves(pipeline_out, run_config, tmp_path):
    """Con classify_unmapped false solo quedan las keywords de keyword_leaves y no se llama a 2a."""
    cfg = json.loads(run_config.read_text(encoding="utf-8"))
    cfg["classify"].update(classify_unmapped=False, weight_by_category_share=False)
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    shutil.copytree(pipeline_out / "s1_universe", tmp_path / "out" / "s1_universe")
    s2_classify.main(["--config", str(tmp_path / "config.json"), "--out", str(tmp_path / "out")])
    ca = pd.read_csv(tmp_path / "out" / "s2_classify" / "category_assign.csv", keep_default_na=False)
    assert sorted(ca.keyword) == ["almonds", "coffee pods"]
    assert set(ca.cat_source) == {"keyword_leaves"}
    cl = pd.read_csv(tmp_path / "out" / "s2_classify" / "classified.csv", keep_default_na=False).set_index("keyword")
    assert cl.loc["coffee pods", "volume"] == cl.loc["coffee pods", "search_volume"]  # sin category_share
