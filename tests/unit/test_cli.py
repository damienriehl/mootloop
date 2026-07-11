"""CLI tests via Typer's CliRunner."""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from mootloop.cli import app

runner = CliRunner()


def test_init_non_interactive_happy_path(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    result = runner.invoke(
        app,
        [
            "init",
            str(vault),
            "--matter-id",
            "acme-v-widgets",
            "--no-interactive",
            "--court",
            "District Court, Hennepin County",
            "--case-number",
            "27-CV-26-1234",
            "--our-side",
            "defendant",
            "--jurisdiction-state",
            "MN",
            "--forum",
            "state",
            "--county",
            "Hennepin",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (vault / "matter.yaml").is_file()
    assert (vault / "corpus" / "normalized").is_dir()
    assert (vault / ".canary").is_file()


def test_init_refuses_vault_inside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    vault = repo / "matters" / "m1"
    result = runner.invoke(
        app,
        [
            "init",
            str(vault),
            "--matter-id",
            "m1",
            "--no-interactive",
            "--court",
            "Court",
            "--case-number",
            "1",
            "--our-side",
            "plaintiff",
            "--jurisdiction-state",
            "MN",
            "--forum",
            "state",
        ],
    )
    assert result.exit_code == 1
    assert not vault.exists()


def test_init_non_interactive_missing_flags_errors(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    result = runner.invoke(
        app,
        ["init", str(vault), "--matter-id", "m1", "--no-interactive"],
    )
    assert result.exit_code == 1
    assert "--court" in result.output


def test_validate_ok(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    runner.invoke(
        app,
        [
            "init",
            str(vault),
            "--matter-id",
            "m1",
            "--no-interactive",
            "--court",
            "Court",
            "--case-number",
            "1",
            "--our-side",
            "plaintiff",
            "--jurisdiction-state",
            "MN",
            "--forum",
            "state",
        ],
    )
    result = runner.invoke(app, ["validate", str(vault)])
    assert result.exit_code == 0
    assert "OK" in result.output


def test_validate_bad_matter_names_field(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "matter.yaml").write_text("schema_version: '1.0'\nmatter_id: m1\n")
    result = runner.invoke(app, ["validate", str(vault)])
    assert result.exit_code == 1
    assert "caption" in result.output


def test_validate_json_output(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "matter.yaml").write_text("schema_version: '1.0'\nmatter_id: m1\n")
    result = runner.invoke(app, ["validate", str(vault), "--json"])
    assert result.exit_code == 1
    assert '"ok": false' in result.output
    assert "caption" in result.output
