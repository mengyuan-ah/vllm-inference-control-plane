PYTHON ?= python3.12
VENV ?= .venv

CANONICAL_DIRS := \
	src/gateway \
	tests \
	experiments/e5 \
	experiments/e6 \
	experiments/e7 \
	experiments/e8 \
	experiments/e9 \
	experiments/e10 \
	experiments/e11 \
	demo

.PHONY: setup demo test lint check

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -e '.[dev]'

demo:
	$(VENV)/bin/python -m demo.run_demo

test:
	$(VENV)/bin/pytest -q tests
	cd experiments/e5 && ../../$(VENV)/bin/pytest -q
	cd experiments/e6 && ../../$(VENV)/bin/pytest -q
	cd experiments/e7 && ../../$(VENV)/bin/pytest -q
	cd experiments/e8 && ../../$(VENV)/bin/pytest -q
	cd experiments/e9 && ../../$(VENV)/bin/pytest -q
	cd experiments/e10 && ../../$(VENV)/bin/pytest -q
	cd experiments/e11 && ../../$(VENV)/bin/pytest -q

lint:
	$(VENV)/bin/ruff check --config pyproject.toml $(CANONICAL_DIRS)

check: lint test
