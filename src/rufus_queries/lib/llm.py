"""Capa LLM: Bedrock (Converse API; por defecto Claude Sonnet 5.5), mock offline y modo manual, con caché en disco.

- provider "bedrock": llama a Bedrock. Autenticación con perfil AWS + MFA, y opcionalmente
  asumiendo un rol en la cuenta de trabajo (role_arn).
- provider "mock":    respuestas sintéticas con la forma correcta (para probar la tubería).
- provider "claude":  igual que "manual": mientras no haya Bedrock, Claude (Cowork) responde los prompts.
- provider "manual":  escribe cada prompt en out/manual/pending/ y lee la respuesta de
  out/manual/responses/. Sirve para validar prompts a mano (o con otra herramienta).

Todas las respuestas se guardan en out/cache/<etapa>.jsonl, así que una ejecución
interrumpida se reanuda sin volver a pagar las llamadas ya hechas.
"""

import contextlib
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from rufus_queries.lib.common import ROOT
from rufus_queries.lib.mock_llm import mock_respond

FATAL = {
    "AccessDeniedException": "Stopping: this AWS identity is not allowed to call this model (IAM permission or "
    "model access). Ask for the Bedrock role/permission, then click Retry.",
    "UnrecognizedClientException": "Stopping: the AWS credentials are not valid. Refresh them and click Retry.",
    "ExpiredTokenException": "Stopping: the AWS session expired. Refresh the credentials and click Retry.",
    "ResourceNotFoundException": (
        "Stopping: the model id does not exist in this region. Check llm.models in config.json."
    ),
}


USAGE_KEYS = ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def usage_cost(model: str, u: dict, prices: dict) -> float | None:
    """Coste estimado en $ con llm.prices[model] = [entrada, salida(, lectura caché, escritura caché)] por millón.
    Sin 3.º/4.º valor: lectura de caché al 10 % y escritura al 125 % del precio de entrada."""
    p = prices.get(model)
    if not p:
        return None
    pin, pout = p[0], p[1]
    pr = p[2] if len(p) > 2 else pin * 0.1
    pw = p[3] if len(p) > 3 else pin * 1.25
    return (
        u["input_tokens"] * pin + u["output_tokens"] * pout + u["cache_read_tokens"] * pr + u["cache_write_tokens"] * pw
    ) / 1e6


