"""Orquestador (rufus-run) y herramientas de AWS con boto3 falso."""
# pylint: disable=missing-function-docstring,redefined-outer-name  # tests de pytest: el nombre ya lo explica

import json

import pytest

from fakes import FakeBedrock, client_error, fake_boto3
from rufus_queries import run
from rufus_queries.lib import common
from rufus_queries.tools import aws_login, check_bedrock


def test_run_executes_step_in_subprocess(run_config, tmp_path):
    run.main(["--only", "1", "--config", str(run_config), "--out", str(tmp_path)])
    assert (tmp_path / "s1_universe" / "leaves.csv").exists()
    assert json.loads((tmp_path / "_status.json").read_text())["state"] == "done"


def test_run_stops_with_code_3_in_claude_mode(run_config, pipeline_out, tmp_path, monkeypatch):
    cfg = json.loads(run_config.read_text())
    cfg["llm"]["provider"] = "claude"
    p = tmp_path / "claude.json"
    p.write_text(json.dumps(cfg))
    out = tmp_path / "out"
    for step in ("s1_universe", "s2_classify", "s3_weight", "s4_focus", "s5_groups", "s6_select"):
        (out / step).mkdir(parents=True)
        for f in (pipeline_out / step).iterdir():
            (out / step / f.name).write_bytes(f.read_bytes())
    monkeypatch.delenv("RUFUS_PROVIDER", raising=False)
    monkeypatch.setenv("RUFUS_CACHE_DIR", str(tmp_path / "cache"))  # sin las respuestas mock de otros tests
    with pytest.raises(SystemExit) as e:
        run.main(["--from", "7", "--config", str(p), "--out", str(out)])
    assert e.value.code == 3
    assert json.loads((out / "_status.json").read_text())["state"] == "waiting"
    assert list((out / "manual" / "pending").glob("generate__*.md"))


def test_run_reports_step_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"inputs": {"tree": str(tmp_path / "missing.xlsx")}}))
    with pytest.raises(SystemExit, match="Paso 1"):
        run.main(["--only", "1", "--config", str(bad), "--out", str(tmp_path / "o")])


@pytest.fixture
def aws_cfg(tmp_path, monkeypatch):
    """config.json mínimo con bloque aws en una raíz temporal."""
    base = json.loads(common.DEFAULT_CONFIG.read_text(encoding="utf-8"))
    base["aws"].update(mfa_serial="arn:aws:iam::0:mfa/test", session_profile="test-session")
    p = tmp_path / "config.json"
    p.write_text(json.dumps(base))
    monkeypatch.setattr(common, "DEFAULT_CONFIG", p)
    monkeypatch.setattr(common, "LOCAL_CONFIG", tmp_path / "none.json")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return base


def test_aws_login_writes_session_profile(aws_cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(aws_login, "boto3", fake_boto3(FakeBedrock([])))
    monkeypatch.setattr("builtins.input", lambda _p: "123456")
    aws_login.main()
    creds = (tmp_path / ".aws" / "credentials").read_text()
    assert "[test-session]" in creds
    assert "aws_session_token = token" in creds
    assert "OK" in capsys.readouterr().out
    assert aws_cfg["aws"]["session_profile"] == "test-session"


def test_aws_login_needs_mfa_serial(aws_cfg, tmp_path):
    aws_cfg["aws"]["mfa_serial"] = None
    (tmp_path / "config.json").write_text(json.dumps(aws_cfg))
    with pytest.raises(SystemExit, match="mfa_serial"):
        aws_login.main()


@pytest.mark.usefixtures("aws_cfg")
def test_check_bedrock_ok_and_denied(monkeypatch, capsys):
    monkeypatch.setattr(check_bedrock, "boto3", fake_boto3(FakeBedrock(["OK", "OK"]), profiles=("test-session",)))
    with pytest.raises(SystemExit) as e:
        check_bedrock.main()
    assert e.value.code == 0
    assert "✓" in capsys.readouterr().out
    denied = FakeBedrock([client_error("AccessDeniedException"), client_error("AccessDeniedException")])
    monkeypatch.setattr(check_bedrock, "boto3", fake_boto3(denied))
    with pytest.raises(SystemExit) as e:
        check_bedrock.main()
    assert e.value.code == 1
    assert "bedrock:InvokeModel" in capsys.readouterr().out


def test_aws_login_writes_role_profile(aws_cfg, tmp_path, monkeypatch, capsys):
    aws_cfg["aws"].update(role_arn="arn:aws:iam::1:role/team", role_profile="team-role")
    (tmp_path / "config.json").write_text(json.dumps(aws_cfg))
    monkeypatch.setattr(aws_login, "boto3", fake_boto3(FakeBedrock([])))
    monkeypatch.setattr("builtins.input", lambda _p: "123456")
    aws_login.main()
    conf = (tmp_path / ".aws" / "config").read_text()
    assert "[profile team-role]" in conf
    assert "role_arn = arn:aws:iam::1:role/team" in conf
    assert "source_profile = test-session" in conf
    assert "rol 'team-role'" in capsys.readouterr().out


def test_check_bedrock_uses_role_profile(aws_cfg, tmp_path, monkeypatch, capsys):
    aws_cfg["aws"]["role_profile"] = "team-role"
    (tmp_path / "config.json").write_text(json.dumps(aws_cfg))
    boto = fake_boto3(FakeBedrock(["OK", "OK"]), profiles=("test-session", "team-role"))
    monkeypatch.setattr(check_bedrock, "boto3", boto)
    with pytest.raises(SystemExit) as e:
        check_bedrock.main()
    assert e.value.code == 0
    assert "Perfil: team-role" in capsys.readouterr().out


@pytest.mark.usefixtures("aws_cfg")
def test_check_bedrock_retries_without_temperature(monkeypatch, capsys):
    temp = client_error("ValidationException", "`temperature` is deprecated for this model.")
    fake = FakeBedrock([temp, "OK", temp, "OK"])
    monkeypatch.setattr(check_bedrock, "boto3", fake_boto3(fake, profiles=("test-session",)))
    with pytest.raises(SystemExit) as e:
        check_bedrock.main()
    assert e.value.code == 0
    assert "✓" in capsys.readouterr().out
