"""App web del pipeline: subir entradas, elegir parámetros, lanzar y descargar resultados.

  uv run rufus-app                 → http://localhost:8000
  uv run rufus-app --port 8001

Cada ejecución vive en runs/<id>/ (entradas, config.json, out/, log.txt). En modo "claude" la ejecución se para en
cada paso con IA hasta que Claude responda los prompts de runs/<id>/out/manual/pending; luego se pulsa "Continuar".
"""

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from rufus_queries.app import catmap
from rufus_queries.app.assets import asset
from rufus_queries.app.universe_svg import UNI_CSS, build_svg
from rufus_queries.lib.common import ROOT
from rufus_queries.lib.intents import load_intents
from rufus_queries.steps.s1_universe import load_tree

RUNS = ROOT / "runs"
RUNS.mkdir(exist_ok=True)
DEFAULT = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
app = FastAPI(title="Rufus Query Builder")
_CATS = []
_MAP = []
E = html.escape  # pylint: disable=invalid-name  # abreviatura usada en todas las plantillas

STEPS = [
    ("s1_universe", "Universe", "Amazon tree, flattened"),
    ("s2_classify", "Classify", "Keyword → category → leaf"),
    ("s3_weight", "Weight", "Search volume per leaf"),
    ("s4_focus", "Focus", "Leaves with 80% of demand"),
    ("s5_groups", "Groups", "Same need, other words"),
    ("s6_select", "Select", "Intents and briefs"),
    ("s7_generate", "Generate", "3 candidates per brief"),
    ("s8_check_judge", "Judge", "Checks and choice"),
    ("s9_output", "Output", "Questions and Excel"),
]
AI_STEPS = {"s2_classify", "s4_focus", "s5_groups", "s7_generate", "s8_check_judge"}
STATE_LABEL = {
    "starting": "Starting",
    "running": "Running",
    "waiting": "Waiting for Claude",
    "done": "Done",
    "error": "Error",
}

CSS = asset("app.css")

JS_FORM = asset("form.js")


def page(title, body, active="new", refresh=False):
    """Página HTML completa con cabecera y navegación."""
    meta = '<meta http-equiv="refresh" content="4">' if refresh else ""
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<link rel='icon' type='image/png' href='/static/favicon.png'>"
        "<link rel='preconnect' href='https://fonts.googleapis.com'>"
        "<link href='https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;600;700&display=swap' rel='stylesheet'>"
        f"{meta}<title>{E(title)} · Rufus Query Builder</title><style>{CSS}{UNI_CSS}{catmap.CSS}</style>"
        "<script src='/static/d3-hierarchy.min.js'></script></head><body>"
        "<header><div class='bar'><a class='logo' href='/'><img src='/static/shalion_logo_green.png' alt='Shalion'>"
        "<span class='sep'></span><small>Rufus Query Builder</small></a>"
        f"<nav><a href='/' class='{'on' if active == 'new' else ''}'>New run</a>"
        f"<a href='/runs' class='{'on' if active == 'runs' else ''}'>Runs</a></nav></div></header>"
        f"<main>{body}</main></body></html>"
    )


def categories():
    """Las categorías (Sector > Category) del árbol por defecto, para el selector."""
    if not _CATS:
        tree = Path(DEFAULT["inputs"]["tree"])
        tree = tree if tree.is_absolute() else (ROOT / tree).resolve()
        try:
            lv = load_tree(tree)
            _CATS.extend(sorted(lv.category.unique()))
            _MAP.extend(catmap.rows_from_tree(lv))
        except (OSError, ValueError, KeyError) as e:
            print("No se pudo leer el árbol para el selector:", e)
    return _CATS


def cat_row(c, checked=False):
    """Fila del selector de categorías."""
    sec, _, cat = c.partition(" > ")
    return (
        f"<label class='ci'><input type='checkbox' name='categories' value='{E(c)}'{' checked' if checked else ''}>"
        f"<span>{E(sec)} ›</span> <b>{E(cat)}</b></label>"
    )


def run_status(rid):
    """Estado de una ejecución (runs/<id>/out/_status.json)."""
    p = RUNS / rid / "out" / "_status.json"
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else {"state": "starting"}
    except json.JSONDecodeError:
        return {"state": "running"}


def run_scope(rid):
    """Texto con las categorías de una ejecución."""
    try:
        cfg = json.loads((RUNS / rid / "config.json").read_text(encoding="utf-8"))
        rx = (cfg.get("scope") or {}).get("categories_regex") or ""
        prov = (cfg.get("llm") or {}).get("provider", "")
    except (OSError, ValueError):
        return "—", ""
    names = re.sub(r"^\^\(|\)\$$|^\^|\$$", "", rx).replace("\\", "").split("|") if rx else ["All categories"]
    short = [n.split(" > ")[-1] for n in names]
    return (", ".join(short[:3]) + (f" +{len(short) - 3}" if len(short) > 3 else "")), prov


