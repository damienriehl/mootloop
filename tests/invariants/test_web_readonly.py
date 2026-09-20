"""Structural invariant: the demo web app is READ-ONLY.

`mootloop.web.app` must import zero write-capable functions — `bake.py` is the demo
tier's only writer and is never imported by the app. Enforced by AST inspection so
a refactor that sneaks a writer into the server fails the suite, not a code review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_PATH = Path(__file__).resolve().parents[2] / "src" / "mootloop" / "web" / "app.py"

# Write-capable entry points across the package. None may appear in app.py imports.
FORBIDDEN_NAMES = {
    "create_vault",
    "init_vault",
    "ingest_folder",
    "add_facts_from_file",
    "save_requests",
    "start_run",
    "record_turn",
    "run_with_provider",
    "continue_run",
    "raise_cap",
    "resolve",
    "derive_and_store",
    "attest",
    "check_attestation",
    "verify_run_citations",
    "fulfill_research_request",
    "export_run",
    "write_ledger",
    "build_panel_report",
    "atomic_write_text",
    "atomic_copy",
    "append",
    "write_turn_body",
}

FORBIDDEN_MODULES = {
    "mootloop.web.bake",
    "mootloop.ingest",
    "mootloop.export.service",
}


@pytest.fixture(scope="module")
def app_imports() -> tuple[set[str], set[str]]:
    """(imported binding names, imported module paths) from app.py, via AST."""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
            names.update(alias.name for alias in node.names)
    return names, modules


def test_app_imports_no_write_capable_functions(
    app_imports: tuple[set[str], set[str]],
) -> None:
    names, _ = app_imports
    leaked = names & FORBIDDEN_NAMES
    assert not leaked, f"read-only app.py imports write-capable names: {sorted(leaked)}"


def test_app_never_imports_the_baker_or_writer_modules(
    app_imports: tuple[set[str], set[str]],
) -> None:
    _, modules = app_imports
    leaked = modules & FORBIDDEN_MODULES
    assert not leaked, f"read-only app.py imports writer modules: {sorted(leaked)}"


def test_bake_is_importable_but_separate(app_imports: tuple[set[str], set[str]]) -> None:
    """The writer exists — as its own module the app never imports."""
    assert (APP_PATH.parent / "bake.py").is_file()
    names, modules = app_imports
    assert "build_demo_vault" not in names
    assert not any(m.startswith("mootloop.web.bake") for m in modules)


def test_catalog_reader_has_no_transitive_vault_or_writer_imports() -> None:
    source_root = APP_PATH.parents[1]
    visited: set[str] = set()
    pending = ["mootloop.web.catalog", "mootloop.web.app"]
    allowed = {
        "mootloop",
        "mootloop.web.app",
        "mootloop.web.catalog",
        "mootloop.models.demo",
        "mootloop.models.common",
    }
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        assert module in allowed, f"public reader imports operational module: {module}"
        path = source_root.joinpath(*module.split(".")[1:])
        path = path / "__init__.py" if path.is_dir() else path.with_suffix(".py")
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == "mootloop" or node.module.startswith("mootloop."))
            ):
                pending.append(node.module)
            elif isinstance(node, ast.Import):
                pending.extend(
                    alias.name
                    for alias in node.names
                    if alias.name == "mootloop" or alias.name.startswith("mootloop.")
                )
