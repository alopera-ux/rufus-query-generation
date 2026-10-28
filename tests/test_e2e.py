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


def test_generate_and_judge_retry_missing_items(pipeline_out, run_config, tmp_path, monkeypatch):
    """Si una respuesta de generar o del juez llega sin alguna ficha, esas fichas se vuelven a pedir."""
    from rufus_queries.lib import llm as llm_mod  # pylint: disable=import-outside-toplevel
    from rufus_queries.steps import s7_generate, s8_check_judge  # pylint: disable=import-outside-toplevel

    out = tmp_path / "out"
    shutil.copytree(pipeline_out, out)
    monkeypatch.setenv("RUFUS_CACHE_DIR", str(tmp_path / "cache"))
    real, calls = llm_mod.mock_respond, {"generate": 0, "judge": 0}

    def flaky(stage, system, user):
        text = real(stage, system, user)
        if stage in calls:
            calls[stage] += 1
            if calls[stage] == 1:  # la primera respuesta de cada etapa pierde su primera ficha
                return json.dumps(json.loads(text)[1:])
        return text

    monkeypatch.setattr(llm_mod, "mock_respond", flaky)
    args = ["--config", str(run_config), "--out", str(out)]
    s7_generate.main(args)
    s8_check_judge.main(args)
    cands = json.loads((out / "s7_generate" / "candidates.json").read_text(encoding="utf-8"))
    assert all(c["candidates"] for c in cands)
    checked = pd.read_csv(out / "s8_check_judge" / "candidates_checked.csv")
    assert checked.judge.notna().all()
    assert calls["generate"] >= 2 and calls["judge"] >= 2


def test_need_naming_own_node_is_generic(tmp_path):
    """Paso 3: una necesidad que es el nombre del propio nodo o de una rama que lo contiene pasa a G."""
    from rufus_queries.steps import s3_weight  # pylint: disable=import-outside-toplevel

    out = tmp_path / "out"
    (out / "s2_classify").mkdir(parents=True)
    cat = "Pets > Cats"
    nodes = pd.DataFrame(
        {
            "category": [cat] * 4,
            "code": ["P1", "L1", "P2", "L2"],
            "node_path": [f"{cat} > Wet", f"{cat} > Wet > Canned", f"{cat} > Dry", f"{cat} > Dry > Kibble"],
            "rel": ["Wet", "Wet > Canned", "Dry", "Dry > Kibble"],
            "is_leaf": [False, True, False, True],
        }
    )
    nodes.to_csv(out / "s2_classify" / "nodes_map.csv", index=False)
    cl = pd.DataFrame(
        {
            "kid": ["K1", "K2", "K3"],
            "keyword": ["tiki cat wet food", "meow mix dry food", "pate wet food"],
            "volume": [10, 5, 3],
            "category": [cat] * 3,
            "code": ["L1", "L1", "L1"],
            "node_path": [f"{cat} > Wet > Canned"] * 3,
            "is_leaf": [True] * 3,
            "intent": ["I05", "I05", "I05"],
            "need": ["wet", "dry", "pate"],
            "brand": ["tiki cat", "meow mix", ""],
        }
    )
    cl.to_csv(out / "s2_classify" / "classified.csv", index=False)
    cfg = tmp_path / "c.json"
    cfg.write_text("{}", encoding="utf-8")
    s3_weight.main(["--config", str(cfg), "--out", str(out)])
    k = pd.read_csv(out / "s3_weight" / "classified_norm.csv", keep_default_na=False).set_index("keyword")
    assert k.loc["tiki cat wet food", "intent"] == "G"  # "wet" es su rama: nombre del producto
    assert k.loc["meow mix dry food", "intent"] == "I05"  # "dry" es OTRO nodo: no se toca
    assert k.loc["pate wet food", "intent"] == "I05"


