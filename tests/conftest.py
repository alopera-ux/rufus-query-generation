"""Datos sintéticos pequeños para ejecutar el pipeline de punta a punta en modo mock (sin AWS ni datos reales)."""
# pylint: disable=redefined-outer-name  # así funcionan las fixtures de pytest

import json
import random

import openpyxl
import pandas as pd
import pytest

from rufus_queries import run
from rufus_queries.lib import common
from rufus_queries.steps import (
    s1_universe,
    s2_classify,
    s3_weight,
    s4_focus,
    s5_groups,
    s6_select,
    s7_generate,
    s8_check_judge,
    s9_output,
)

TREE = [
    "Grocery and gourmet food > Beverages > Coffee > Single-serve capsules and pods",
    "Grocery and gourmet food > Beverages > Coffee > Ground coffee",
    "Grocery and gourmet food > Beverages > Coffee > Instant coffee",
    "Grocery and gourmet food > Beverages > Bottled beverages > Energy drinks",
    "Grocery and gourmet food > Beverages > Bottled beverages > Soft drinks",
    "Grocery and gourmet food > Beverages > Bottled beverages > Sparkling water",
    "Grocery and gourmet food > Beverages > Bottled beverages > Sports drinks",
    "Grocery and gourmet food > Beverages > Juices > Orange juice",
    "Grocery and gourmet food > Snacks > Chips > Potato chips",
    "Grocery and gourmet food > Snacks > Nuts > Almonds",
    "Beauty > Skin care > Face > Moisturizers",
]

PRODUCTS = {
    "coffee pods": 900_000,
    "k cups": 400_000,
    "ground coffee": 300_000,
    "instant coffee": 250_000,
    "energy drink": 700_000,
    "energy drinks": 300_000,
    "soda": 500_000,
    "soft drinks": 100_000,
    "sparkling water": 400_000,
    "sports drinks": 150_000,
    "orange juice": 350_000,
    "potato chips": 300_000,
    "almonds": 200_000,
    "face moisturizer": 250_000,
}
MODIFIERS = [
    "decaf",
    "sugar free",
    "zero sugar",
    "organic",
    "12 pack",
    "24 pack",
    "mini",
    "variety pack",
    "vanilla",
    "chocolate",
    "for hydration",
    "for energy",
    "for kids",
    "for travel",
    "compatible with keurig",
    "vertuo",
    "cheap",
    "gift",
]
BRANDS = ["acme", "zesto"]

INTENTS = [
    (1, "Discover", "Occasion / Event", "a specific occasion", "party, christmas"),
    (2, "Discover", "Gift", "buying a gift", "gift for"),
    (3, "Discover", "Target User", "who will use it", "for kids, for men"),
    (4, "Consider", "Problem / Need", "a problem to solve", "for sleep, for hydration"),
    (5, "Consider", "Product Attributes", "flavor, color, material", "vanilla, black"),
    (6, "Consider", "Pack & Format Preference", "size or pack", "12 pack, mini"),
    (7, "Consider", "Compatibility & Fit", "fits a device", "for keurig, vertuo"),
    (8, "Consider", "Health, Safety & Ingredients", "ingredients and diet", "sugar free, organic"),
    (9, "Consider", "Quality & Durability", "how long it lasts", "durable"),
    (10, "Decide", "Price & Value", "budget", "cheap, premium"),
    (11, "Decide", "Reviews & Ratings", "social proof", "best rated"),
    (12, "Decide", "Delivery & Availability", "when it arrives", "fast delivery"),
    (13, "Decide", "Brand", "a specific brand", "brand names"),
    (14, "Decide", "Comparison", "comparing options", "vs, alternative"),
    (15, "Decide", "Sustainability", "eco friendly", "recyclable"),
]


def _keywords() -> pd.DataFrame:
    rnd = random.Random(7)
    rows = []
    for prod, vol in PRODUCTS.items():
        rows.append((prod, vol))
        rows.append((prod + "s" if not prod.endswith("s") else prod[:-1], vol // 5))
        for mod in rnd.sample(MODIFIERS, 9):
            kw = f"{prod} {mod}" if mod.startswith(("for", "compatible")) else f"{mod} {prod}"
            rows.append((kw, int(vol * rnd.uniform(0.02, 0.2))))
        for b in BRANDS:
            rows.append((f"{b} {prod}", int(vol * rnd.uniform(0.05, 0.3))))
    rows.append(("acme", 50_000))
    return pd.DataFrame(rows, columns=["keyword", "volume"])


def _intents_xlsx(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Intent categories"
    ws.append(["#", "Phase", "Intent", "Captures", "Signals", "Example 1", "Example 2", "Example 3"])
    for n, phase, name, captures, signals in INTENTS:
        ws.append([n, phase, name, captures, signals, f"{name} q1", f"{name} q2", f"{name} q3"])
    wb.save(path)


@pytest.fixture(autouse=True)
def no_local_config(monkeypatch, tmp_path):
    """Los tests no leen el config.local.json personal (AWS, MFA…) de quien los ejecuta."""
    monkeypatch.setattr(common, "LOCAL_CONFIG", tmp_path / "no-local-config.json")


@pytest.fixture(scope="session")
def inputs(tmp_path_factory):
    """Ficheros de entrada sintéticos (árbol, keywords con volumen, intents)."""
    d = tmp_path_factory.mktemp("inputs")
    pd.DataFrame({"breadcrumb": ["Home > " + b for b in TREE]}).to_excel(d / "tree.xlsx", index=False)
    _keywords().to_csv(d / "keywords.csv", index=False)
    _intents_xlsx(d / "intents.xlsx")
    return d


@pytest.fixture(scope="session")
def run_config(inputs, tmp_path_factory):
    """config de una ejecución mock sobre Beverages, con rutas absolutas a los datos sintéticos."""
    cfg = {
        "inputs": {
            "tree": str(inputs / "tree.xlsx"),
            "keywords": str(inputs / "keywords.csv"),
            "intents": str(inputs / "intents.xlsx"),
        },
        "scope": {"categories_regex": "^Grocery and gourmet food > Beverages$"},
        "llm": {"provider": "mock", "sleep_between_calls": 0},
        "selection": {"n_intents": 3, "questions_per_intent": 2},
    }
    p = tmp_path_factory.mktemp("cfg") / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


@pytest.fixture(scope="session")
def pipeline_out(run_config, tmp_path_factory):
    """Ejecuta los 9 pasos en el mismo proceso (para medir coverage) y devuelve la carpeta de salida."""
    out = tmp_path_factory.mktemp("out")
    args = ["--config", str(run_config), "--out", str(out)]
    for i, step in enumerate(
        [s1_universe, s2_classify, s3_weight, s4_focus, s5_groups, s6_select, s7_generate, s8_check_judge, s9_output],
        1,
    ):
        run.status(out, step=i, name=step.STEP, state="running")
        step.main(args)
    return out
