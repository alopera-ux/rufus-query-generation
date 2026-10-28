"""Cliente de IA: caché, modo manual (Claude), Bedrock con un cliente falso y lectura de JSON."""
# pylint: disable=missing-function-docstring,redefined-outer-name  # tests de pytest: el nombre ya lo explica

import time

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
def no_env(monkeypatch, tmp_path):
    """Sin RUFUS_PROVIDER del entorno y con una caché de IA propia de cada test."""
    monkeypatch.delenv("RUFUS_PROVIDER", raising=False)
    monkeypatch.setenv("RUFUS_CACHE_DIR", str(tmp_path / "cache"))


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


def test_bedrock_prefers_role_profile(tmp_path, monkeypatch):
    fake = FakeBedrock(["ok"])
    boto = fake_boto3(fake, profiles=("shalion", "shalion-session", "shalion-rmm"))
    monkeypatch.setattr(llm_mod, "boto3", boto)
    cfg = Config.load(
        overrides={
            "llm": {"provider": "bedrock"},
            "aws": {"session_profile": "shalion-session", "role_profile": "shalion-rmm"},
        }
    )
    assert LLM(cfg, tmp_path).call("judge", "s", "u") == "ok"
    assert {"profile_name": "shalion-rmm"} in boto.Session.created


def test_bedrock_retries_without_temperature(tmp_path, monkeypatch):
    fake = FakeBedrock([client_error("ValidationException", "`temperature` is deprecated for this model."), "ok"])
    monkeypatch.setattr(llm_mod, "boto3", fake_boto3(fake))
    llm = make(tmp_path, "bedrock")
    assert llm.call("judge", "s", "u") == "ok"
    assert "temperature" not in fake.calls[-1]["inferenceConfig"]


def test_shared_cache_dir_from_config(tmp_path, monkeypatch):
    monkeypatch.delenv("RUFUS_CACHE_DIR")
    cfg = Config.load(overrides={"llm": {"provider": "mock", "cache_dir": str(tmp_path / "shared")}})
    a = LLM(cfg, tmp_path / "run1")
    a.call("judge", "s", "[]")
    b = LLM(cfg, tmp_path / "run2")
    b.call("judge", "s", "[]")
    assert b.stats["cached"] == 1  # la segunda ejecución reutiliza la respuesta de la primera


def test_report_writes_usage_and_cost(tmp_path, monkeypatch, capsys):
    fake = FakeBedrock(["ok"])
    monkeypatch.setattr(llm_mod, "boto3", fake_boto3(fake))
    llm = make(tmp_path, "bedrock", prices={"eu.anthropic.claude-sonnet-5-5": [1.0, 2.0]})
    llm.call("judge", "s", "u")
    llm.report("s8_check_judge")
    usage = (tmp_path / "usage.csv").read_text(encoding="utf-8")
    assert "s8_check_judge,eu.anthropic.claude-sonnet-5-5,1,10,2,0,0" in usage
    assert "coste estimado" in capsys.readouterr().out


def test_usage_cost_defaults_for_cache():
    u = {
        "calls": 1,
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_read_tokens": 1_000_000,
        "cache_write_tokens": 0,
    }
    assert llm_mod.usage_cost("m", u, {"m": [1.0, 5.0]}) == 1.1
    assert llm_mod.usage_cost("x", u, {}) is None


class _NoTemperatureBedrock(FakeBedrock):  # pylint: disable=too-few-public-methods
    """Como Claude Sonnet 5.5: rechaza cualquier llamada que lleve temperature (con un pequeño retardo)."""

    def converse(self, **kw):
        time.sleep(0.05)  # que las llamadas en paralelo salgan todas antes del primer error
        if "temperature" in kw["inferenceConfig"]:
            raise client_error("ValidationException", "`temperature` is deprecated for this model.")
        return super().converse(**kw)


def test_parallel_calls_all_retry_without_temperature(tmp_path, monkeypatch):
    """Con varios hilos, todas las llamadas que salieron con temperature se repiten sin ella (antes solo la 1.ª)."""
    fake = _NoTemperatureBedrock(["a", "b", "c", "d"])
    monkeypatch.setattr(llm_mod, "boto3", fake_boto3(fake))
    llm = make(tmp_path, "bedrock", workers=4)
    out = llm.map("judge", [("s", str(i)) for i in range(4)])
    assert sorted(out) == ["a", "b", "c", "d"]
    assert llm.stats["failed"] == 0


def test_bedrock_cached_map_needs_no_credentials(tmp_path, monkeypatch):
    fake = FakeBedrock(["one", "two"])
    monkeypatch.setattr(llm_mod, "boto3", fake_boto3(fake))
    llm = make(tmp_path, "bedrock", workers=2)
    llm.map("judge", [("s", "a"), ("s", "b")])  # llena la caché
    again = make(tmp_path, "bedrock", workers=2)
    monkeypatch.setattr(again, "_bedrock_client", lambda: (_ for _ in ()).throw(AssertionError("no credentials")))
    assert sorted(again.map("judge", [("s", "a"), ("s", "b")])) == ["one", "two"]


def test_bedrock_expired_session_stops_with_message(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "boto3", fake_boto3(FakeBedrock([])))
    llm = make(tmp_path, "bedrock", workers=2)

    def expired():
        raise client_error("ExpiredToken", "The security token included in the request is expired")

    monkeypatch.setattr(llm, "_bedrock_client", expired)
    with pytest.raises(SystemExit, match="MFA session has expired"):
        llm.map("judge", [("s", "x"), ("s", "y")])


def test_parse_json_salvages_items_after_extra_brace():
    # caso real del juez (Sonnet 5.5): una llave de más tras un objeto hacía perder el lote entero
    raw = '[\n{"id":"I07-1","scores":{"1":{"realism":5}}}}},\n{"id":"I08-4","scores":{"1":{"realism":4}}}\n]'
    got = parse_json(raw)
    assert [x["id"] for x in got if "id" in x] == ["I07-1", "I08-4"]
    assert parse_json('[{"id": "a"}, {"id": "b"}]') == [{"id": "a"}, {"id": "b"}]
    assert parse_json("[ nada válido ]") is None
