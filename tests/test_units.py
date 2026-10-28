"""Reglas sueltas de los pasos: lectura de entradas, marcas, fusiones, selección con topes y controles."""
# pylint: disable=missing-function-docstring,redefined-outer-name  # tests de pytest: el nombre ya lo explica

import json

import pandas as pd
import pytest

from rufus_queries.lib import common
from rufus_queries.lib.common import Config, Out, chunks
from rufus_queries.steps import s2_classify
from rufus_queries.steps.s1_universe import build_nodes, load_tree
from rufus_queries.steps.s2_classify import (
    batched,
    category_lines,
    category_nodes,
    load_keywords,
    node_lines,
    resolve_slug,
    unique_slugs,
)
from rufus_queries.steps.s3_weight import brand_families
from rufus_queries.steps.s4_focus import LeafKw, parse_merges
from rufus_queries.steps.s6_select import fill_rest, mark_brands, near_dup, product_term, take_groups, toks
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
    # segunda llamada (turnos): el tope por hoja del intent se acumula → A ya tiene 2; de B solo cabe g5
    assert take_groups(cand, 5, ("cat", "I05"), state, (2, 9)) == 1
    # otro intent: su propio tope, pero no repite una necesidad ya elegida en la misma hoja → solo queda g3
    assert take_groups(cand, 5, ("cat", "I06"), state, (2, 9)) == 1


def test_product_term_rules():
    unit_generic = pd.DataFrame({"keyword": ["coffee k cups", "coffee pods"], "volume": [5, 9]})
    grp = pd.DataFrame({"keyword": ["decaf coffee k cups"], "brand": [""], "volume": [3]})
    assert product_term(grp, ["decaf"], unit_generic, "Pods") == ("coffee k cups", "residual")
    branded = pd.DataFrame({"keyword": ["acme decaf"], "brand": ["acme"], "volume": [3]})
    assert product_term(branded, ["decaf"], unit_generic, "Pods") == ("coffee pods", "unit_generic")
    assert toks("The best of Coffee") == ["best", "coffee"]
    water = pd.DataFrame({"keyword": ["water bottles", "distilled water"], "volume": [5, 9]})
    pack = pd.DataFrame({"keyword": ["water bottles 24 pack"], "brand": [""], "volume": [3]})
    assert product_term(pack, ["bottles 24 pack", "24 pack"], water, "Still water") == ("water bottles", "residual")
    sports = pd.DataFrame({"keyword": ["pickle juice", "sports drink", "electrolyte drink"], "volume": [9, 2, 5]})
    assert product_term(branded, ["decaf"], sports, "Sports drinks") == ("sports drink", "unit_generic")


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


def test_chunks_even_and_bounded():
    assert [len(c) for c in chunks(list(range(25)), 12)] == [9, 8, 8]
    assert [len(c) for c in chunks(list(range(5)), 12)] == [5]
    assert not chunks([], 12)


def test_category_codes_are_readable_slugs():
    nodes = build_nodes(
        pd.DataFrame({"breadcrumb": ["G > Beverages > Tea", "G > Breads and bakery > Bread", "H > Beverages > Kits"]})
    )
    cats = pd.DataFrame({"category": ["G > Beverages", "G > Breads and bakery", "H > Beverages"]})
    text, code_of = category_lines(cats, nodes, 3)
    assert code_of == {
        "beverages": "G > Beverages",
        "breads-and-bakery": "G > Breads and bakery",
        "h-beverages": "H > Beverages",
    }
    assert text.splitlines()[0] == "beverages | G > Beverages | e.g. Tea"


def test_resolve_slug_tolerates_small_variations():
    valid = {"breads-and-bakery": "B", "beverages": "V"}
    assert resolve_slug("beverages", valid) == "V"
    assert resolve_slug("Breads and Bakery", valid) == "B"
    assert resolve_slug("bread-and-bakery", valid) == "B"
    assert resolve_slug("xx", valid, fixed=("XX",)) == "XX"
    assert resolve_slug("pet-food", valid) is None
    assert unique_slugs([["other"], ["other", "tea-other"], ["other", "tea-other"]]) == [
        "other",
        "tea-other",
        "tea-other-2",
    ]


def test_node_lines_mark_branches_and_disambiguate():
    nodes = build_nodes(
        pd.DataFrame({"breadcrumb": ["S > C > Coffee > Other", "S > C > Tea > Other", "S > C > Tea > Green"]})
    )
    nm = category_nodes("S > C", nodes)
    assert set(nm.slug) == {"other", "tea-other", "green", "coffee", "tea"}
    lines = node_lines(nm)
    assert "coffee | Coffee [branch]" in lines
    assert "tea-other | Tea > Other" in lines


# ── keyword_leaves (paso 2) ────────────────────────────────────────────────


