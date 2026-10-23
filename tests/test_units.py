"""Reglas sueltas de los pasos: lectura de entradas, marcas, fusiones, selección con topes y controles."""
# pylint: disable=missing-function-docstring,redefined-outer-name  # tests de pytest: el nombre ya lo explica

import json

import pandas as pd
import pytest

from rufus_queries.lib import common
from rufus_queries.lib.common import Config, Out
from rufus_queries.steps.s1_universe import build_nodes, load_tree
from rufus_queries.steps.s2_classify import batched, category_nodes, load_keywords
from rufus_queries.steps.s3_weight import brand_families
from rufus_queries.steps.s4_focus import LeafKw, parse_merges
from rufus_queries.steps.s6_select import product_term, take_groups, toks
from rufus_queries.steps.s8_check_judge import has


def test_load_tree_strips_home_and_needs_breadcrumb(tmp_path):
    p = tmp_path / "t.csv"
    pd.DataFrame({"Breadcrumb": ["Home > A > B > C", "A > B > D", "Only"]}).to_csv(p, index=False)
    lv = load_tree(p)
    assert list(lv.breadcrumb) == ["A > B > C", "A > B > D"]
    assert set(lv.category) == {"A > B"}
    nodes = build_nodes(lv)
    assert nodes.set_index("node_path").n_leaves["A > B"] == 2
    bad = tmp_path / "bad.csv"
    pd.DataFrame({"x": [1]}).to_csv(bad, index=False)
    with pytest.raises(ValueError, match="breadcrumb"):
        load_tree(bad)


def test_load_keywords_detects_columns_and_dedupes(tmp_path):
    p = tmp_path / "k.csv"
    pd.DataFrame({"Search_Term": ["Coffee  Pods", "coffee pods", "tea"], "Search_Volume": [10, 5, "x"]}).to_csv(
        p, index=False
    )
    kw = load_keywords(p)
    assert dict(zip(kw.keyword, kw.volume, strict=True)) == {"coffee pods": 15, "tea": 0}
    assert load_keywords(p, sample=1).keyword.tolist() == ["coffee pods"]
    bad = tmp_path / "bad.csv"
    pd.DataFrame({"a": [1]}).to_csv(bad, index=False)
    with pytest.raises(ValueError):
        load_keywords(bad)


def test_category_nodes_codes():
    nodes = build_nodes(pd.DataFrame({"breadcrumb": ["S > C > X > L1", "S > C > L2"]}))
    m = category_nodes("S > C", nodes)
    assert set(m[m.is_leaf].code) == {"L01", "L02"}
    assert set(m[~m.is_leaf].code) == {"P01"}
    assert [len(b) for b in batched(pd.DataFrame({"a": range(5)}), 2)] == [2, 2, 1]


def test_brand_families_prefix_and_alias():
    fam = brand_families(["coke", "coke zero", "pepsi", "coca cola", ""], {"coca cola": "coke"})
    assert fam == {"coke": "coke", "coke zero": "coke", "pepsi": "pepsi", "coca cola": "coke"}


def test_parse_merges_only_valid_codes():
    raw = "L01 + L02 | same product\nL01 + L09 | invalid code\nNONE"
    assert parse_merges(raw, {"L01", "L02", "L03"}) == [({"L01", "L02"}, "same product")]
    assert not parse_merges(None, {"L01"})


def test_leafkw_sorted_and_empty():
    kw = pd.DataFrame({"category": ["c", "c"], "code": ["L1", "L1"], "keyword": ["a", "b"], "volume": [1, 5]})
    lk = LeafKw(kw, kw.groupby(["category", "code"]).indices)
    assert lk.get(("c", "L1")).keyword.tolist() == ["b", "a"]
    assert lk.get(("c", "L9")).empty


def test_unit_names_join_merged_leaves_by_volume():
    """Nombre de una unidad fusionada = hojas por volumen descendente (mismo resultado que el cálculo anterior)."""
    g = pd.DataFrame(
        {"unit_id": ["U1", "U1", "U2"], "leaf_name": ["Seltzer", "Sparkling water", "Soda"], "total": [10, 30, 5]}
    )
    names = g.sort_values("total", ascending=False).groupby("unit_id").leaf_name.agg(" + ".join)
    old = {i: " + ".join(g[g.unit_id == i].sort_values("total", ascending=False).leaf_name) for i in ["U1", "U2"]}
    assert names.to_dict() == old == {"U1": "Sparkling water + Seltzer", "U2": "Soda"}


def test_take_groups_respects_caps():
    cand = pd.DataFrame({"unit_id": ["A", "A", "A", "B", "B"], "group": ["g1", "g2", "g3", "g4", "g5"]})
    state = {"used": {"B": 1}, "picked": set(), "sel": []}
    k = take_groups(cand, 5, ("cat", "I05"), state, (2, 2))
    # A: máx. 2 en el intent · B: ya tenía 1 en el set y el tope del set es 2 → solo 1 más
    assert k == 3
    assert [r["group"] for r in state["sel"]] == ["g1", "g2", "g4"]
    assert take_groups(cand, 5, ("cat", "I05"), state, (2, 9)) == 2  # no repite lo ya elegido


def test_product_term_rules():
    unit_generic = pd.DataFrame({"keyword": ["coffee k cups", "coffee pods"], "volume": [5, 9]})
    grp = pd.DataFrame({"keyword": ["decaf coffee k cups"], "brand": [""], "volume": [3]})
    assert product_term(grp, ["decaf"], unit_generic, "Pods") == ("coffee k cups", "residual")
    branded = pd.DataFrame({"keyword": ["acme decaf"], "brand": ["acme"], "volume": [3]})
    assert product_term(branded, ["decaf"], unit_generic, "Pods") == ("coffee pods", "unit_generic")
    assert toks("The best of Coffee") == ["best", "coffee"]


def test_has_matches_words_and_plurals():
    assert has("coffee pod", "which coffee pods should i get?")
    assert not has("decaf", "which coffee pods should i get?")
    assert not has("", "anything")


def test_config_merges_local_and_run(tmp_path, monkeypatch):
    base = tmp_path / "config.json"
    base.write_text(json.dumps({"llm": {"provider": "claude", "workers": 8}, "inputs": {"tree": "data/t.xlsx"}}))
    local = tmp_path / "config.local.json"
    local.write_text(json.dumps({"llm": {"workers": 2}}))
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"llm": {"provider": "mock"}}))
    monkeypatch.setattr(common, "DEFAULT_CONFIG", base)
    monkeypatch.setattr(common, "LOCAL_CONFIG", local)
    monkeypatch.setattr(common, "ROOT", tmp_path)
    cfg = Config.load(run)
    assert cfg.llm == {"provider": "mock", "workers": 2}
    assert cfg.input_path("tree") == (tmp_path / "data" / "t.xlsx").resolve()


def test_out_reports_missing_step(tmp_path):
    out = Out(tmp_path)
    out.write_json("s1", "a.json", {"x": 1})
    assert out.read_json("s1", "a.json") == {"x": 1}
    with pytest.raises(FileNotFoundError, match="s0"):
        out.read_csv("s0", "x.csv")
    with pytest.raises(FileNotFoundError):
        out.read_json("s0", "x.json")
