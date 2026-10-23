"""Crea el repo en GitHub, sube el código y protege la rama main.

    gh auth login                        # una vez, en tu terminal (no compartas el token)
    uv run python scripts/setup_github.py

Qué hace:
  1. git init + primer commit (si aún no es un repo)
  2. crea el repo privado OWNER/REPO y sube main
  3. reglas de main (rulesets):
       - "checks":  ruff, pylint y tests en verde para poder hacer merge; sin push directo ni force-push.
                    Nadie se lo salta, tampoco el admin.
       - "review":  PR con 1 aprobación de CODEOWNERS (@alopera-ux). El admin puede saltársela en sus propios PR,
                    porque GitHub no deja aprobar un PR propio.
Las reglas en repos privados necesitan GitHub Pro (o una organización con plan Team): si GitHub lo rechaza,
el script lo dice y el resto queda hecho; se pueden volver a aplicar luego con  --only-rules.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

OWNER = "alopera-ux"
REPO = "rufus-query-generation"
DESCRIPTION = "Preguntas para Rufus / Alexa for Shopping ponderadas por la demanda real de Amazon US"
CHECKS = ["ruff", "pylint", "tests"]
ROOT = Path(__file__).resolve().parent.parent

RULESETS = [
    {
        "name": "checks",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "bypass_actors": [],
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": c} for c in CHECKS],
                },
            },
        ],
    },
    {
        "name": "review",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        # 5 = rol "Repository admin": puede hacer merge de sus PR sin aprobación (nunca sin checks)
        "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}],
        "rules": [
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 1,
                    "require_code_owner_review": True,
                    "dismiss_stale_reviews_on_push": True,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": True,
                },
            }
        ],
    },
]


def sh(*cmd, check=True, stdin=None) -> subprocess.CompletedProcess:
    """Ejecuta un comando en la raíz del repo y devuelve su resultado (texto)."""
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True, capture_output=True, input=stdin)


def ensure_tools():
    """gh y git instalados, y gh con sesión iniciada como OWNER."""
    for tool in ("git", "gh"):
        if not shutil.which(tool):
            sys.exit(f"Falta {tool}. Instálalo (gh: https://cli.github.com) y vuelve a ejecutar.")
    if sh("gh", "auth", "status", check=False).returncode != 0:
        sys.exit("gh no tiene sesión: ejecuta  gh auth login  en tu terminal y vuelve a probar.")
    login = sh("gh", "api", "user", "-q", ".login").stdout.strip()
    if login.lower() != OWNER.lower():
        sys.exit(f"gh tiene sesión como {login}, no como {OWNER}: gh auth switch / gh auth login.")


def ensure_commit():
    """Repo git con al menos un commit en main."""
    if not (ROOT / ".git").exists():
        sh("git", "init", "-b", "main")
    if sh("git", "rev-parse", "--verify", "HEAD", check=False).returncode != 0:
        if not sh("git", "config", "user.email", check=False).stdout.strip():
            sys.exit('Configura git primero: git config --global user.name "…" y user.email "…"')
        sh("git", "add", "-A")
        sh("git", "commit", "-m", "Primera versión del pipeline de queries de Rufus")
        print("✓ primer commit")


def ensure_repo():
    """Crea el repo privado y sube main (si ya existe, solo se asegura el remoto)."""
    full = f"{OWNER}/{REPO}"
    if sh("gh", "repo", "view", full, check=False).returncode == 0:
        print(f"• {full} ya existe")
        if sh("git", "remote", "get-url", "origin", check=False).returncode != 0:
            sh("git", "remote", "add", "origin", f"https://github.com/{full}.git")
        sh("git", "push", "-u", "origin", "main")
        return
    sh(
        "gh",
        "repo",
        "create",
        full,
        "--private",
        "--source",
        ".",
        "--remote",
        "origin",
        "--push",
        "--description",
        DESCRIPTION,
    )
    sh("gh", "repo", "edit", full, "--delete-branch-on-merge", "--enable-squash-merge")
    print(f"✓ repo creado: https://github.com/{full}")


def _rules_failed(msg: str, name: str = "") -> bool:
    """Explica por qué no se han podido poner las reglas: plan de GitHub o error concreto."""
    if "Upgrade" in msg or "403" in msg or "not available" in msg.lower():
        print(
            "✗ GitHub no permite reglas en un repo privado con el plan Free.\n"
            "  Opciones: GitHub Pro en tu cuenta, mover el repo a una organización con plan Team,\n"
            "  o hacerlo público. Después:  uv run python scripts/setup_github.py --only-rules"
        )
        return False
    sys.exit(f"Error con las reglas {name}: {msg}")


def apply_rules() -> bool:
    """Crea o actualiza los rulesets de main. Devuelve False si el plan de GitHub no lo permite."""
    full = f"{OWNER}/{REPO}"
    got = sh("gh", "api", f"repos/{full}/rulesets", check=False)
    if got.returncode != 0:
        return _rules_failed((got.stdout + got.stderr).strip())
    existing = {r["name"]: r["id"] for r in json.loads(got.stdout or "[]")}
    for rs in RULESETS:
        if rs["name"] in existing:
            method, path = "PUT", f"repos/{full}/rulesets/{existing[rs['name']]}"
        else:
            method, path = "POST", f"repos/{full}/rulesets"
        r = sh("gh", "api", "-X", method, path, "--input", "-", stdin=json.dumps(rs), check=False)
        if r.returncode != 0:
            return _rules_failed((r.stdout + r.stderr).strip(), rs["name"])
        print(f"✓ ruleset '{rs['name']}'")
    return True


def main(argv=None):
    """Repo + reglas, o solo reglas con --only-rules."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only-rules", action="store_true", help="solo (re)aplica las reglas de main")
    a = ap.parse_args(argv)
    ensure_tools()
    if not a.only_rules:
        ensure_commit()
        ensure_repo()
    if apply_rules():
        print("Listo: main solo acepta PRs con ruff, pylint y tests en verde y tu aprobación.")


if __name__ == "__main__":
    main()