def test_load_keyword_leaves(tmp_path):
    p = tmp_path / "m.csv"
    pd.DataFrame({"KEYWORD": [" Coffee  Pods", "x", "coffee pods"], "TOP_LEAF": ["A > B", "Z > Z", "A > C"]}).to_csv(
        p, index=False
    )
    m = s2_classify.load_keyword_leaves(p, {"A > B", "A > C"})
    assert m.leaf.to_dict() == {"coffee pods": "A > B"}
    assert m.share.isna().all()  # sin columna category_share
    pd.DataFrame({"keyword": ["soda"], "top_leaf": ["A > B"], "CATEGORY_SHARE": ["40"]}).to_csv(p, index=False)
    assert s2_classify.load_keyword_leaves(p, {"A > B"}).share.to_dict() == {"soda": 40.0}
    pd.DataFrame({"KEYWORD": ["a"], "OTHER": ["b"]}).to_csv(p, index=False)
    with pytest.raises(ValueError):
        s2_classify.load_keyword_leaves(p, set())


def test_category_of_and_user_line():
    cats = ["Grocery > Beverages", "Grocery > Beverages > Coffee", "Beauty"]
    assert s2_classify.category_of("Grocery > Beverages > Coffee > Pods", cats) == "Grocery > Beverages > Coffee"
    assert s2_classify.category_of("Grocery > Beverages > Tea", cats) == "Grocery > Beverages"
    assert s2_classify.category_of("Toys > Dolls", cats) == ""
    row = pd.DataFrame({"kid": ["K1", "K2"], "keyword": ["a", "b"], "hint": ["pods", ""]})
    assert [s2_classify.user_line(r) for r in row.itertuples()] == ["K1\ta\tpods", "K2\tb"]


def test_final_code_with_known_leaf():
    path_of = {"P1": "Bev > Coffee", "L1": "Bev > Coffee > Ground", "L2": "Bev > Coffee > Pods", "P2": "Bev > Tea"}
    code_of_path = {v: k for k, v in path_of.items()}
    fc = s2_classify.final_code
    assert fc("L2", "", path_of, code_of_path) == "L2"  # sin keyword_leaves manda la IA
    assert fc("L2", "Bev > Coffee > Ground", path_of, code_of_path) == "L1"  # otra leaf → la del mapeo
    assert fc("P1", "Bev > Coffee > Ground", path_of, code_of_path) == "P1"  # rama que la contiene → rama
    assert fc("P2", "Bev > Coffee > Ground", path_of, code_of_path) == "L1"  # rama ajena → la del mapeo
    assert fc("N0", "Bev > Coffee > Ground", path_of, code_of_path) == "L1"
    assert fc("NX", "Bev > Coffee > Ground", path_of, code_of_path) == "NX"
    assert fc("L1", "Bev > Coffee > Ground", path_of, code_of_path, "coffee") == "P1"  # nombre de la rama
    assert fc("L1", "Bev > Coffee > Ground", path_of, code_of_path, "coffees") == "P1"
    assert fc("L1", "Bev > Coffee > Ground", path_of, code_of_path, "tea") == "L1"  # rama que no la contiene


def test_fill_rest_ignores_leaf_caps_but_not_quotas():
    el = pd.DataFrame(
        {"block": ["I07", "I05", "I07", "I01", "I07"], "group": ["a", "b", "c", "d", "e"], "unit_id": list("AAABA")}
    )
    state = {"used": {"A": 4}, "picked": {("cat", "I05", "b", "A")}, "sel": []}
    room = {"I07": 2, "I01": 5}
    assert fill_rest(el, 5, "cat", state, room) == ["I07", "I07", "I01"]  # A ya tenía 4: se salta el tope
    assert [r["group"] for r in state["sel"]] == ["a", "c", "d"]  # e no: I07 ya llegó a su cuota
    assert room == {"I07": 0, "I01": 4}
    state2 = {"used": {}, "picked": set(), "sel": []}
    assert fill_rest(el, 1, "cat", state2, {"I01": 1, "I07": 5}) == ["I01"]  # primero el primero de room


def test_mark_brands_from_other_keywords():
    kws = pd.DataFrame(
        {
            "category": ["C"] * 4,
            "keyword": ["airpods", "apple airpods", "airpods pro case", "ear buds"],
            "brand": ["", "airpods", "", ""],
            "brand_family": ["", "apple", "", ""],
        }
    )
    m = mark_brands(kws)
    assert m.brand.tolist() == ["airpods", "airpods", "airpods", ""]
    assert m.brand_family.tolist() == ["apple", "apple", "apple", ""]


