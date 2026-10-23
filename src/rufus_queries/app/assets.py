"""CSS y JS de la app, guardados como ficheros en app/assets/ y cargados una vez al importar."""

from functools import cache
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"


@cache
def asset(name: str) -> str:
    """Contenido de app/assets/<name>."""
    return (ASSETS / name).read_text(encoding="utf-8")
