"""Comprueba que Bedrock responde con la configuración actual, antes de lanzar una ejecución real.

    uv run rufus-check-bedrock

Hace una llamada mínima (unos pocos tokens) a cada modelo de config.json → llm.models, en llm.region,
con la misma identidad que el pipeline: aws.role_profile, si no aws.session_profile, si no aws.profile.
Muestra qué funciona y, si algo falla, por qué.
"""

import sys
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from rufus_queries.lib.common import Config


def _converse(br, model: str) -> dict:
    """Llamada mínima; si el modelo no acepta temperature (p. ej. Claude Haiku 5.5), se repite sin ella."""
    msg = [{"role": "user", "content": [{"text": "Reply with the word OK."}]}]
    try:
        return br.converse(modelId=model, messages=msg, inferenceConfig={"maxTokens": 5, "temperature": 0})
    except ClientError as e:
        if "temperature" not in str(e):
            raise
        return br.converse(modelId=model, messages=msg, inferenceConfig={"maxTokens": 5})


def main():
    """Llamada mínima a cada modelo configurado; muestra qué funciona y por qué falla."""
    cfg = Config.load()
    aws, llm = cfg["aws"], cfg["llm"]
    profiles = boto3.Session().available_profiles
    prof = next(
        (p for p in (aws.get("role_profile"), aws.get("session_profile")) if p and p in profiles), aws.get("profile")
    )
    print(f"Perfil: {prof} · región: {llm['region']}")
    sess = boto3.Session(profile_name=prof)
    try:
        print("Identidad:", sess.client("sts").get_caller_identity()["Arn"])
    except (BotoCoreError, ClientError) as e:
        sys.exit(f"No se puede autenticar: {e}\n→ ¿Has ejecutado uv run rufus-aws-login hoy?")
    br = sess.client("bedrock-runtime", region_name=llm["region"])
    used = {llm["models"].get(a, a) for a in llm["stage_models"].values()}
    ok = True
    for alias, model in llm["models"].items():
        if model not in used:
            continue
        t = time.time()
        try:
            r = _converse(br, model)
            out = "".join(b.get("text", "") for b in r["output"]["message"]["content"]).strip()
            print(f"  ✓ {alias:7} {model}  → “{out}” en {time.time() - t:.1f}s")
        except ClientError as e:
            ok = False
            code = e.response.get("Error", {}).get("Code", "")
            hint = {
                "AccessDeniedException": (
                    "falta permiso bedrock:InvokeModel para este modelo/región (o la sesión no tiene MFA)"
                ),
                "ResourceNotFoundException": "el id del modelo no existe en esta región",
                "ValidationException": "id de modelo o región incorrectos",
            }.get(code, "")
            print(f"  ✗ {alias:7} {model}  {code}: {str(e)[:160]}" + (f"\n      → {hint}" if hint else ""))
    if ok:
        print('Todo listo: en la app elige IA = Bedrock (o config.json → llm.provider = "bedrock").')
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