def test_near_dup():
    picked = {("cat", "I06", "powder packets", "U1")}
    assert near_dup(("cat", "I06"), "powder", "U1", picked)
    assert not near_dup(("cat", "I06"), "powder", "U2", picked)  # otra hoja
    assert not near_dup(("cat", "I05"), "powder", "U1", picked)  # otro intent
    assert not near_dup(("cat", "I06"), "12 pack", "U1", {("cat", "I06", "24 pack", "U1")})


def test_fallback_pick():
    from rufus_queries.steps.s8_check_judge import fallback_pick  # pylint: disable=import-outside-toplevel

    g = pd.DataFrame(
        {
            "text": ["a b c", "d e f g", "h i", "j k"],
            "fails": ["need", "need", "brand:x", "product;need"],
            "judge": [3.6, 3.8, 5.0, None],
            "words": [3, 4, 2, 2],
        }
    )
    assert fallback_pick(g).text == "d e f g"  # mejor nota; la de marca nunca
    assert fallback_pick(g[g.fails.str.startswith("brand")]) is None


def test_template_ignores_product_and_need():
    from rufus_queries.steps.s8_check_judge import template  # pylint: disable=import-outside-toplevel

    a = template("What body wash should I buy?", {"product": "body wash", "need": ""})
    b = template("What vitamin c serum should I buy?", {"product": "serum", "need": "vitamin c"})
    c = template("Looking for vitamin c serums, any suggestions?", {"product": "serum", "need": "vitamin c"})
    assert a == b == "what should i buy"  # misma plantilla aunque empiecen distinto
    assert c == "looking for any suggestions"


def test_final_code_other_node_named_by_keyword():
    path_of = {
        "L1": "Audio > Headphones > Over-ear headphones",
        "P2": "Audio > Headphones > Earbud headphones",
        "L3": "Audio > Headphones > Earbud headphones > Sleep earbuds",
    }
    code_of_path = {v: k for k, v in path_of.items()}
    leaf = "Audio > Headphones > Over-ear headphones"
    fc = s2_classify.final_code
    assert fc("P2", leaf, path_of, code_of_path, "wireless earbuds") == "P2"  # nombra el otro nodo
    assert fc("P2", leaf, path_of, code_of_path, "ear buds") == "P2"  # "ear buds" → "earbud"
    assert fc("P2", leaf, path_of, code_of_path, "airpods") == "L1"  # no lo nombra: manda Amazon
    assert fc("L3", leaf, path_of, code_of_path, "over ear headphones") == "L1"


def test_single_leaf_branch_is_one_unit():
    from rufus_queries.steps.s3_weight import single_leaf_branches  # pylint: disable=import-outside-toplevel

    nm = pd.DataFrame(
        {
            "category": ["C"] * 5,
            "code": ["P1", "P2", "L1", "L2", "L3"],
            "node_path": ["C > Head", "C > Head > Earbud", "C > Head > Earbud > Sleep", "C > Head > Over", "C > Case"],
            "is_leaf": [False, False, True, True, True],
        }
    )
    assert single_leaf_branches(nm) == {("C", "P2"): ("L1", "Earbud")}  # P1 tiene dos hojas


def test_is_latin():
    from rufus_queries.steps.s6_select import is_latin  # pylint: disable=import-outside-toplevel

    assert is_latin("café au lait 12-pack")
    assert not is_latin("耳机")
    assert not is_latin("наушники")


def test_product_term_named_or_top_generic_by_shared_words():
    branded = pd.DataFrame({"keyword": ["gatorade powder"], "brand": ["gatorade"], "volume": [3]})
    sports = pd.DataFrame({"keyword": ["pickle juice", "sports drinks"], "volume": [9, 2]})
    assert product_term(branded, ["powder"], sports, "Sports drinks") == ("sports drinks", "unit_generic")  # empate
    canned = pd.DataFrame({"keyword": ["canned", "wet cat food"], "volume": [1, 9]})
    kitten = pd.DataFrame({"keyword": ["kitten wet food"], "brand": ["sheba"], "volume": [3]})
    assert product_term(kitten, ["kitten"], canned, "Canned") == ("wet cat food", "unit_generic")
    heads = pd.DataFrame({"keyword": ["on ear headphones", "\ufffcheadphones"], "volume": [9, 2]})
    kids = pd.DataFrame({"keyword": ["wired headphones for kids"], "brand": [""], "volume": [3]})
    assert product_term(kids, ["for kids"], heads, "On-ear headphones") == ("headphones", "unit_generic")
    wand = pd.DataFrame({"keyword": ["toilet bowl cleaner"], "volume": [9]})
    refills = pd.DataFrame({"keyword": ["toilet wand refills"], "brand": [""], "volume": [3]})
    # sin palabras buscadas que formen otra genérica: se queda igual y lo mira la revisión con IA
    assert product_term(refills, ["refills"], wand, "Toilet cleaners") == ("toilet bowl cleaner", "unit_generic")
    drains = pd.DataFrame({"keyword": ["drain opener", "drain"], "volume": [9, 2]})
    flies = pd.DataFrame({"keyword": ["fruit fly drain treatment"], "brand": [""], "volume": [3]})
    assert product_term(flies, ["fruit fly"], drains, "Drain openers") == ("drain opener", "unit_generic")  # núcleo
    serums = pd.DataFrame({"keyword": ["volufiline", "serum"], "volume": [9, 2]})
    peptide = pd.DataFrame({"keyword": ["ghk-cu peptide serum"], "brand": ["x"], "volume": [3]})
    assert product_term(peptide, ["ghk-cu peptide"], serums, "Serums") == ("serum", "unit_generic")


