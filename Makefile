.PHONY: help install dev test lint format typecheck sim clean

help:
	@echo "make install     install runtime deps"
	@echo "make dev         install runtime + dev deps"
	@echo "make test        run unit, contract and architecture suites"
	@echo "make test-all    run everything including scored evals"
	@echo "make lint        ruff check"
	@echo "make format      ruff format + autofix"
	@echo "make typecheck   mypy (strict)"
	@echo "make sim         run the mock-runtime scenario replay"
	@echo "make clean       remove caches"

install:
	python -m pip install -e .

dev:
	python -m pip install -e ".[dev]"

test:
	python -m pytest tests/unit tests/contract tests/arch tests/integration

test-all:
	python -m pytest

lint:
	ruff check src tests

format:
	ruff format src tests
	ruff check --fix src tests

typecheck:
	mypy

sim:
	python -m swayam.interfaces.cli.app sim capture_weekly_tasks

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
