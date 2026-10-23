"""Los 15 intents del Excel (pestaña 'Intent categories')."""

from dataclasses import dataclass
from pathlib import Path

import openpyxl


@dataclass
class Intent:
    """Un intent de la taxonomía de 15."""

    code: str
    name: str
    phase: str
    captures: str
    signals: str
    examples: list

    def prompt_line(self) -> str:
        """Línea del intent para los prompts."""
        return f"{self.code} {self.name}: {self.captures}. Signals: {self.signals}."


def load_intents(path: Path) -> list[Intent]:
    """Lee los intents de la pestaña 'Intent categories' del Excel."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Intent categories"]
    out = []
    for row in ws.iter_rows(values_only=True):
        cells = [c for c in row if c is not None]
        if len(cells) >= 8 and isinstance(cells[0], (int, float)):
            n, phase, name, captures, signals, e1, e2, e3 = cells[:8]
            out.append(
                Intent(
                    f"I{int(n):02d}",
                    str(name).strip(),
                    str(phase),
                    str(captures).strip(),
                    str(signals).strip(),
                    [str(e1), str(e2), str(e3)],
                )
            )
    if len(out) != 15:
        print(f"  ⚠️  se esperaban 15 intents y hay {len(out)}")
    return out