def test_need_names_other_node():
    from rufus_queries.steps.s6_select import need_names_other_node  # pylint: disable=import-outside-toplevel

    cat = "Pets > Cats"
    nm = pd.DataFrame(
        {
            "category": [cat] * 4,
            "code": ["P1", "L1", "P2", "L2"],
            "node_path": [f"{cat} > Wet", f"{cat} > Wet > Canned", f"{cat} > Dry", f"{cat} > Dry > Kibble"],
        }
    )
    g = pd.DataFrame(
        {
            "category": [cat] * 4,
            "unit_id": ["L1"] * 4,
            "block": ["I05", "I05", "I05", "G"],
            "group": ["dry", "wet", "pate", "dry"],
        }
    )
    # "dry" nombra otro nodo; "wet" es su propia rama; "pate" no es ningún nodo; en G no se mira
    assert need_names_other_node(g, nm).tolist() == [True, False, False, False]


def test_product_without_need_and_same_need_across_intents():
    towers = pd.DataFrame({"keyword": ["gaming pc", "desktop computer"], "volume": [9, 5]})
    grp = pd.DataFrame({"keyword": ["gaming pc"], "brand": [""], "volume": [3]})
    # "gaming pc" ya lleva la necesidad "gaming": la pregunta sería "gaming pc for gaming"
    assert product_term(grp, ["gaming"], towers, "Towers") == ("desktop computer", "unit_generic")
    picked = {("cat", "I04", "anti theft", "U1")}
    assert near_dup(("cat", "I08"), "anti-theft", "U1", picked)  # misma necesidad en otro intent
    assert not near_dup(("cat", "I08"), "anti-theft", "U2", picked)


def test_review_fix_only_from_real_keywords():
    from rufus_queries.steps.s6_select import fix_duplicates, valid_fix  # pylint: disable=import-outside-toplevel

    b = {
        "category": "c",
        "intent": "I05",
        "product": "skin care",
        "need": "korean",
        "top_keywords": ["korean serum", "korean skin care serums"],
    }
    assert valid_fix({"product": "Serum", "need": "korean"}, b) == {"product": "serum", "need": "korean"}
    assert valid_fix({"product": "face cream", "need": "korean"}, b) is None  # "face cream" no sale en las keywords
    assert valid_fix({"product": "serum", "need": ""}, b) is None  # fuera de G hace falta necesidad
    assert valid_fix(None, b) is None
    kit = {"intent": "I03", "need": "kitten", "literal_needs": ["kitten"], "top_keywords": ["dry kitten food"]}
    assert valid_fix({"product": "kitten food", "need": "dry"}, kit) is None  # cambiaría de intent
    assert valid_fix({"product": "food", "need": "kittens"}, kit) == {"product": "food", "need": "kittens"}
    assert valid_fix({"product": "serum", "need": "korean"}, b | {"intent": "G"}) == {"product": "serum", "need": ""}
    other = {"category": "c", "intent": "I05", "product": "Serum", "need": "korean"}
    assert fix_duplicates({"product": "serum", "need": "korean"}, b, [b, other])
    assert not fix_duplicates({"product": "serum", "need": "korean"}, b, [b, other | {"intent": "I06"}])


def test_brand_used_as_product_name_is_allowed():
    from rufus_queries.steps.s8_check_judge import brands_for  # pylint: disable=import-outside-toplevel

    brands = ["stanley", "thermos"]
    assert brands_for(brands, {"product": "food thermos", "need": "for hot food"}) == ["stanley"]
    assert brands_for(brands, {"product": "lunch box", "need": "kids"}) == brands
    assert brands_for(["owala"], {"product": "water bottle", "need": "owalas"}) == ["owala"]  # palabra completa


def test_product_is_not_a_two_type_label():
    gen = pd.DataFrame({"keyword": ["disinfectant sprays & liquids", "disinfectant spray"], "volume": [9, 5]})
    grp = pd.DataFrame({"keyword": ["hypochlorous acid spray for home"], "brand": [""], "volume": [3]})
    assert product_term(grp, ["for home"], gen, "Disinfectant sprays and liquids") == (
        "disinfectant spray",
        "unit_generic",
    )
