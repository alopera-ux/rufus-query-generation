"""Abre una sesión temporal de AWS con MFA y la guarda como perfil, para que el pipeline y la app usen Bedrock
sin pedir el código cada vez (la app lanza los pasos en segundo plano y no puede preguntar).

    uv run rufus-aws-login           # pide el código MFA (1Password) y guarda el perfil de sesión

Lee de config.json → aws:
    profile          perfil con tus claves permanentes (aws configure --profile shalion)
    mfa_serial       ARN de tu dispositivo MFA (arn:aws:iam::<cuenta>:mfa/<usuario>)
    session_profile  perfil donde se guarda la sesión temporal (lo usa el pipeline)
    session_hours    duración de la sesión (máx. 36 h con MFA de usuario IAM)

Las claves nunca se escriben en el proyecto: solo en ~/.aws/credentials, como hace `aws configure`.
"""

import configparser
import sys
from pathlib import Path

import boto3

from rufus_queries.lib.common import Config


def main():
    """Pide el código MFA y guarda una sesión temporal en el perfil aws.session_profile."""
    aws = Config.load()["aws"]
    base_profile, serial = aws.get("profile"), aws.get("mfa_serial")
    target = aws.get("session_profile") or f"{base_profile}-session"
    if not serial:
        sys.exit(
            "Falta aws.mfa_serial en config.local.json (ARN de tu MFA, p. ej. arn:aws:iam::123456789012:mfa/usuario)."
        )
    hours = float(aws.get("session_hours", 12))
    code = input(f"Código MFA para {serial}: ").strip()
    sts = boto3.Session(profile_name=base_profile).client("sts")
    c = sts.get_session_token(SerialNumber=serial, TokenCode=code, DurationSeconds=int(hours * 3600))["Credentials"]

    path = Path.home() / ".aws" / "credentials"
    path.parent.mkdir(exist_ok=True)
    cp = configparser.RawConfigParser()
    cp.read(path, encoding="utf-8")
    if not cp.has_section(target):
        cp.add_section(target)
    cp.set(target, "aws_access_key_id", c["AccessKeyId"])
    cp.set(target, "aws_secret_access_key", c["SecretAccessKey"])
    cp.set(target, "aws_session_token", c["SessionToken"])
    with open(path, "w", encoding="utf-8") as fh:
        cp.write(fh)
    ident = boto3.Session(profile_name=target).client("sts").get_caller_identity()["Arn"]
    print(
        f"OK · sesión guardada en el perfil '{target}' hasta {c['Expiration']:%Y-%m-%d %H:%M} UTC · identidad {ident}"
    )
    print("Siguiente: uv run rufus-check-bedrock")


if __name__ == "__main__":
    main()
