"""App web: páginas y endpoints sobre una ejecución mock ya terminada (sin lanzar procesos)."""
# pylint: disable=missing-function-docstring,redefined-outer-name  # tests de pytest: el nombre ya lo explica

import json
import shutil

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from rufus_queries.app import main as app_main

RID = "20261023-101500"


@pytest.fixture
def client(tmp_path, monkeypatch, pipeline_out, run_config, inputs):
    """App con runs/ en una carpeta temporal y una ejecución terminada (la del e2e mock)."""
    runs = tmp_path / "runs"
    d = runs / RID
    shutil.copytree(pipeline_out, d / "out")
    shutil.copy(run_config, d / "config.json")
    (d / "log.txt").write_text("=== 1. s1_universe ===\nHecho.\n", encoding="utf-8")
    (d / "out" / "_status.json").write_text(json.dumps({"state": "done", "step": 9}), encoding="utf-8")
    default = json.loads(json.dumps(app_main.DEFAULT))
    default["inputs"].update(tree=str(inputs / "tree.xlsx"), intents=str(inputs / "intents.xlsx"))
    launched = []
    monkeypatch.setattr(app_main, "RUNS", runs)
    monkeypatch.setattr(app_main, "DEFAULT", default)
    monkeypatch.setattr(app_main, "_CATS", [])
    monkeypatch.setattr(app_main, "_MAP", [])
    monkeypatch.setattr(app_main, "launch", lambda rid, start=1: launched.append((rid, start)))
    c = TestClient(app_main.app)
    c.launched = launched
    return c


def test_index_lists_categories(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Grocery and gourmet food &gt; Beverages" in r.text


def test_runs_page(client):
    r = client.get("/runs")
    assert r.status_code == 200
    assert f"/runs/{RID}" in r.text


def test_run_page_shows_questions_and_universe(client):
    r = client.get(f"/runs/{RID}")
    assert r.status_code == 200
    assert "<svg" in r.text
    assert "coffee pods" in r.text.lower()
    assert "6 versions" in r.text
    assert "chosen" in r.text
    assert client.get("/runs/nope").status_code == 404


def test_live_endpoint(client):
    r = client.get(f"/runs/{RID}/live")
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "done"
    assert data["questions"] > 0
    assert client.get("/runs/../etc/live").status_code == 404


def test_leaf_keywords(client, pipeline_out):
    lw = pd.read_csv(pipeline_out / "s3_weight" / "leaf_weights.csv", keep_default_na=False)
    row = lw[lw.total > 0].iloc[0]
    r = client.get(f"/runs/{RID}/leaf", params={"cat": row.category, "code": row.code})
    assert r.status_code == 200
    assert r.json()["keywords"]
    assert client.get(f"/runs/{RID}/leaf", params={"cat": row.category, "code": "L999"}).status_code == 404


def test_download_and_static(client):
    assert client.get(f"/runs/{RID}/results.xlsx").status_code == 200
    assert client.get("/runs/missing/results.xlsx").status_code == 404
    assert client.get("/static/favicon.png").status_code == 200


def test_upload_tree_returns_categories(client, inputs):
    with open(inputs / "tree.xlsx", "rb") as fh:
        r = client.post("/categories", files={"tree": ("tree.xlsx", fh)})
    assert r.status_code == 200
    assert "Grocery and gourmet food > Beverages" in r.json()["categories"]
    r = client.post("/categories", files={"tree": ("bad.csv", b"x\n1\n")})
    assert r.status_code == 400


def test_new_run_writes_config_and_launches(client):
    r = client.post(
        "/run",
        data={
            "categories": ["Grocery and gourmet food > Beverages"],
            "n_intents": "4",
            "overrides": "G=3, I04=6",
            "provider": "mock",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    rid = r.headers["location"].rsplit("/", 1)[-1]
    cfg = json.loads((app_main.RUNS / rid / "config.json").read_text(encoding="utf-8"))
    assert cfg["selection"]["n_intents"] == 4
    assert cfg["selection"]["questions_per_intent_override"] == {"G": 3, "I04": 6}
    assert client.launched == [(rid, 1)]
    r = client.post(f"/runs/{RID}/resume", follow_redirects=False)
    assert r.status_code == 303


def test_running_and_error_states(client):
    st = app_main.RUNS / RID / "out" / "_status.json"
    st.write_text(json.dumps({"state": "running", "step": 3, "step_started": 0}), encoding="utf-8")
    (app_main.RUNS / RID / "out" / "_progress.json").write_text(
        json.dumps({"label": "x", "done": 1, "total": 4}), encoding="utf-8"
    )
    assert client.get(f"/runs/{RID}").status_code == 200
    assert client.get(f"/runs/{RID}/live").json()["progress"]["total"] == 4
    st.write_text(json.dumps({"state": "error", "step": 2}), encoding="utf-8")
    (app_main.RUNS / RID / "log.txt").write_text("ERROR Bedrock AccessDenied\nStopping: fix it\n", encoding="utf-8")
    r = client.get(f"/runs/{RID}")
    assert "Retry" in r.text


def test_run_page_quality_review_and_rerun(client):
    out = app_main.RUNS / RID / "out"
    rev = pd.read_csv(out / "s6_select" / "review.csv", keep_default_na=False)
    rev.loc[0, ["ok", "fix", "reason"]] = [True, "serum | korean", "corregida: keywords ask for serum"]
    rev.loc[1, ["ok", "reason"]] = [False, "duplicate of another brief"]
    rev.to_csv(out / "s6_select" / "review.csv", index=False)
    r = client.get(f"/runs/{RID}")
    assert "Quality check" in r.text and "AI cost" in r.text
    assert "AI review" in r.text and "serum | korean" in r.text and "duplicate of another brief" in r.text
    assert "corregida" not in r.text  # la acción ya dice que está corregida
    assert "Re-run from step 6" in r.text
    r = client.post(f"/runs/{RID}/rerun", data={"step": "6"}, follow_redirects=False)
    assert r.status_code == 303 and client.launched[-1] == (RID, 6)
    assert client.post("/runs/nope/rerun", data={"step": "6"}).status_code == 404


def test_new_run_defaults_to_bedrock(client):
    assert "Focus threshold" not in client.get("/").text
    r = client.post("/run", data={"categories": ["Grocery and gourmet food > Beverages"]}, follow_redirects=False)
    rid = r.headers["location"].rsplit("/", 1)[-1]
    cfg = json.loads((app_main.RUNS / rid / "config.json").read_text(encoding="utf-8"))
    assert cfg["llm"]["provider"] == "bedrock" and "focus" not in cfg
