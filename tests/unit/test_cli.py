"""CLI tests via Typer's CliRunner."""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from mootloop.cli import app

runner = CliRunner()

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic-matter"


def _init_from_fixture(vault: Path) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            str(vault),
            "--matter-id",
            "northfield-widgets-v-granite-supply",
            "--no-interactive",
            "--from-yaml",
            str(FIXTURE / "matter.yaml"),
        ],
    )
    assert result.exit_code == 0, result.output


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


def test_ingest_requests_facts_pipeline(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _init_from_fixture(vault)

    ingest = runner.invoke(
        app,
        ["ingest", str(vault), str(FIXTURE / "source-docs"), "--tags", str(FIXTURE / "tags.yaml")],
    )
    assert ingest.exit_code == 0, ingest.output
    assert "Ingested 6 document(s)" in ingest.output

    rogs = str(FIXTURE / "served" / "rogs-set1.txt")
    parse = runner.invoke(app, ["requests", "parse", str(vault), rogs, "--type", "rog"])
    assert parse.exit_code == 0, parse.output
    assert "8 request(s) + 3 subpart(s)" in parse.output
    assert (vault / "requests" / "rog-set01.json").is_file()

    add = runner.invoke(app, ["facts", "add", str(vault), "--input", str(FIXTURE / "facts.json")])
    assert add.exit_code == 0, add.output
    assert "Added 6 fact(s)" in add.output

    listed = runner.invoke(app, ["facts", "list", str(vault)])
    assert listed.exit_code == 0, listed.output
    assert "contract price of $148,500" in listed.output


def test_facts_add_unknown_source_exits_nonzero(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _init_from_fixture(vault)
    bad = tmp_path / "bad.json"
    bad.write_text('[{"statement": "x", "provenance": [{"source": "nope.md", "quote": "q"}]}]')
    result = runner.invoke(app, ["facts", "add", str(vault), "--input", str(bad)])
    assert result.exit_code == 1
