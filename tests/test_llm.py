"""Cliente de IA: caché, modo manual (Claude), Bedrock con un cliente falso y lectura de JSON."""
# pylint: disable=missing-function-docstring,redefined-outer-name  # tests de pytest: el nombre ya lo explica

import pytest

from fakes import FakeBedrock, client_error, fake_boto3
from rufus_queries.lib import llm as llm_mod
from rufus_queries.lib.common import Config
from rufus_queries.lib.llm import LLM, parse_json


def make(tmp_path, provider, **llm_over):
    """LLM con la config por defecto y el proveedor indicado."""
    cfg = Config.load(overrides={"llm": {"provider": provider, "sleep_between_calls": 0, **llm_over}})
    return LLM(cfg, tmp_path)


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    """Sin RUFUS_PROVIDER del entorno."""
    monkeypatch.delenv("RUFUS_PROVIDER", raising=False)


def test_parse_json_variants():
    assert parse_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert parse_json('Here you go: {"b": 2} thanks') == {"b": 2}
    assert parse_json("no json") is None
    assert parse_json(None) is None


def test_key_is_stable():
    assert LLM.key("m", "s", "u", 0.0) == LLM.key("m", "s", "u", 0.0)
    assert LLM.key("m", "s", "u", 0.0) != LLM.key("m", "s", "u2", 0.0)


def test_manual_mode_pending_then_response(tmp_path):
    llm = make(tmp_path, "claude")
    assert llm.call("generate", "SYS", "USER") is None
    assert len(llm.pending) == 1
    name = llm.pending[0]
    pend = tmp_path / "manual" / "pending" / f"{name}.md"
    assert "SYS" in pend.read_text(encoding="utf-8")
    (tmp_path / "manual" / "responses" / f"{name}.txt").write_text("ANSWER", encoding="utf-8")
    llm2 = make(tmp_path, "claude")
    assert llm2.call("generate", "SYS", "USER") == "ANSWER"
    assert not pend.exists()
    # tercera vez: sale de la caché en disco
    llm3 = make(tmp_path, "claude")
    assert llm3.call("generate", "SYS", "USER") == "ANSWER"
    assert llm3.stats["cached"] == 1


def test_cache_ignores_broken_lines(tmp_path):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "judge.jsonl").write_text('{"key": "x", "text": "t"}\nnot json\n', encoding="utf-8")
    llm = make(tmp_path, "mock")
    assert llm._stage_cache("judge")["x"]["text"] == "t"  # pylint: disable=protected-access


def test_progress_file(tmp_path):
    llm = make(tmp_path, "mock")
    llm.expect(2, "Testing")
    llm.map("judge", [("s", "[]"), ("s2", "[]")])
    assert (tmp_path / "_progress.json").exists()


def test_bedrock_ok_and_parallel(tmp_path, monkeypatch):
    fake = FakeBedrock(["one", "two", "three"])
    monkeypatch.setattr(llm_mod, "boto3", fake_boto3(fake))
    llm = make(tmp_path, "bedrock", workers=3)
    out = llm.map("judge", [("s", "a"), ("s", "b"), ("s", "c")])
    assert sorted(out) == ["one", "three", "two"]
    assert llm.stats["calls"] == 3
    assert llm.stats["input_tokens"] == 30


def test_bedrock_retries_throttling(tmp_path, monkeypatch):
    fake = FakeBedrock([client_error("ThrottlingException"), "after retry"])
    monkeypatch.setattr(llm_mod, "boto3", fake_boto3(fake))
    monkeypatch.setattr(llm_mod.time, "sleep", lambda _s: None)
    llm = make(tmp_path, "bedrock")
    assert llm.call("judge", "s", "u") == "after retry"


def test_bedrock_disables_cache_point(tmp_path, monkeypatch):
    fake = FakeBedrock([client_error("ValidationException", "prompt caching not supported"), "ok"])
    monkeypatch.setattr(llm_mod, "boto3", fake_boto3(fake))
    llm = make(tmp_path, "bedrock")
    assert llm.call("judge", "s", "u") == "ok"
    assert "cachePoint" not in str(fake.calls[-1]["system"])


def test_bedrock_access_denied_stops(tmp_path, monkeypatch):
    fake = FakeBedrock([client_error("AccessDeniedException")])
    monkeypatch.setattr(llm_mod, "boto3", fake_boto3(fake))
    llm = make(tmp_path, "bedrock")
    with pytest.raises(SystemExit, match="AccessDeniedException"):
        llm.call("judge", "s", "u")


def test_bedrock_other_error_returns_none(tmp_path, monkeypatch):
    fake = FakeBedrock([client_error("SomethingOdd")])
    monkeypatch.setattr(llm_mod, "boto3", fake_boto3(fake))
    llm = make(tmp_path, "bedrock")
    assert llm.call("judge", "s", "u") is None
    assert llm.stats["failed"] == 1


def test_bedrock_without_mfa_session_stops(tmp_path, monkeypatch):
    fake = FakeBedrock([])
    monkeypatch.setattr(llm_mod, "boto3", fake_boto3(fake, profiles=("shalion",)))
    monkeypatch.setattr(llm_mod.sys.stdin, "isatty", lambda: False, raising=False)
    cfg = Config.load(
        overrides={
            "llm": {"provider": "bedrock"},
            "aws": {"mfa_serial": "arn:aws:iam::0:mfa/x", "session_profile": "missing"},
        }
    )
    with pytest.raises(SystemExit, match="NoSession"):
        LLM(cfg, tmp_path).call("judge", "s", "u")


def test_bedrock_uses_session_profile_and_role(tmp_path, monkeypatch):
    fake = FakeBedrock(["ok"])
    monkeypatch.setattr(llm_mod, "boto3", fake_boto3(fake, profiles=("shalion", "shalion-session")))
    cfg = Config.load(
        overrides={
            "llm": {"provider": "bedrock"},
            "aws": {"session_profile": "shalion-session", "role_arn": "arn:aws:iam::0:role/r"},
        }
    )
    assert LLM(cfg, tmp_path).call("judge", "s", "u") == "ok"
