"""Dobles de boto3 para probar Bedrock y las herramientas de AWS sin red ni credenciales."""

import datetime as dt
from types import SimpleNamespace
from typing import ClassVar

from botocore.exceptions import ClientError


def client_error(code: str, msg: str = "boom") -> ClientError:
    """ClientError de botocore con el código dado."""
    return ClientError({"Error": {"Code": code, "Message": msg}}, "Converse")


class FakeBedrock:  # pylint: disable=too-few-public-methods
    """bedrock-runtime: devuelve los textos de `replies` en orden; si un elemento es una excepción, la lanza."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def converse(self, **kw):
        """Respuesta con el formato de la API Converse."""
        self.calls.append(kw)
        r = self.replies.pop(0) if self.replies else "OK"
        if isinstance(r, Exception):
            raise r
        return {
            "output": {"message": {"content": [{"text": r}]}},
            "usage": {"inputTokens": 10, "outputTokens": 2},
            "stopReason": "end_turn",
        }


class FakeSTS:
    """sts: identidad y tokens de sesión ficticios."""

    def get_caller_identity(self):
        """Identidad fija."""
        return {"Arn": "arn:aws:iam::000000000000:user/test"}

    def get_session_token(self, **_kw):
        """Credenciales temporales ficticias."""
        return {"Credentials": _creds()}

    def assume_role(self, **_kw):
        """Credenciales de rol ficticias."""
        return {"Credentials": _creds()}


def _creds():
    return {
        "AccessKeyId": "AKIAFAKE",
        "SecretAccessKey": "secret",
        "SessionToken": "token",
        "Expiration": dt.datetime(2030, 1, 1, 12, 0),
    }


def fake_boto3(bedrock: FakeBedrock, profiles=("shalion",)):
    """Módulo boto3 falso cuyo Session crea los clientes de arriba."""

    class Session:  # pylint: disable=too-few-public-methods
        """Sesión con perfiles fijos."""

        available_profiles: ClassVar[list] = list(profiles)
        created: ClassVar[list] = []  # kwargs de cada sesión creada, para comprobar qué perfil se usó

        def __init__(self, **kw):
            self.kw = kw
            Session.created.append(kw)

        def client(self, name, **_kw):
            """Cliente sts o bedrock-runtime."""
            return FakeSTS() if name == "sts" else bedrock

    return SimpleNamespace(Session=Session)
