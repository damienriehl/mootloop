"""Human-approved per-matter context.md launch memory."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mootloop.cli import app
from mootloop.context_memory import (
    context_memory_contribution,
    load_context_memory,
    set_context_memory,
)
from mootloop.engine.launch import launch_run
from mootloop.errors import OrchestratorError
from mootloop.vault import init_vault
from tests.conftest import make_matter

NOW = "2026-08-21T00:00:00+00:00"


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
    return vault


def test_context_memory_requires_matching_human_sidecar_and_enters_next_run(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    metadata = set_context_memory(
        vault,
        "Prefer a short chronology before the analysis.",
        approved_by="Attorney Example",
        approved_at=NOW,
    )

    loaded = load_context_memory(vault)
    assert loaded is not None
    assert loaded[1] == metadata
    assert context_memory_contribution(vault).permission == "privileged"  # type: ignore[union-attr]

    run_id = launch_run(
        vault,
        "discovery-responses",
        NOW,
        run_id="context-memory-run",
    )
    from mootloop.context import load_run_context

    [captured] = load_run_context(vault, run_id).manifest.context_contributions
    assert captured.provenance_locator == "context.md"
    assert "short chronology" in captured.text


def test_context_memory_fails_closed_after_unapproved_edit(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    set_context_memory(vault, "Approved memory.", approved_by="Attorney", approved_at=NOW)
    (vault / "context.md").write_text("Changed without approval.\n", encoding="utf-8")

    with pytest.raises(OrchestratorError, match="changed after human approval"):
        load_context_memory(vault)
    with pytest.raises(OrchestratorError, match="changed after human approval"):
        launch_run(vault, "discovery-responses", NOW, run_id="context-memory-tampered")


def test_context_cli_derives_local_actor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    source = tmp_path / "context-input.md"
    source.write_text("Prefer chronological analysis.\n", encoding="utf-8")
    monkeypatch.setattr(
        "mootloop.cli.operations.pwd.getpwuid",
        lambda _uid: type("User", (), {"pw_name": "trusted-local-attorney"})(),
    )

    runner = CliRunner()
    written = runner.invoke(app, ["context", "set", str(vault), "--input", str(source)])
    shown = runner.invoke(app, ["context", "show", str(vault), "--json"])

    assert written.exit_code == 0, written.output
    assert shown.exit_code == 0, shown.output
    assert "trusted-local-attorney" in shown.output
    assert "Prefer chronological analysis." in shown.output
