.PHONY: setup lint format typecheck test test-paid-oracles check

## First-time project setup: install dependencies and pre-commit hooks
setup:
	uv sync
	uv run pre-commit install

## Run linter with auto-fix
lint:
	uv run ruff check src/ tests/ tools/ --fix

## Format code
format:
	uv run ruff format src/ tests/ tools/

## Run type checker (mypy strict is the authoritative gate)
typecheck:
	uv run mypy

## Run tests with coverage
test:
	uv run pytest tests/ -v --cov=mootloop -m "not paid_oracle"

## Run only explicitly authorized paid-oracle tests (never part of check or CI)
test-paid-oracles:
	uv run pytest tests/ -v -m paid_oracle --run-paid-oracles

## Full gate: lint + typecheck + test
check: lint typecheck test