class LLM:
    """Cliente de IA por etapas: Bedrock, Claude (manual) o mock, con caché en disco y llamadas en paralelo."""

    def __init__(self, cfg, out_dir: Path):
        self.cfg = cfg
        self.provider = os.environ.get("RUFUS_PROVIDER") or cfg["llm"]["provider"]
        self.out = Path(out_dir)
        # caché compartida entre ejecuciones (llm.cache_dir): la clave es modelo + prompt, así que un mismo lote
        # (p. ej. el paso 2a con el mismo fichero de keywords) no se vuelve a pagar en otra ejecución
        cd = os.environ.get("RUFUS_CACHE_DIR") or cfg["llm"].get("cache_dir")
        self.cache_dir = (Path(cd) if Path(str(cd)).is_absolute() else ROOT / cd) if cd else self.out / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self.pending: list[str] = []
        self.stats = {
            "calls": 0,
            "cached": 0,
            "failed": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        self.usage: dict[str, dict] = {}  # por modelo: llamadas y tokens reales de Bedrock
        self._client = None
        self._use_cache_point = bool(cfg["llm"].get("prompt_cache", True))
        self._no_temperature: set[str] = set()  # modelos que no aceptan temperature (p. ej. Claude Haiku 5.5)
        self._prog = {"label": "", "done": 0, "total": None, "started": time.time()}
        self._prog_w = 0.0
        self._lock = threading.RLock()  # caché, estadísticas y progreso se comparten entre hilos
        # llamadas simultáneas a Bedrock (los demás proveedores van de una en una)
        self.workers = max(1, int(cfg["llm"].get("workers", 8))) if self.provider == "bedrock" else 1

    # ── progreso (lo lee la app para la barra en vivo) ──────────────────
    def expect(self, total: int | None, label: str):
        """Anuncia cuántas llamadas va a hacer el paso a continuación."""
        self._prog = {"label": label, "done": 0, "total": total, "started": time.time()}
        self._prog_w = 0.0
        self._write_progress(force=True)

    def _tick(self):
        with self._lock:
            self._prog["done"] += 1
            t = self._prog["total"]
            self._write_progress(force=bool(t) and self._prog["done"] >= t)

    def _write_progress(self, force=False):
        now = time.time()
        if not force and now - self._prog_w < 0.5:
            return
        self._prog_w = now
        with contextlib.suppress(OSError):
            (self.out / "_progress.json").write_text(json.dumps({**self._prog, "updated": now}), encoding="utf-8")

    def report(self, step: str):
        """Imprime las estadísticas del paso y añade su consumo real (tokens y coste estimado) a <out>/usage.csv."""
        print(f"[{step}] LLM: {self.stats}")
        if not self.usage:
            return
        prices = self.cfg["llm"].get("prices", {})
        rows, total = [], 0.0
        for model, u in self.usage.items():
            cost = usage_cost(model, u, prices)
            total += cost or 0
            rows.append({"step": step, "model": model, **u, "cost_usd": "" if cost is None else round(cost, 4)})
        p = self.out / "usage.csv"
        new = not p.exists()
        with open(p, "a", encoding="utf-8") as fh:
            if new:
                fh.write(",".join(rows[0]) + "\n")
            for r in rows:
                fh.write(",".join(str(v) for v in r.values()) + "\n")
        hit = self.stats["cache_read_tokens"] / max(1, self.stats["cache_read_tokens"] + self.stats["input_tokens"])
        print(f"[{step}] coste estimado {total:.2f} $ · tokens de entrada leídos de la caché de prompts {hit:.0%}")

    # ── caché ───────────────────────────────────────────────────────────
    def _stage_cache(self, stage: str) -> dict:
        with self._lock:
            return self._load_stage(stage)

    def _load_stage(self, stage: str) -> dict:
        if stage not in self._cache:
            d = {}
            f = self.cache_dir / f"{stage}.jsonl"
            if f.exists():
                for line in f.read_text(encoding="utf-8").splitlines():
                    try:
                        r = json.loads(line)
                        d[r["key"]] = r
                    except (ValueError, KeyError):
                        pass  # línea de caché incompleta: se ignora
            self._cache[stage] = d
        return self._cache[stage]

    def _store(self, stage: str, key: str, model: str, text: str):
        rec = {"key": key, "model": model, "text": text, "ts": time.time()}
        with self._lock:
            self._stage_cache(stage)[key] = rec
            with open(self.cache_dir / f"{stage}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @staticmethod
    def key(model: str, system: str, user: str, temperature: float) -> str:
        """Clave de caché de una llamada."""
        h = hashlib.sha1(f"{model}\x00{temperature}\x00{system}\x00{user}".encode())
        return h.hexdigest()[:16]

    # ── llamada principal ───────────────────────────────────────────────
    def call(
        self,
        stage: str,
        system: str,
        user: str,
        model_stage: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str | None:
        """Una llamada (con caché); en modo claude deja el prompt pendiente y devuelve None."""
        llm = self.cfg["llm"]
        model = self.cfg.model_for(model_stage or stage)
        temperature = llm["temperature"].get(stage, 0.3) if temperature is None else temperature
        max_tokens = max_tokens or llm["max_tokens"].get(stage, 4000)
        try:
            return self._call(stage, system, user, model, temperature, max_tokens)
        finally:
            self._tick()

    def map(self, stage: str, prompts: list, model_stage: str | None = None) -> list:
        """Varias llamadas de la misma etapa: prompts = [(system, user), ...] → respuestas en el mismo orden.
        Con Bedrock se lanzan en paralelo (llm.workers hilos); con los demás proveedores, una detrás de otra.
        Las que ya están en caché no se vuelven a pedir."""
        if not prompts:
            return []
        if self.workers <= 1 or len(prompts) == 1:
            return [self.call(stage, sy, us, model_stage) for sy, us in prompts]
        if self.provider == "bedrock" and not self._all_cached(stage, prompts, model_stage):
            self._client_or_exit()  # credenciales/MFA en el hilo principal, antes de abrir los hilos
        out = [None] * len(prompts)
        pool = ThreadPoolExecutor(max_workers=self.workers)
        futs = {pool.submit(self.call, stage, sy, us, model_stage): i for i, (sy, us) in enumerate(prompts)}
        try:
            for f in as_completed(futs):
                out[futs[f]] = f.result()  # un error fatal (SystemExit) llega aquí y para el resto
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return out

    def _call(self, stage, system, user, model, temperature, max_tokens):
        llm = self.cfg["llm"]
        k = self.key(model, system, user, temperature)
        hit = self._stage_cache(stage).get(k)
        if hit:
            with self._lock:
                self.stats["cached"] += 1
            return hit["text"]

        if self.provider == "mock":
            text = mock_respond(stage, system, user)
            time.sleep(float(os.environ.get("RUFUS_MOCK_DELAY", 0)))  # para probar la barra de progreso
        elif self.provider in ("manual", "claude"):
            text = self._manual(stage, k, model, system, user)
            if text is None:
                return None
        else:
            text = self._bedrock(model, system, user, temperature, max_tokens)
            time.sleep(llm.get("sleep_between_calls", 0))
        if text is None:
            with self._lock:
                self.stats["failed"] += 1
            return None
        with self._lock:
            self.stats["calls"] += 1
        self._store(stage, k, model, text)
        return text

    # ── modo manual ─────────────────────────────────────────────────────
    def _manual(self, stage, k, model, system, user):
        pend = self.out / "manual" / "pending"
        resp = self.out / "manual" / "responses"
        pend.mkdir(parents=True, exist_ok=True)
        resp.mkdir(parents=True, exist_ok=True)
        name = f"{stage}__{k}"
        r = resp / f"{name}.txt"
        if r.exists():
            (pend / f"{name}.md").unlink(missing_ok=True)
            return r.read_text(encoding="utf-8")
        (pend / f"{name}.md").write_text(
            f"<!-- stage={stage} model={model} -->\n# SYSTEM\n\n{system}\n\n# USER\n\n{user}\n", encoding="utf-8"
        )
        with self._lock:
            self.pending.append(name)
        return None

    # ── Bedrock ─────────────────────────────────────────────────────────
    def _session(self):

        aws = self.cfg["aws"]
        profiles = boto3.Session().available_profiles
        # 0) rol del equipo en otra cuenta (perfil con role_arn + source_profile que escribe tools/aws_login.py):
        #    botocore asume el rol con la sesión MFA y lo renueva solo, así que no caduca a mitad de ejecución
        rp = aws.get("role_profile")
        if rp and rp in profiles:
            return boto3.Session(profile_name=rp)
        # 1) sesión temporal con MFA creada por tools/aws_login.py (lo normal: la app no puede pedir el código)
        sp = aws.get("session_profile")
        if sp and sp in profiles:
            base = boto3.Session(profile_name=sp)
            if aws.get("role_arn"):
                c = base.client("sts").assume_role(
                    RoleArn=aws["role_arn"],
                    RoleSessionName="rufus-pipeline",
                    DurationSeconds=int(aws.get("session_seconds", 3600)),
                )["Credentials"]
                return boto3.Session(
                    aws_access_key_id=c["AccessKeyId"],
                    aws_secret_access_key=c["SecretAccessKey"],
                    aws_session_token=c["SessionToken"],
                )
            return base
        if aws.get("mfa_serial") and not sys.stdin.isatty():
            raise SystemExit(
                "ERROR Bedrock NoSession: no hay sesión AWS con MFA.\n"
                "Stopping: run  uv run rufus-aws-login  (asks for your MFA code), then click Retry."
            )
        base = boto3.Session(profile_name=aws.get("profile") or None)
        creds = None
        if aws.get("role_arn"):
            kw = {
                "RoleArn": aws["role_arn"],
                "RoleSessionName": "rufus-pipeline",
                "DurationSeconds": int(aws.get("session_seconds", 3600)),
            }
            if aws.get("mfa_serial"):
                kw.update(SerialNumber=aws["mfa_serial"], TokenCode=input("Código MFA: ").strip())
            creds = base.client("sts").assume_role(**kw)["Credentials"]
        elif aws.get("mfa_serial"):
            creds = base.client("sts").get_session_token(
                SerialNumber=aws["mfa_serial"],
                TokenCode=input("Código MFA: ").strip(),
                DurationSeconds=max(int(aws.get("session_seconds", 3600)), 900),
            )["Credentials"]
        if creds:
            return boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )
        return base

    def _all_cached(self, stage: str, prompts: list, model_stage: str | None) -> bool:
        """True si todas las llamadas ya están en caché (entonces no hacen falta credenciales de AWS)."""
        model = self.cfg.model_for(model_stage or stage)
        temperature = self.cfg["llm"]["temperature"].get(stage, 0.3)
        cache = self._stage_cache(stage)
        return all(self.key(model, sy, us, temperature) in cache for sy, us in prompts)

    def _client_or_exit(self):
        """Cliente de Bedrock; si las credenciales no valen (sesión MFA caducada…), para con un mensaje claro."""
        try:
            return self._bedrock_client()
        except (BotoCoreError, ClientError) as e:
            expired = "ExpiredToken" in str(e) or "expired" in str(e).lower()
            raise SystemExit(
                f"ERROR Bedrock {type(e).__name__}: {str(e)[:400]}\n"
                + (
                    "Stopping: the AWS MFA session has expired (it lasts 12 h). "
                    if expired
                    else "Stopping: the AWS profile/credentials could not be loaded on this computer. "
                )
                + "Run  uv run rufus-aws-login  (MFA code), check aws.profile / aws.role_profile, then click Retry."
            ) from None

    def _bedrock_client(self):
        if self._client is None:
            sess = self._session()
            self._client = sess.client(
                "bedrock-runtime",
                region_name=self.cfg["llm"]["region"],
                config=BotoConfig(
                    retries={"max_attempts": 8, "mode": "adaptive"},
                    max_pool_connections=max(10, self.workers + 2),
                    connect_timeout=10,
                    read_timeout=180,
                ),
            )
            ident = sess.client("sts").get_caller_identity()["Arn"]
            print(f"  Bedrock listo · región {self.cfg['llm']['region']} · identidad {ident}")
        return self._client

    def _bedrock(self, model, system, user, temperature, max_tokens):

        client = self._client_or_exit()
        retries = int(self.cfg["llm"].get("max_retries", 5))
        for attempt in range(retries + 1):
            # qué se envía en ESTA llamada: con varios hilos, otro puede haber cambiado los ajustes mientras tanto
            sent_cache = self._use_cache_point
            sent_temp = model not in self._no_temperature
            sys_blocks = [{"text": system}]
            if sent_cache:
                sys_blocks.append({"cachePoint": {"type": "default"}})
            try:
                r = client.converse(
                    modelId=model,
                    system=sys_blocks,
                    messages=[{"role": "user", "content": [{"text": user}]}],
                    inferenceConfig=(
                        {"maxTokens": max_tokens, "temperature": temperature}
                        if sent_temp
                        else {"maxTokens": max_tokens}
                    ),
                )
                u = r.get("usage", {})
                with self._lock:
                    self.stats["input_tokens"] += u.get("inputTokens", 0)
                    self.stats["output_tokens"] += u.get("outputTokens", 0)
                    self.stats["cache_read_tokens"] += u.get("cacheReadInputTokens", 0)
                    self.stats["cache_write_tokens"] += u.get("cacheWriteInputTokens", 0)
                    mu = self.usage.setdefault(model, dict.fromkeys(USAGE_KEYS, 0))
                    mu["calls"] += 1
                    mu["input_tokens"] += u.get("inputTokens", 0)
                    mu["output_tokens"] += u.get("outputTokens", 0)
                    mu["cache_read_tokens"] += u.get("cacheReadInputTokens", 0)
                    mu["cache_write_tokens"] += u.get("cacheWriteInputTokens", 0)
                if r.get("stopReason") == "max_tokens":
                    print("    ⚠️  respuesta cortada por max_tokens")
                return "".join(b.get("text", "") for b in r["output"]["message"]["content"])
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                msg = str(e)
                if code == "ValidationException" and "temperature" in msg and sent_temp:
                    with self._lock:
                        if model not in self._no_temperature:
                            print(f"    ({model} no admite temperature; se envía sin ella)")
                        self._no_temperature.add(model)
                    continue
                if code == "ValidationException" and "cach" in msg.lower() and sent_cache:
                    with self._lock:
                        if self._use_cache_point:
                            print("    (este modelo no admite prompt caching; se desactiva)")
                        self._use_cache_point = False
                    continue
                transient = (
                    code
                    in {
                        "ThrottlingException",
                        "ServiceUnavailableException",
                        "InternalServerException",
                        "ModelTimeoutException",
                        "ModelNotReadyException",
                    }
                    or "Too many" in msg
                )
                if transient and attempt < retries:
                    w = min(30, 2**attempt) + random.uniform(0, 1.5)
                    print(f"    ⏳ {code}: reintento {attempt + 1}/{retries} en {w:.1f}s")
                    time.sleep(w)
                    continue
                if code in FATAL:
                    # no tiene sentido seguir: todas las llamadas fallarían igual
                    raise SystemExit(f"ERROR Bedrock {code}: {msg[:400]}\n{FATAL[code]}") from None
                print(f"    ❌ Bedrock {code}: {msg[:300]}")
                return None
            except BotoCoreError as e:
                if type(e).__name__ in (
                    "NoCredentialsError",
                    "PartialCredentialsError",
                    "ProfileNotFound",
                    "SSOTokenLoadError",
                    "UnauthorizedSSOTokenError",
                ):
                    raise SystemExit(
                        f"ERROR Bedrock {type(e).__name__}: {e}\n"
                        "Stopping: no valid AWS credentials on this computer. Log in to AWS, then click Retry."
                    ) from None
                if attempt < retries:
                    time.sleep(min(30, 2**attempt))
                    continue
                print(f"    ❌ Bedrock transporte: {e}")
                return None
        return None


# ── utilidades JSON ─────────────────────────────────────────────────────────


def parse_json(raw: str | None):
    """Extrae el primer JSON (array u objeto) de la respuesta, tolerando ```json```, texto alrededor y, en un
    array, objetos mal cerrados (se rescatan los que estén bien)."""
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    for open_, close in (("[", "]"), ("{", "}")):
        i, j = s.find(open_), s.rfind(close)
        if i != -1 and j > i:
            try:
                return json.loads(s[i : j + 1])
            except json.JSONDecodeError:
                continue
    if s.find("[") != -1 and s.find("[") < s.find("{"):
        return salvage_objects(s) or None
    return None


def salvage_objects(s: str) -> list:
    """Objetos válidos de un array JSON mal cerrado: el modelo a veces escribe una llave de más ("}}}},") y se
    perdía el lote entero. Lee cada objeto completo y salta lo que sobra hasta el siguiente."""
    dec, out, i = json.JSONDecoder(), [], s.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(s, i)
        except json.JSONDecodeError:
            i = s.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            out.append(obj)
        i = s.find("{", end)
    return out
