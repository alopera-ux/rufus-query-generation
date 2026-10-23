"""Utilidades comunes del pipeline: configuración, rutas de entrada/salida y lectura/escritura de pasos."""

import argparse
import copy
import json
import os
from pathlib import Path

import pandas as pd

PACKAGE_DIR = Path(__file__).resolve().parent.parent  # src/rufus_queries/
PROMPTS = PACKAGE_DIR / "prompts"
# raíz del proyecto (config.json, data/, runs/, out/): RUFUS_HOME o la raíz del repo
ROOT = Path(os.environ.get("RUFUS_HOME") or PACKAGE_DIR.parent.parent).resolve()
DEFAULT_CONFIG = ROOT / "config.json"
LOCAL_CONFIG = ROOT / "config.local.json"  # ajustes personales (AWS…), fuera de git


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class Config(dict):
    """dict con acceso por atributo de primer nivel (cfg.selection['n_intents'])."""

    __getattr__ = dict.get

    @classmethod
    def load(cls, path=None, overrides=None) -> "Config":
        """config.json + config.local.json + el JSON de la ejecución + overrides."""
        cfg = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        if LOCAL_CONFIG.exists():
            cfg = _merge(cfg, json.loads(LOCAL_CONFIG.read_text(encoding="utf-8")))
        if path:
            cfg = _merge(cfg, json.loads(Path(path).read_text(encoding="utf-8")))
        return cls(_merge(cfg, overrides or {}))

    def input_path(self, key: str) -> Path:
        """Ruta absoluta de una entrada (relativa a la raíz del proyecto)."""
        p = Path(self["inputs"][key])
        return p if p.is_absolute() else (ROOT / p).resolve()

    def model_for(self, stage: str) -> str:
        """Id del modelo para una etapa (resuelve alias lite/strong)."""
        alias = self["llm"]["stage_models"].get(stage, "lite")
        return self["llm"]["models"].get(alias, alias)


def step_args(description: str, argv=None) -> argparse.Namespace:
    """Argumentos comunes de cada paso: --config y --out."""
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--config", default=None, help="JSON que sobrescribe config.json")
    ap.add_argument("--out", default=str(ROOT / "out"), help="carpeta de salida (por defecto out/)")
    return ap.parse_args(argv)


class Out:
    """Carpeta de salida: cada paso escribe en out/<paso>/ y lee lo que dejaron los anteriores."""

    def __init__(self, base):
        self.base = Path(base)

    def dir(self, step: str) -> Path:
        """Carpeta del paso (se crea si no existe)."""
        d = self.base / step
        d.mkdir(parents=True, exist_ok=True)
        return d

    def path(self, step: str, name: str) -> Path:
        """Ruta de un fichero dentro de la carpeta del paso."""
        return self.dir(step) / name

    def write_csv(self, step: str, name: str, df: pd.DataFrame) -> Path:
        """Guarda un DataFrame en la carpeta del paso."""
        p = self.path(step, name)
        df.to_csv(p, index=False, encoding="utf-8")
        return p

    def read_csv(self, step: str, name: str) -> pd.DataFrame:
        """Lee un CSV de un paso anterior (error claro si falta)."""
        p = self.base / step / name
        if not p.exists():
            raise FileNotFoundError(f"Falta {p}: ejecuta antes el paso '{step}'.")
        return pd.read_csv(p, keep_default_na=False)

    def write_json(self, step: str, name: str, obj) -> Path:
        """Guarda un objeto JSON en la carpeta del paso."""
        p = self.path(step, name)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        return p

    def read_json(self, step: str, name: str):
        """Lee un JSON de un paso anterior (error claro si falta)."""
        p = self.base / step / name
        if not p.exists():
            raise FileNotFoundError(f"Falta {p}: ejecuta antes el paso '{step}'.")
        return json.loads(p.read_text(encoding="utf-8"))
