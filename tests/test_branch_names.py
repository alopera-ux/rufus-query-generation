"""Convención de nombres de rama del ruleset "branch-names" (scripts/setup_github.py)."""
# pylint: disable=missing-function-docstring

import importlib.util
from fnmatch import fnmatchcase
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "setup_github", Path(__file__).resolve().parent.parent / "scripts" / "setup_github.py"
)
setup_github = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup_github)


def allowed(branch: str) -> bool:
    return any(fnmatchcase(f"refs/heads/{branch}", p) for p in setup_github.branch_patterns())


def test_convention_accepts_type_area_description():
    assert allowed("feat/prompts-versiones-preguntas")
    assert allowed("fix/app-desplegable-versiones")
    assert allowed("chore/infra-actualizar-dependencias")


def test_convention_rejects_other_names():
    for name in ("prueba", "feature/versiones", "feat/versiones", "feat/otra-area-x", "toni/cosas", "Feat/app-x"):
        assert not allowed(name), name


def test_ruleset_blocks_creation_except_convention():
    rs = next(r for r in setup_github.RULESETS if r["name"] == "branch-names")
    assert rs["rules"] == [{"type": "creation"}]
    assert "~DEFAULT_BRANCH" in rs["conditions"]["ref_name"]["exclude"]
    assert len(setup_github.branch_patterns()) == len(setup_github.BRANCH_TYPES) * len(setup_github.BRANCH_AREAS)
