.PHONY: install lint format test check app

install:      ## entorno con uv (incluye dependencias de desarrollo)
	uv sync

lint:         ## ruff + pylint (igual que CI)
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts
	uv run pylint src tests scripts

format:       ## arregla formato e imports
	uv run ruff check --fix src tests scripts
	uv run ruff format src tests scripts

test:         ## tests con coverage (mínimo 85 %)
	uv run pytest --cov --cov-report=term

check: lint test

app:          ## app web en http://localhost:8000
	uv run rufus-app
