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


def _seed_requests(vault: Path) -> None:
    _init_from_fixture(vault)
    served_sets = (("rogs-set1.txt", "rog"), ("rfps-set1.txt", "rfp"), ("rfas-set1.txt", "rfa"))
    for name, code in served_sets:
        served = str(FIXTURE / "served" / name)
        result = runner.invoke(app, ["requests", "parse", str(vault), served, "--type", code])
        assert result.exit_code == 0


def test_run_estimate_prints_range_and_breakdown(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_requests(vault)
    result = runner.invoke(app, ["run", "estimate", str(vault), "--tier", "moderate"])
    assert result.exit_code == 0, result.output
    assert "range:" in result.output
    assert "notional" in result.output
    assert "judge_panel" in result.output
    assert "rubric_gate" in result.output


def test_run_status_labels_spend_notional(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_requests(vault)
    runner.invoke(app, ["facts", "add", str(vault), "--input", str(FIXTURE / "facts.json")])
    run_id = runner.invoke(app, ["run", "start", str(vault)]).output.strip()
    runner.invoke(app, ["run", "drive", str(vault), run_id, "--fake"])
    status = runner.invoke(app, ["run", "status", str(vault), run_id, "--json"])
    assert status.exit_code == 0, status.output
    assert "notional (plan mode)" in status.output
    assert '"spend_usd"' in status.output


def test_run_raise_cap_appends_event(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_requests(vault)
    run_id = runner.invoke(app, ["run", "start", str(vault)]).output.strip()
    result = runner.invoke(app, ["run", "raise-cap", str(vault), run_id, "--to", "500"])
    assert result.exit_code == 0, result.output
    assert "raised cap" in result.output


def test_cite_verify_text_routes_federal_to_research(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _init_from_fixture(vault)
    text = tmp_path / "cites.txt"
    text.write_text("The claim arises under 42 U.S.C. § 1983.", encoding="utf-8")
    result = runner.invoke(app, ["cite", "verify", str(vault), "--text", str(text)])
    assert result.exit_code == 0, result.output
    assert "needs_research" in result.output
    assert "citator" in result.output.lower()


def test_cite_verify_requires_exactly_one_source(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _init_from_fixture(vault)
    result = runner.invoke(app, ["cite", "verify", str(vault)])
    assert result.exit_code == 1


def test_research_list_and_fulfill(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _init_from_fixture(vault)
    text = tmp_path / "cites.txt"
    text.write_text("See 42 U.S.C. § 1983.", encoding="utf-8")
    runner.invoke(app, ["cite", "verify", str(vault), "--text", str(text)])

    listed = runner.invoke(app, ["research", "list", str(vault)])
    assert listed.exit_code == 0, listed.output
    request_id = listed.output.split()[0]
    assert request_id.startswith("research-")

    authority = tmp_path / "authority.md"
    authority.write_text("# 42 U.S.C. 1983 curated\n", encoding="utf-8")
    fulfilled = runner.invoke(
        app, ["research", "fulfill", str(vault), request_id, "--file", str(authority)]
    )
    assert fulfilled.exit_code == 0, fulfilled.output
    assert "verified" in fulfilled.output
    # queue now shows no open requests
    relist = runner.invoke(app, ["research", "list", str(vault)])
    assert "No open research requests." in relist.output