def fmt_rid(rid):
    """Fecha legible a partir del id de ejecución."""
    m = re.match(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})", rid)
    return f"{m.group(3)}/{m.group(2)} {m.group(4)}:{m.group(5)}" if m else rid


def pill(state):
    """Etiqueta de color con el estado."""
    return f"<span class='pill {E(state)}'>{STATE_LABEL.get(state, state)}</span>"


def runs_list(limit=None):
    """Lista HTML de las ejecuciones, de la más reciente a la más antigua."""
    runs = [r for r in sorted(RUNS.iterdir(), reverse=True) if r.is_dir() and not r.name.startswith("_")]
    if limit:
        runs = runs[:limit]
    if not runs:
        return "<p class='empty'>No runs yet.</p>"
    out = []
    for r in runs:
        scope, prov = run_scope(r.name)
        st = run_status(r.name)
        out.append(
            f"<a class='run' href='/runs/{r.name}'><div class='meta'><b>{E(scope)}</b>"
            f"<span>{fmt_rid(r.name)} · {E(prov)}</span></div>{pill(st.get('state', ''))}</a>"
        )
    return "<div class='runs'>" + "".join(out) + "</div>"


def launch(rid, start=1):
    """Lanza run.py en segundo plano desde el paso start; la salida va a runs/<id>/log.txt."""
    d = RUNS / rid
    with open(d / "log.txt", "a", encoding="utf-8") as log:  # el proceso hijo hereda su propia copia
        log.write(f"\n--- lanzado {time.strftime('%H:%M:%S')} desde el paso {start} ---\n")
        log.flush()
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}  # Windows escribe en cp1252 si no
        subprocess.Popen(  # pylint: disable=consider-using-with  # proceso en segundo plano, no se espera
            [
                sys.executable,
                "-u",
                "-m",
                "rufus_queries.run",
                "--from",
                str(start),
                "--config",
                str(d / "config.json"),
                "--out",
                str(d / "out"),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )


# ── páginas ──────────────────────────────────────────────────────────────


@app.get("/")
def index():
    """Formulario de nueva ejecución."""
    s, f = DEFAULT["selection"], DEFAULT["focus"]
    cur = DEFAULT["scope"].get("categories_regex") or ""
    opts = "".join(cat_row(c, bool(cur and re.search(cur, c))) for c in categories())
    inp = DEFAULT["inputs"]
    body = f"""
<h1>New run</h1>
<p class="sub">Generate Rufus questions for the categories you choose, from the Amazon tree and real search volume.</p>
<div class="grid"><div>
<form action="/run" method="post" enctype="multipart/form-data">

<div class="card"><h2><span class="n">1</span>Data</h2><p class="desc">If you upload nothing, the files in data/raw are used.</p>
<label>Category tree (breadcrumbs)</label>
<div class="file"><span class="ic">XLSX</span><span class="fname" id="tree_n">No file: the default tree is used</span>
<label class="btn ghost" style="margin:0" for="tree">Choose file</label><input type="file" name="tree" id="tree" accept=".xlsx,.xlsm,.csv">
<button type="button" class="btn ghost" id="loadcats">Load categories</button></div>
<div class="default">Default: {E(Path(inp["tree"]).name)} · <span id="catmsg"></span></div>
<label>Keywords with search volume</label>
<div class="file"><span class="ic">XLSX</span><span class="fname" id="kw_n">No file: the default keywords are used</span>
<label class="btn ghost" style="margin:0" for="kw">Choose file</label><input type="file" name="keywords" id="kw" accept=".xlsx,.xlsm,.csv"></div>
<div class="default">Default: {E(Path(inp["keywords"]).name)} · columns keyword and volume</div>
<div class="row2"><div><label>Volume period</label><input type="text" name="volume_period" value="{E(inp.get("volume_period", ""))}" placeholder="2026-09"></div><div></div></div>
</div>

<div class="card"><h2><span class="n">2</span>Categories</h2><p class="desc">Search and click to choose one or more. Each category gets its own question set.</p>
<div class="catbox"><div class="cattop"><input type="text" id="catq" placeholder="Search categories…">
<span class="cnt" id="catcnt"></span><button type="button" id="catall">Select all</button><button type="button" id="catclear">Clear</button></div>
<div class="catlist" id="catlist">{opts}</div></div>
<div class="chips" id="chips"></div>
<details><summary>Advanced: regular expression</summary>
<input type="text" name="categories_regex" placeholder="^Grocery and gourmet food > Beverages$">
<div class="hint">Only if you do not pick from the list. No quotes, format "Sector &gt; Category".</div></details>
</div>

<div class="card"><h2>Map of your selection</h2><p class="desc" style="margin-left:0">Only the part of the Amazon tree you have chosen, grouped by sector. Click to go down.</p>
<div id="selmap"></div></div>

<div class="card"><h2><span class="n">3</span>Questions</h2><p class="desc">Intents are chosen by demand; generic searches (G) compete as one more intent.</p>
<div class="row">
<div><label>Number of intents</label><input type="number" id="ni" name="n_intents" min="1" max="16" value="{s["n_intents"]}"></div>
<div><label>Questions per intent</label><input type="number" id="qpi" name="questions_per_intent" min="1" max="15" value="{s["questions_per_intent"]}"></div>
<div><label>Generic (G) as an intent</label><select name="generic_as_intent"><option value="1">Yes</option><option value="0">No</option></select></div>
</div>
<label>Per-intent adjustment (optional)</label><input type="text" id="ov" name="overrides" placeholder="G=3, I04=6">
<div class="total" id="tot"></div>
</div>

<div class="card"><h2><span class="n">4</span>Run settings</h2><p class="desc">Demand focus and the AI that handles the language steps.</p>
<div class="row">
<div><label>Focus threshold</label><input type="number" step="0.01" min="0.5" max="1" name="threshold" value="{f["threshold"]}"></div>
<div><label>Minimum per leaf (%)</label><input type="number" step="0.1" name="min_leaf_share" placeholder="optional"></div>
<div><label>Keyword sample</label><input type="number" name="sample" placeholder="all"></div>
</div>
<label>AI</label>
<div class="radios">
<label class="radio"><input type="radio" name="provider" value="claude" checked><b>Claude</b><span>Pauses at each AI step until Claude answers. Without a sample it uses the 5,000 most searched keywords.</span></label>
<label class="radio"><input type="radio" name="provider" value="mock"><b>Test</b><span>Simulated answers to check that everything works. The questions are not meaningful.</span></label>
<label class="radio"><input type="radio" name="provider" value="bedrock"><b>Bedrock</b><span>Real calls to AWS. First run <code>uv run rufus-aws-login</code> in a terminal (MFA, lasts 12 h).</span></label>
</div>
</div>

<div class="submit"><button type="submit" class="btn primary">Launch run →</button></div>
</form></div>
<aside><div class="card"><h2>Latest runs</h2>{runs_list(8)}
<p style="margin:12px 0 0"><a href="/runs" class="hint">See all →</a></p></div></aside>
</div>
<script>{catmap.JS}</script><script>window.MAPROWS={json.dumps(_MAP if categories() else [], ensure_ascii=False)};</script><script>{JS_FORM}</script>"""
    return page("New run", body, "new")


@app.get("/static/{name}")
def static(name: str):
    """Ficheros estáticos de la app (logos, d3)."""
    p = Path(__file__).resolve().parent / "static" / Path(name).name
    if not p.exists():
        raise HTTPException(status_code=404)
    return FileResponse(p)


@app.get("/runs")
def all_runs():
    """Página con todas las ejecuciones."""
    return page(
        "Runs", f"<h1>Runs</h1><p class='sub'>All runs in this folder.</p><div class='card'>{runs_list()}</div>", "runs"
    )


@app.post("/categories")
async def tree_categories(tree: UploadFile = File(...)):
    """Lee un árbol subido y devuelve sus categorías (Sector > Category) para el selector."""

    tmp = RUNS / "_tmp_tree"
    tmp.mkdir(exist_ok=True)
    p = tmp / Path(tree.filename or "tree.xlsx").name
    with open(p, "wb") as fh:
        shutil.copyfileobj(tree.file, fh)
    try:
        leaves = load_tree(p)
    except Exception as e:  # pylint: disable=broad-exception-caught  # cualquier fallo de lectura → 400 con el motivo
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        p.unlink(missing_ok=True)
    return {
        "categories": sorted(leaves.category.unique().tolist()),
        "leaves": len(leaves),
        "map": catmap.rows_from_tree(leaves),
    }


@app.post("/run")
async def run(
    tree: UploadFile = File(None),
    keywords: UploadFile = File(None),
    volume_period: str = Form(""),
    categories: list[str] = Form([]),  # pylint: disable=redefined-outer-name  # nombre del campo del formulario
    categories_regex: str = Form(""),
    n_intents: int = Form(5),
    questions_per_intent: int = Form(5),
    generic_as_intent: str = Form("1"),
    overrides: str = Form(""),
    threshold: float = Form(0.8),
    min_leaf_share: str = Form(""),
    sample: str = Form(""),
    provider: str = Form("claude"),
):
    """Crea una ejecución con los ficheros y parámetros del formulario y la lanza."""
    rx = categories_regex.strip().strip('"').strip("'").strip()
    if categories:
        rx = "^(" + "|".join(re.escape(c) for c in categories) + ")$"
    if provider == "claude" and not sample:
        sample = "5000"
    rid = time.strftime("%Y%m%d-%H%M%S")
    d = RUNS / rid
    (d / "inputs").mkdir(parents=True)
    cfg = {
        "inputs": {},
        "scope": {"categories_regex": rx or None},
        "selection": {
            "n_intents": n_intents,
            "questions_per_intent": questions_per_intent,
            "generic_as_intent": generic_as_intent == "1",
            "questions_per_intent_override": {},
        },
        "focus": {"threshold": threshold, "min_leaf_share": float(min_leaf_share) if min_leaf_share else None},
        "classify": {"sample": int(sample) if sample else None},
        "llm": {"provider": provider},
    }
    for up, key in ((tree, "tree"), (keywords, "keywords")):
        if up is not None and up.filename:
            p = d / "inputs" / Path(up.filename).name
            with open(p, "wb") as fh:
                shutil.copyfileobj(up.file, fh)
            cfg["inputs"][key] = str(p)
    if volume_period:
        cfg["inputs"]["volume_period"] = volume_period
    for part in overrides.replace(";", ",").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if v.strip().isdigit():
                cfg["selection"]["questions_per_intent_override"][k.strip().upper()] = int(v)
    (d / "config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    launch(rid)
    return RedirectResponse(f"/runs/{rid}", status_code=303)


@app.get("/runs/{rid}")
def show(rid: str):
    """Página de una ejecución: progreso, universo, intents, preguntas y log."""
    d = RUNS / rid
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="Run not found")
    st = run_status(rid)
    state = st.get("state", "starting")
    cur = int(st.get("step", 1) or 1)
    scope, prov = run_scope(rid)
    log = (d / "log.txt").read_text(encoding="utf-8", errors="replace")[-8000:] if (d / "log.txt").exists() else ""

    steps = []
    for i, (key, name, desc) in enumerate(STEPS, 1):
        if state == "done" or i < cur:
            cls = "done"
        elif i == cur:
            cls = {"waiting": "waiting", "error": "error"}.get(state, "current")
        else:
            cls = ""
        ai = "<span class='ai'>AI</span>" if key in AI_STEPS else ""
        steps.append(f"<div class='step {cls}' data-i='{i}'><div class='dot'></div><b>{i}. {name}{ai}</b>{desc}</div>")

    banner = ""
    if state == "waiting":
        pend = d / "out" / "manual" / "pending"
        banner = (
            f"<div class='banner waiting'><p><b>Waiting for Claude.</b> Step {cur} ({E(STEPS[cur - 1][1])}) "
            f"left {st.get('pending', '')} prompts in <code>{E(str(pend))}</code>. Ask Claude to answer them, then "
            f"click Continue.</p><form method='post' action='/runs/{rid}/resume'>"
            f"<button class='btn primary'>Continue</button></form></div>"
        )
    elif state == "error":
        why = next((ln.strip() for ln in reversed(log.splitlines()) if ln.strip().startswith("ERROR")), "")
        hint = next((ln.strip() for ln in reversed(log.splitlines()) if ln.strip().startswith("Stopping")), "")
        banner = (
            f"<div class='banner error'><p><b>The run stopped with an error at step {cur} "
            f"({E(STEPS[cur - 1][1])}).</b> "
            + (f"<br><code style='color:#FF9AA8'>{E(why[:300])}</code>" if why and why != hint else "")
            + (f"<br>{E(hint)}" if hint else " See the log below.")
            + "</p>"
            f"<form method='post' action='/runs/{rid}/resume'><button class='btn ghost'>Retry step {cur}</button></form></div>"
        )

    universe = universe_card(d)
    results = ""
    fq = d / "out" / "s9_output" / "final_questions.csv"
    if state == "done" and fq.exists():
        q = pd.read_csv(fq, keep_default_na=False)
        jm = pd.to_numeric(q.judge, errors="coerce").mean()
        kpis = (
            f"<div class='kpis'><div class='kpi'><span>Questions</span><b>{len(q)}</b></div>"
            f"<div class='kpi'><span>Categories</span><b>{q.category.nunique()}</b></div>"
            f"<div class='kpi'><span>Distinct intents</span><b>{q.intent.nunique()}</b></div>"
            f"<div class='kpi'><span>Average judge score</span><b>{jm:.2f}</b></div></div>"
        )
        tables = []
        badges, qcodes = question_links(d)
        merges = merges_of(d)
        why = {(c, "+".join(codes)): w for c, lst in merges.items() for codes, w in lst}
        lwp, fop = d / "out" / "s3_weight" / "leaf_weights.csv", d / "out" / "s4_focus" / "focus.csv"
        lw = pd.read_csv(lwp, keep_default_na=False) if lwp.exists() else None
        fo = pd.read_csv(fop, keep_default_na=False) if fop.exists() else None
        for cat, g in q.groupby("category", sort=False):
            mx = pd.to_numeric(g.weight_pct_category, errors="coerce").max() or 1
            mini = ""
            if lw is not None and not lw[lw.category == cat].empty:
                fc = (
                    set()
                    if fo is None
                    else set(fo[(fo.category == cat) & (fo.in_focus.astype(str).str.lower() == "true")].code)
                )
                mini = (
                    f"<div class='qside'><div class='qmini'>{build_svg(lw[lw.category == cat], fc, max_labels=0, cat_full=cat, badges=badges.get(cat), merges=merges.get(cat))}"
                    f"<p class='hint'>Hover a question to light up its leaf. Badge = questions per leaf.</p></div></div>"
                )
            rows = "".join(
                f"<tr class='qrow' data-cat='{E(cat)}' data-codes='{E(qcodes.get((cat, r.id), ''))}'>"
                f"<td><span class='it {E(r.intent)}'>{E(r.intent_name)}</span></td><td>{E(r.unit)}"
                f"{sib(why.get((cat, qcodes.get((cat, r.id), ''))))}</td>"
                f"<td class='q'>{E(r.question)}</td><td style='white-space:nowrap'><span class='bar-w'>"
                f"<i style='width:{min(100, float(r.weight_pct_category) / mx * 100):.0f}%'></i></span>"
                f"{float(r.weight_pct_category):.2f}%</td></tr>"
                for r in g.itertuples()
            )
            tables.append(
                f"<div class='catblock' data-cat='{E(cat)}'><h3 class='cathead'>{E(cat)}</h3><div class='qgrid'><div><table>"
                f"<tr><th>Intent</th><th>Leaf</th><th>Question</th><th>Weight</th></tr>{rows}</table></div>{mini}</div></div>"
            )
        results = (
            kpis
            + universe
            + intents_card(d)
            + "<div class='card'><div style='display:flex;align-items:center;gap:12px'>"
            "<h2 style='margin:0'>Questions</h2><span class='scopelbl'></span><span style='margin-left:auto'></span>"
            f"<a class='btn primary' href='/runs/{rid}/results.xlsx'>Download Excel</a></div>"
            + "".join(tables)
            + "<p class='hint'>Weight = share of the category's demand that each question represents.</p></div>"
        )

    body = (
        f"<p class='hint'><a href='/runs'>← Runs</a></p>"
        f"<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap'><h1>{E(scope)}</h1>{pill(state)}</div>"
        f"<p class='sub'>Run {fmt_rid(rid)} · AI: {E(prov)} · id {E(rid)}</p>"
        f"<div class='card'><div class='steps'>{''.join(steps)}</div>{live_box(state)}</div>{banner}{results or universe}"
        f"<div class='card'><details {'open' if state in ('error', 'running', 'starting') else ''}>"
        f"<summary><b>Log</b></summary><pre id='log'>{E(log)}</pre></details></div>"
        + (UNI_JS if universe else "")
        + LIVE_JS.replace("__RID__", rid).replace("__STATE__", state).replace("__SCOPE__", E(scope).replace("'", ""))
    )
    return page(scope, body, "runs")


def live_box(state):
    """Barra de progreso del paso en curso + botón de aviso (solo mientras corre)."""
    if state not in ("running", "starting"):
        return ""
    return (
        "<div class='live' id='live'><div class='live-top'><span class='spin'></span><b id='lv-label'>Starting…</b>"
        "<span id='lv-count' class='lv-muted'></span><span class='lv-sp'></span><span id='lv-eta' class='lv-muted'></span>"
        "<span id='lv-el' class='lv-muted'></span><button type='button' class='btn ghost bell' id='bell'>🔔 Notify me when it finishes</button></div>"
        "<div class='lv-bar'><i id='lv-fill'></i></div></div>"
    )


LIVE_JS = asset("live.js")


@app.get("/runs/{rid}/live")
def live(rid: str):
    """Estado en vivo de una ejecución (lo consulta la página cada 2 s)."""
    if not re.fullmatch(r"[\w-]+", rid) or not (RUNS / rid).is_dir():
        raise HTTPException(404)
    d = RUNS / rid
    st = run_status(rid)
    prog = None
    pp = d / "out" / "_progress.json"
    if pp.exists():
        try:
            prog = json.loads(pp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prog = None
    step = int(st.get("step", 1) or 1)
    try:
        t0 = time.mktime(time.strptime(rid[:15], "%Y%m%d-%H%M%S"))
    except ValueError:
        t0 = time.time()
    now = time.time()
    log = (d / "log.txt").read_text(encoding="utf-8", errors="replace")[-8000:] if (d / "log.txt").exists() else ""
    nq = 0
    fq = d / "out" / "s9_output" / "final_questions.csv"
    if st.get("state") == "done" and fq.exists():
        with open(fq, encoding="utf-8", errors="replace") as fh:
            nq = max(0, sum(1 for _ in fh) - 1)
    return {
        "state": st.get("state", "starting"),
        "step": step,
        "step_name": STEPS[step - 1][1] if 0 < step <= len(STEPS) else "",
        "pending": st.get("pending", 0),
        "progress": prog,
        "elapsed": now - t0,
        "step_elapsed": now - float(st.get("step_started", now)),
        "log": log,
        "questions": nq,
    }


def sib(reason):
    """Etiqueta 'siblings' con el motivo de la fusión (vacía si no hay)."""
    return f"<span class='sibchip' title='Merged siblings: {E(reason)}'>siblings</span>" if reason else ""


def merges_of(d: Path) -> dict:
    """{categoría: [([códigos], motivo), ...]} hojas hermanas fusionadas en el paso 4."""

    mp = d / "out" / "s4_focus" / "merges.csv"
    if not mp.exists():
        return {}
    mg = pd.read_csv(mp, keep_default_na=False)
    out = {}
    for r in mg.itertuples():
        out.setdefault(r.category, []).append((r.codes.split("+"), r.reason))
    return out


def question_links(d: Path):
    """{categoría: {código de hoja: nº de preguntas}} y, por pregunta (id), los códigos de hoja de su unidad."""

    o = d / "out"
    fq, up = o / "s9_output" / "final_questions.csv", o / "s4_focus" / "units.csv"
    if not fq.exists() or not up.exists():
        return {}, {}
    q = pd.read_csv(fq, keep_default_na=False)
    u = pd.read_csv(up, keep_default_na=False)
    full = {}
    mp = o / "s4_focus" / "merges.csv"
    if mp.exists():
        mg = pd.read_csv(mp, keep_default_na=False)
        full = {(r.category, r.codes.split("+")[0]): r.codes for r in mg.itertuples()}
    uid = {(r.category, r.unit_name): full.get((r.category, r.unit_id), r.unit_id) for r in u.itertuples()}
    badges, codes = {}, {}
    for r in q.itertuples():
        c = uid.get((r.category, r.unit), "")
        codes[(r.category, r.id)] = c
        for x in filter(None, c.split("+")):
            badges.setdefault(r.category, {})[x] = badges.get(r.category, {}).get(x, 0) + 1
    return badges, codes


def run_map_card(d: Path) -> str:
    """Subárbol de las categorías de la ejecución con su volumen real (con una sola, abre directamente sus hojas)."""

    lwp, fop = d / "out" / "s3_weight" / "leaf_weights.csv", d / "out" / "s4_focus" / "focus.csv"
    if not lwp.exists():
        return ""
    lw = pd.read_csv(lwp, keep_default_na=False)
    focus = set()
    if fop.exists():
        fo = pd.read_csv(fop, keep_default_na=False)
        focus = {
            (c, k)
            for c, k, f in zip(fo.category, fo.code, fo.in_focus.astype(str).str.lower(), strict=False)
            if f == "true"
        }
    rows = catmap.rows_from_run(lw, focus)
    secs = sorted({r["s"] for r in (_MAP if categories() else [])})
    return (
        "<div id='runmap' class='breakdown' style='display:none'></div>"
        f"<script>{catmap.JS}</script><script>window.RUNMAP={json.dumps(rows, ensure_ascii=False)};"
        f"window.RUNSECS={json.dumps(secs, ensure_ascii=False)};</script>"
    )


def intents_card(d: Path) -> str:
    """Demanda de cada intent frente a las preguntas que ha recibido (huecos vacíos = cedidos al siguiente)."""

    o = d / "out"
    rp, fq = o / "s6_select" / "intent_ranking.csv", o / "s9_output" / "final_questions.csv"
    if not rp.exists() or not fq.exists():
        return ""
    ir = pd.read_csv(rp, keep_default_na=False)
    ir["share_pct"] = pd.to_numeric(ir.share_pct, errors="coerce").fillna(0)
    q = pd.read_csv(fq, keep_default_na=False)
    try:
        sc = json.loads((d / "config.json").read_text(encoding="utf-8")).get("selection", {})
    except (OSError, ValueError):
        sc = {}
    per = int(sc.get("questions_per_intent", 5))
    over = sc.get("questions_per_intent_override", {}) or {}
    cats = list(dict.fromkeys(ir.category))
    blocks = []
    for cat in cats:
        g = ir[ir.category == cat].sort_values("rank")
        n_by = q[q.category == cat].groupby("intent").size()
        top = max(g.share_pct.max(), 1)
        rows, given, filled = "", 0, 0
        for r in g.itertuples():
            n = int(n_by.get(r.intent, 0))
            st = str(r.chosen)
            if r.share_pct < 0.1 and n == 0:
                continue
            target = int(over.get(r.intent, per)) if st == "True" else 0
            label = {"True": "chosen", "filler": "filler · got the free slots"}.get(st, "not chosen")
            if st == "True" and n < target:
                label = f"chosen · {target - n} slot{'s' if target - n != 1 else ''} passed on"
                given += target - n
            if st == "filler":
                filled += n
            sq = "<u></u>" * n + ("<s></s>" * max(0, target - n))
            rows += (
                f"<div class='ir {'' if st in ('True', 'filler') else 'off'}'><div class='irn'><span class='it {E(r.intent)}'>"
                f"{E(r.intent_name)}</span><small>{E(label)}</small></div>"
                f"<div class='irb'><div class='l1'><i class='c-{E(r.intent)}' style='width:{r.share_pct / top * 100:.1f}%'></i></div>"
                f"<em>{r.share_pct:.1f}%</em></div><div class='irq'>{sq}<em>{n}</em></div></div>"
            )
        head = "<div class='ir ir-h'><div>Intent</div><div>Share of demand</div><div>Questions</div></div>"
        note = (
            f"<p class='hint'>{given} slot{'s' if given != 1 else ''} could not be filled with enough demand and "
            f"went to the next intent in the ranking.</p>"
            if given
            else ""
        )
        title = f"<h3 class='cathead'>{E(cat)}</h3>" if len(cats) > 1 else ""
        blocks.append(
            f"<div class='catblock' data-cat='{E(cat)}'>{title}<div class='irwrap'>{head}{rows}</div>{note}</div>"
        )
    return (
        "<div class='card'><h2 style='margin-bottom:4px'>Demand vs questions per intent</h2>"
        "<p class='hint' style='margin:0 0 14px'>Bar: share of the category's demand. Squares: questions received; "
        "dashed squares: slots the intent could not fill.</p>" + "".join(blocks) + "</div>"
    )


def universe_card(d: Path) -> str:
    """Tarjeta con el universo 3D de cada categoría (si ya existen los pasos 3 y 4)."""
    lwp, fop = d / "out" / "s3_weight" / "leaf_weights.csv", d / "out" / "s4_focus" / "focus.csv"
    if not lwp.exists():
        return ""

    lw = pd.read_csv(lwp, keep_default_na=False)
    focus = set()
    if fop.exists():
        fo = pd.read_csv(fop, keep_default_na=False)
        focus = {
            (c, k)
            for c, k, f in zip(fo.category, fo.code, fo.in_focus.astype(str).str.lower(), strict=False)
            if f == "true"
        }
    cats = list(dict.fromkeys(lw.category))
    badges, _ = question_links(d)
    merges = merges_of(d)
    panes, tabs = [], []
    for i, c in enumerate(cats):
        g = lw[lw.category == c]
        if g.empty:
            continue
        fc = {k for (cc, k) in focus if cc == c}
        panes.append(
            f"<div class='unipane' data-i='{i}' style='{'' if i == 0 else 'display:none'}'>"
            f"{build_svg(g, fc, cat_full=c, badges=badges.get(c), merges=merges.get(c))}</div>"
        )
        tabs.append(
            f"<button type='button' class='{'on' if i == 0 else ''}' data-i='{i}' data-cat='{E(c)}'>{E(c.split(' > ')[-1])}</button>"
        )
    tabbar = f"<div class='unitabs'>{''.join(tabs)}</div>" if len(tabs) > 1 else ""
    js = (
        "<script>document.querySelectorAll('.unitabs button').forEach(b=>b.onclick=()=>{"
        "document.querySelectorAll('.unitabs button').forEach(x=>x.classList.toggle('on',x===b));"
        "document.querySelectorAll('.unipane').forEach(p=>p.style.display=p.dataset.i===b.dataset.i?'':'none');});</script>"
    )
    bd = run_map_card(d)
    btn = "<button type='button' class='btn ghost bdbtn' id='bdbtn'>▦ Break down</button>" if bd else ""
    return (
        f"<div class='card'><div style='display:flex;align-items:center;gap:12px;margin-bottom:12px'>"
        f"<h2 style='margin:0'>Universe and demand</h2><span class='scopelbl'></span><span style='margin-left:auto'></span>{btn}</div>"
        f"{bd}{tabbar}{''.join(panes)}{js}</div>"
    )


UNI_JS = asset("universe.js")


@app.get("/runs/{rid}/leaf")
def leaf_keywords(rid: str, cat: str, code: str, max_parents: int = 50):
    """Keywords de una hoja (para el panel que se abre al clicar una barra del universo)."""

    if not re.fullmatch(r"[\w-]+", rid):
        raise HTTPException(404)
    o = RUNS / rid / "out"
    kp, lp = o / "s3_weight" / "classified_norm.csv", o / "s3_weight" / "leaf_weights.csv"
    if not kp.exists() or not lp.exists():
        raise HTTPException(404, "This run has no weights yet")
    k = pd.read_csv(kp, keep_default_na=False)
    lw = pd.read_csv(lp, keep_default_na=False)
    row = lw[(lw.category == cat) & (lw.code == code)]
    if row.empty:
        raise HTTPException(404, "Leaf not found")
    row = row.iloc[0]
    names = {"G": "Generic (product type)"}
    rp = o / "s6_select" / "intent_ranking.csv"
    if rp.exists():
        ir = pd.read_csv(rp, keep_default_na=False)
        names.update(dict(zip(ir.intent, ir.intent_name, strict=False)))
    try:
        cfg = json.loads((RUNS / rid / "config.json").read_text(encoding="utf-8"))
        ip = Path(cfg.get("inputs", {}).get("intents") or DEFAULT["inputs"]["intents"])
        ip = ip if ip.is_absolute() else (ROOT / ip).resolve()
        for it in load_intents(ip):
            names.setdefault(it.code, it.name)
    except (OSError, ValueError, KeyError):
        pass  # sin Excel de intents: se quedan los nombres del ranking
    k["volume"] = pd.to_numeric(k.volume, errors="coerce").fillna(0)
    kc = k[k.category == cat]
    mine = kc[kc.code == code].sort_values("volume", ascending=False)

    def kname(r):
        if r.kw_type == "brand_only":
            return "Brand only"
        return names.get(r.intent, r.intent or "—")

    kws = [
        {
            "keyword": r.keyword,
            "volume": float(r.volume),
            "intent": ("BR" if r.kw_type == "brand_only" else r.intent),
            "intent_name": kname(r),
            "need": r.need,
            "brand": r.brand_family or r.brand,
        }
        for r in mine.itertuples()
    ]

    # palabras clave clasificadas en nodos padre (P..): parte que recibe esta hoja, igual que en s3
    parents = []
    path = row.node_path
    own = kc[kc.code.str.startswith("L")].groupby("code").volume.sum()
    leaves = lw[lw.category == cat]
    pk = kc[kc.code.str.startswith("P")]
    for p, g in pk.groupby("node_path"):
        if not path.startswith(p + " > "):
            continue
        kids = leaves[leaves.node_path.str.startswith(p + " > ")].code.tolist()
        base = own.reindex(kids).fillna(0)
        share = (base[code] / base.sum()) if base.sum() > 0 else 1 / len(kids)
        if share <= 0:
            continue
        node = " > ".join(p.split(" > ")[2:]) or p
        parents += [
            {"keyword": r.keyword, "volume": float(r.volume), "share": float(r.volume * share), "node": node}
            for r in g.itertuples()
        ]
    parents = sorted(parents, key=lambda d: -d["share"])[:max_parents]
    merged = None
    for codes, why in merges_of(RUNS / rid).get(cat, []):
        if code in codes:
            names_ = dict(zip(leaves.code, leaves.leaf_name, strict=False))
            merged = {"with": " + ".join(names_.get(c, c) for c in codes if c != code), "reason": why}
    return {
        "leaf": row.leaf_name,
        "path": " > ".join(path.split(" > ")[:-1]),
        "total": float(row.total),
        "merged": merged,
        "own": float(row.own_volume),
        "from_nodes": float(row.from_nodes),
        "keywords": kws,
        "parents": parents,
    }


@app.post("/runs/{rid}/resume")
def resume(rid: str):
    """Relanza una ejecución desde el paso en el que se paró."""
    launch(rid, int(run_status(rid).get("step", 1)))
    return RedirectResponse(f"/runs/{rid}", status_code=303)


@app.get("/runs/{rid}/results.xlsx")
def download(rid: str):
    """Descarga results.xlsx de una ejecución."""
    p = RUNS / rid / "out" / "s9_output" / "results.xlsx"
    if not p.exists():
        raise HTTPException(status_code=404, detail="No Excel for this run yet")
    return FileResponse(p, filename=f"rufus_queries_{rid}.xlsx")


def serve(argv=None):
    """Arranca la app con uvicorn (comando rufus-app)."""
    ap = argparse.ArgumentParser(description="App web del pipeline de queries de Rufus.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    uvicorn.run("rufus_queries.app.main:app", host=a.host, port=a.port)
