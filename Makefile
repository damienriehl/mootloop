.PHONY: setup lint format typecheck test check

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
	uv run pytest tests/ -v --cov=mootloop

## Full gate: lint + typecheck + test
check: lint typecheck test
