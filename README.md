# rufus-query-generation

Genera, para cada categoría de Amazon US, las preguntas que un comprador le haría a **Rufus / Alexa for Shopping**, elegidas y ponderadas por la demanda real (search volume de las keywords).

Salida por categoría: `final_questions.csv` y `results.xlsx`. Por defecto son 25 preguntas: los 5 intents con más demanda × 5 preguntas cada uno.

Diseño completo del método: [`docs/generacion_queries_rufus.md`](docs/generacion_queries_rufus.md).

## Estructura

Basada en [cookiecutter-data-science](https://cookiecutter-data-science.drivendata.org/), reducida a lo que usamos:

```
├─ .github/              CI (ruff, pylint, tests), CODEOWNERS, plantilla de PR
├─ config.json           parámetros por defecto (alcance, foco, selección, IA, AWS sin datos personales)
├─ config.local.example.json   plantilla de config.local.json (tus datos de AWS; no se sube)
├─ data/
│  ├─ raw/               entradas: árbol, keywords con volumen, intents (no se suben)
│  └─ processed/         reservado para salidas intermedias compartidas
├─ docs/                 diseño del método
├─ scripts/              setup_github.py (crear el repo y sus reglas)
├─ src/rufus_queries/
│  ├─ steps/             s1…s9, un módulo por paso
│  ├─ lib/               config, cliente de IA (bedrock / claude / mock), intents
│  ├─ prompts/           instrucciones de cada paso con IA
│  ├─ app/               app web (FastAPI) + assets (CSS/JS)
│  ├─ tools/             aws_login y check_bedrock
│  └─ run.py             orquestador
├─ tests/                unitarios, app, IA con dobles de boto3 y pipeline completo en modo mock
├─ runs/  · out/         ejecuciones (no se suben)
└─ pyproject.toml · uv.lock
```

## Instalación

Requisitos: [uv](https://docs.astral.sh/uv/) (instala él mismo la versión de Python de `.python-version`).

```bash
uv sync                      # crea .venv con dependencias y herramientas de desarrollo
```

Copia los tres ficheros de entrada en `data/raw/` (los nombres están en `config.json → inputs`):

| Entrada | Fichero | Contenido |
|---|---|---|
| Árbol | `us_2026-09.xlsx` | columna `breadcrumb` por hoja de Amazon US |
| Keywords | `amazon_us_volume.xlsx` | keyword y volumen (top 200k del mes) |
| Intents | `shopper_assistant_intent_categories.xlsx` | pestaña *Intent categories* con los 15 intents |

## Uso

```bash
uv run rufus-app                         # app web → http://localhost:8000  (--port 8001 si está ocupado)
uv run rufus-run                         # los 9 pasos por línea de comandos
uv run rufus-run --only 1                # un paso
uv run rufus-run --from 3 --to 6         # un tramo
uv run rufus-run --config mi.json --out out/prueba
```

### Proveedores de IA (`llm.provider`)

- **`claude`** (manual): cada paso con IA deja sus prompts en `<out>/manual/pending/` y se para con código 3. Se responden en `<out>/manual/responses/<mismo nombre>.txt` y se continúa con `--from <paso>` o con el botón **Continuar** de la app.
- **`bedrock`**: llamadas reales en paralelo (`llm.workers`, 8 por defecto). Ver más abajo.
- **`mock`**: respuestas sintéticas para comprobar que todo funciona; es el que usan los tests.

Todas las respuestas se guardan en caché (`<out>/cache/`): repetir un paso no repite llamadas.

### Poner Bedrock en marcha

1. **Permiso de IT**: `bedrock:InvokeModel` sobre los modelos de `llm.models` en `llm.region`. Por defecto son Nova Lite y Nova Pro en `eu-west-1`.
2. **Claves permanentes**, solo en tu terminal: `aws configure --profile shalion`.
3. **`config.local.json`**: copia `config.local.example.json` y pon el ARN de tu MFA.
4. **Sesión con MFA**: `uv run rufus-aws-login`. Te pide el código de 1Password y guarda el perfil `shalion-session` durante 12 h. La cuenta deniega llamadas sin MFA.
5. **Comprobar**: `uv run rufus-check-bedrock` hace una llamada mínima a cada modelo y muestra ✓ o ✗ con el motivo.
6. **Lanzar**: en la app elige IA **Bedrock**, o pon `llm.provider: "bedrock"` en tu config.

Coste orientativo para todas las categorías: unas 4.300 llamadas, unos 20 $ con Nova y entre 1 y 1,5 h con 8 workers.

## Calidad y CI

Cada PR y cada push a `main` pasan tres checks en GitHub Actions. Los tres son obligatorios para hacer merge.

| Check | Qué exige | En local |
|---|---|---|
| `ruff` | lint y formato sin avisos | `uv run ruff check src tests scripts` · `uv run ruff format --check src tests scripts` |
| `pylint` | nota ≥ 9.0 y ningún error (E/F) | `uv run pylint src tests scripts` |
| `tests` | todos en verde y coverage ≥ 85 % | `uv run pytest --cov` |

Con `make check` se ejecuta todo. Con `uv run pre-commit install` se pasan ruff y pylint antes de cada commit.

Estado actual: pylint 9.84, 40 tests, coverage 87 %.

Excepciones explícitas, en `pyproject.toml`:
- La longitud de línea no se mira en las plantillas HTML de la app.
- `fastapi.File`/`Form` se aceptan como valores por defecto.
- `invalid-name` se desactiva en las fórmulas de geometría del gráfico isométrico.

Deuda conocida: los `main()` de algunos pasos (s2, s3, s6) son largos; pylint lo marca como `too-many-locals`/`too-many-statements`.

## Cómo contribuir

1. Rama desde `main` con el nombre `<tipo>/<área>-<descripción>`, por ejemplo `git switch -c feat/prompts-versiones-preguntas`. GitHub rechaza el push de ramas con otro nombre.

   | Tipo | Para |
   |---|---|
   | `feat` | algo nuevo |
   | `fix` | arreglar un error |
   | `docs` | documentación |
   | `refactor` | reorganizar código sin cambiar lo que hace |
   | `test` | tests |
   | `chore` | mantenimiento (dependencias, limpieza) |
   | `ci` | integración continua |

   | Área | Qué incluye |
   |---|---|
   | `pipeline` | los pasos s1–s9 y `lib/` |
   | `prompts` | `src/rufus_queries/prompts/` |
   | `app` | la app web |
   | `bedrock` | AWS, Bedrock y sus herramientas |
   | `infra` | repo, CI, uv, scripts |
   | `docs` | README y `docs/` |

   La descripción va en minúsculas y con guiones. La lista está en `scripts/setup_github.py` (`BRANCH_TYPES`, `BRANCH_AREAS`).
2. Cambios con sus tests; `make check` en local.
3. PR a `main`. Para hacer merge hacen falta los tres checks en verde y la aprobación de [@alopera-ux](https://github.com/alopera-ux), definida en CODEOWNERS.

El repo y sus reglas se crean con `scripts/setup_github.py`; los pasos están dentro del script.

## Limitaciones conocidas

- Una categoría con una sola hoja se queda en 4 preguntas por el tope de 4 por hoja (`selection.cap_per_leaf_in_set`).
- Las keywords sin intent claro (NX) no se reasignan.
- Falta validar las preguntas en Rufus: qué responde y qué productos salen.

## Seguridad

Las claves de AWS y los `.ovpn` van solo en el terminal o en 1Password, nunca en el repo. `config.local.json`, `data/raw/` y `runs/` están en `.gitignore`.
