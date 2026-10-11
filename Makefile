.PHONY: help install dev test lint format typecheck sim clean eval eval-live

PYTHON := .venv/bin/python
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy

help:
	@echo "make install       install runtime deps"
	@echo "make dev           install runtime + dev deps"
	@echo "make test          run unit, contract and architecture suites"
	@echo "make test-all      run everything including scored evals"
	@echo "make eval          run eval suite against MockLLM"
	@echo "make eval-live     run eval suite against OpenRouter (requires credentials)"
	@echo "make lint          ruff check"
	@echo "make format        ruff format + autofix"
	@echo "make typecheck     mypy (strict)"
	@echo "make sim           run the mock-runtime scenario replay"
	@echo "make clean         remove caches"

install:
	$(PYTHON) -m pip install -e .

dev:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest tests/unit tests/contract tests/arch tests/integration

test-all:
	$(PYTHON) -m pytest

eval:
	$(PYTHON) -m pytest tests/evals/ -v

eval-live:
	@echo "Live eval requires OPENROUTER_API_KEY and model configuration"
	@echo "Not implemented yet - requires live adapter"

lint:
	$(RUFF) check src tests

format:
	$(RUFF) format src tests
	$(RUFF) check --fix src tests

typecheck:
	$(MYPY)

sim:
	$(PYTHON) -m swayam.interfaces.cli.app sim capture_weekly_tasks

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