def test_review_rejects_and_replaces(pipeline_out, run_config, tmp_path, monkeypatch):
    """La revisión con IA rechaza una ficha: sale del set y entra el siguiente grupo del mismo intent."""
    from rufus_queries.lib import llm as llm_mod  # pylint: disable=import-outside-toplevel
    from rufus_queries.steps import s6_select  # pylint: disable=import-outside-toplevel

    out = tmp_path / "out"
    shutil.copytree(pipeline_out, out)
    monkeypatch.setenv("RUFUS_CACHE_DIR", str(tmp_path / "cache"))
    before = json.loads((out / "s6_select" / "briefs.json").read_text(encoding="utf-8"))
    bad = next(b for b in before if b["intent"] != "G")
    real = llm_mod.mock_respond

    def reviewer(stage, system, user):
        if stage != "review":
            return real(stage, system, user)
        items = json.loads(user[user.index("[") :])
        return json.dumps(
            [{"id": it["id"], "ok": it["need"] != bad["need"], "reason": "contradicts the product"} for it in items]
        )

    monkeypatch.setattr(llm_mod, "mock_respond", reviewer)
    s6_select.main(["--config", str(run_config), "--out", str(out)])
    after = json.loads((out / "s6_select" / "briefs.json").read_text(encoding="utf-8"))
    assert bad["need"] not in [b["need"] for b in after if b["unit"] == bad["unit"]]
    count = lambda bs, i: sum(b["intent"] == i for b in bs)  # noqa: E731
    assert count(after, bad["intent"]) >= count(before, bad["intent"]) - 1  # sustituida si había otro grupo
    rev = pd.read_csv(out / "s6_select" / "review.csv")
    assert (~rev.ok).sum() >= 1 and "contradicts" in rev[~rev.ok].reason.iloc[0]


def test_review_fixes_instead_of_dropping(pipeline_out, run_config, tmp_path, monkeypatch):
    """Si el revisor propone una corrección hecha con palabras de las keywords, la ficha se queda corregida;
    si la corrección se inventa palabras, la ficha se descarta como antes."""
    from rufus_queries.lib import llm as llm_mod  # pylint: disable=import-outside-toplevel
    from rufus_queries.steps import s6_select  # pylint: disable=import-outside-toplevel

    out = tmp_path / "out"
    shutil.copytree(pipeline_out, out)
    monkeypatch.setenv("RUFUS_CACHE_DIR", str(tmp_path / "cache"))
    before = json.loads((out / "s6_select" / "briefs.json").read_text(encoding="utf-8"))
    good, bad = [b for b in before if b["intent"] != "G" and b["top_keywords"]][:2]
    word = s6_select.toks(good["top_keywords"][0])[-1]
    real = llm_mod.mock_respond

    def reviewer(stage, system, user):
        if stage != "review":
            return real(stage, system, user)
        res = []
        for it in json.loads(user[user.index("[") :]):
            if it.get("already_accepted"):
                continue
            if (it["product"], it["need"]) == (good["product"], good["need"]):
                res.append(
                    {
                        "id": it["id"],
                        "ok": False,
                        "reason": "wrong product",
                        "fix": {"product": word, "need": good["need"]},
                    }
                )
            elif (it["product"], it["need"]) == (bad["product"], bad["need"]):
                res.append(
                    {
                        "id": it["id"],
                        "ok": False,
                        "reason": "wrong product",
                        "fix": {"product": "zzzinvented", "need": bad["need"]},
                    }
                )
            else:
                res.append({"id": it["id"], "ok": True, "reason": "", "fix": None})
        return json.dumps(res)

    monkeypatch.setattr(llm_mod, "mock_respond", reviewer)
    s6_select.main(["--config", str(run_config), "--out", str(out)])
    after = json.loads((out / "s6_select" / "briefs.json").read_text(encoding="utf-8"))
    fixed = [b for b in after if b.get("product_source") == "review_fix"]
    assert [(b["unit"], b["intent"], b["product"]) for b in fixed] == [(good["unit"], good["intent"], word)]
    assert not any((b["unit"], b["product"], b["need"]) == (bad["unit"], bad["product"], bad["need"]) for b in after)
    rev = pd.read_csv(out / "s6_select" / "review.csv", keep_default_na=False)
    assert rev.reason.str.startswith("corregida").sum() == 1
    assert (rev.fix == f"{word} | {good['need']}").sum() == 1
