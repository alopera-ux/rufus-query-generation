# rufus-query-generation

For every Amazon US category, generates the questions a shopper would ask **Rufus / Alexa for Shopping**, chosen and weighted by real demand (keyword search volume).

Output per category: `final_questions.csv` and `results.xlsx`. By default each category gets 25 questions: the 5 intents with the most demand × 5 questions each. Before the questions are written, an AI review fixes or replaces wrong briefs (`review.csv`), and step 9 adds a quality check per category (`quality_summary.csv`, `quality_alerts.csv`; *Calidad*, *Alertas*, *Revisión IA* and *Coste* sheets in the Excel; same checks on the run page of the app).

Full design of the method (in Spanish): [`docs/generacion_queries_rufus.md`](docs/generacion_queries_rufus.md).

## Structure

Based on [cookiecutter-data-science](https://cookiecutter-data-science.drivendata.org/), trimmed to what we use:

```
├─ .github/              CI (ruff, pylint, tests), CODEOWNERS, PR template
├─ config.json           default parameters (scope, focus, selection, AI, AWS without personal data)
├─ config.local.example.json   template for config.local.json (your AWS details; not committed)
├─ data/
│  ├─ raw/               inputs: tree, keywords with volume, intents (not committed)
│  └─ processed/         reserved for shared intermediate outputs
├─ docs/                 design of the method
├─ scripts/              setup_github.py (creates the repo and its rules)
├─ src/rufus_queries/
│  ├─ steps/             s1…s9, one module per step
│  ├─ lib/               config, AI client (bedrock / claude / mock), intents
│  ├─ prompts/           instructions for each AI step
│  ├─ app/               web app (FastAPI) + assets (CSS/JS)
│  ├─ tools/             aws_login and check_bedrock
│  └─ run.py             orchestrator
├─ tests/                unit tests, app, AI with boto3 doubles and the full pipeline in mock mode
├─ runs/  · out/         runs (not committed)
└─ pyproject.toml · uv.lock
```

## Installation

Requirements: [uv](https://docs.astral.sh/uv/). It installs the Python version in `.python-version` by itself.

```bash
uv sync                      # creates .venv with dependencies and dev tools
```

Copy the three input files into `data/raw/` (the file names are in `config.json → inputs`):

| Input | File | Contents |
|---|---|---|
| Tree | `us_2026-09.xlsx` | `breadcrumb` column, one row per Amazon US leaf |
| Keywords | `amazon_us_volume.xlsx` | keyword and volume (top 200k of the month) |
| Intents | `shopper_assistant_intent_categories.xlsx` | *Intent categories* tab with the 15 intents |

## Usage

```bash
uv run rufus-app                         # web app → http://localhost:8000  (--port 8001 if it is busy)
uv run rufus-run                         # all 9 steps from the command line
uv run rufus-run --only 1                # one step
uv run rufus-run --from 3 --to 6         # a range of steps
uv run rufus-run --config my.json --out out/test
uv run rufus-run --config runs/<id>/config.json --out runs/<id>/out --from 6   # redo selection, review, questions
uv run rufus-run --config runs/<id>/config.json --out runs/<id>/out --only 9   # rebuild the Excel and checks (no AI)
```

### AI providers (`llm.provider`)

- **`claude`** (manual): each AI step writes its prompts to `<out>/manual/pending/` and stops with exit code 3. Answers go in `<out>/manual/responses/<same name>.txt`; then continue with `--from <step>` or the **Continue** button in the app.
- **`bedrock`**: real calls, in parallel (`llm.workers`, 8 by default). See below.
- **`mock`**: synthetic answers to check that everything runs; the tests use it.

Every answer is cached (`<out>/cache/`): re-running a step does not repeat calls.

### Setting up Bedrock

1. **IT permission**: `bedrock:InvokeModel` on the models in `llm.models`, in `llm.region`, for the team role. By default every step uses Claude Sonnet 5.5 (`eu.anthropic.claude-sonnet-5-5`) in `eu-west-1`.
2. **Permanent keys**, only in your terminal: `aws configure --profile shalion`.
3. **`config.local.json`**: copy `config.local.example.json` and add your MFA ARN and the team role. Bedrock lives in the team account, not in your user's account: your user only has `ReadOnlyAccess` plus permission to assume `shalion-rmm-team-role`.
4. **MFA session**: `uv run rufus-aws-login`. It asks for the code from 1Password, saves the `shalion-session` profile for 12 h and writes the `shalion-rmm` profile, which assumes the team role from that session (renewed automatically). The account denies calls without MFA.
5. **Check**: `uv run rufus-check-bedrock` makes a minimal call to each model and shows ✓ or ✗ with the reason.
6. **Run**: in the app choose AI **Bedrock**, or set `llm.provider: "bedrock"` in your config.

Rough cost with Sonnet 5.5 and `keyword_leaves`: about $1.5 per category (Beverages: $1.3–1.7, ~5 min with 24 workers), so roughly $200–250 for all categories. Each run writes the real cost to `<out>/usage.csv`.

## Quality and CI

Every PR and every push to `main` runs three checks in GitHub Actions. All three must pass before merging.

| Check | Requirement | Locally |
|---|---|---|
| `ruff` | lint and formatting with no warnings | `uv run ruff check src tests scripts` · `uv run ruff format --check src tests scripts` |
| `pylint` | score ≥ 9.0 and no errors (E/F) | `uv run pylint src tests scripts` |
| `tests` | all green and coverage ≥ 85% | `uv run pytest --cov` |

`make check` runs everything. `uv run pre-commit install` runs ruff and pylint before each commit.

Current status: pylint 9.85, 65 tests, coverage 88%.

Explicit exceptions, in `pyproject.toml`:
- Line length is not checked in the app's HTML templates.
- `fastapi.File`/`Form` are allowed as default values.
- `invalid-name` is disabled in the geometry formulas of the isometric chart.

Known debt: the `main()` functions of some steps (s2, s3, s6) are long; pylint flags them as `too-many-locals`/`too-many-statements`.

## Contributing

1. Branch off `main` with the name `<type>/<area>-<description>`, for example `git switch -c feat/prompts-question-versions`. GitHub rejects the push of branches with any other name.

   | Type | For |
   |---|---|
   | `feat` | something new |
   | `fix` | fixing a bug |
   | `docs` | documentation |
   | `refactor` | restructuring code without changing what it does |
   | `test` | tests |
   | `chore` | maintenance (dependencies, cleanup) |
   | `ci` | continuous integration |

   | Area | What it covers |
   |---|---|
   | `pipeline` | steps s1–s9 and `lib/` |
   | `prompts` | `src/rufus_queries/prompts/` |
   | `app` | the web app |
   | `bedrock` | AWS, Bedrock and their tools |
   | `infra` | repo, CI, uv, scripts |
   | `docs` | README and `docs/` |

   The description is lowercase, with hyphens. The lists live in `scripts/setup_github.py` (`BRANCH_TYPES`, `BRANCH_AREAS`).
2. Make your changes with their tests; run `make check` locally.
3. Open a PR to `main`. Merging needs the three checks green and approval from [@alopera-ux](https://github.com/alopera-ux), set in CODEOWNERS.

The repo and its rules are created with `scripts/setup_github.py`; the steps are described inside the script.

## Known limitations

- Keywords without a clear intent (NX) are not reassigned.
- The questions still need to be validated in Rufus: what it answers and which products it shows.

## Security

AWS keys and `.ovpn` files go only in the terminal or in 1Password, never in the repo. `config.local.json`, `data/raw/` and `runs/` are in `.gitignore`.
