"""CLI tests via Typer's CliRunner."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from mootloop import orchestrator
from mootloop.cli import app
from mootloop.context import load_run_context
from mootloop.context_sources import FIRM_PREFERENCES_ENV, ContextContributionStore
from mootloop.engine.queue import Queue
from mootloop.errors import QueueError
from mootloop.journal import read_events
from mootloop.llm import FakeLLMProvider
from mootloop.models.common import MatterId
from mootloop.models.context import ContextContribution
from mootloop.models.events import RunEnqueued, RunReopened, RunStarted

runner = CliRunner()

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic-matter"
WORKER_IMAGE = "ghcr.io/alea-institute/folio-enrich@sha256:" + "a" * 64


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


@pytest.mark.parametrize(
    ("command", "service_name"),
    [
        ("start-matter-worker", "start_matter_worker"),
        ("stop-matter-worker", "stop_matter_worker"),
        ("remove-matter-worker", "remove_matter_worker"),
    ],
)
def test_matter_worker_cli_uses_worker_compose_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    service_name: str,
) -> None:
    captured: dict[str, object] = {}

    def fake_service(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured.update(kwargs)

    monkeypatch.setattr(f"mootloop.cli.operations.driver_service.{service_name}", fake_service)
    args = ["driver", command, "2026-08-21-acme-test"]
    if command == "start-matter-worker":
        args.extend(
            [
                "--matters-root",
                str(tmp_path / "matters"),
                "--proxy-password-file",
                str(tmp_path / "proxy-password"),
                "--legal-proxy-password-file",
                str(tmp_path / "legal-proxy-password"),
                "--folio-enrich-image",
                WORKER_IMAGE,
            ]
        )
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert captured["compose_file"] == Path("docker-compose.worker.yaml")


def test_close_uses_trusted_local_os_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_close(*args: object, **kwargs: object) -> SimpleNamespace:
        captured["args"] = args
        captured.update(kwargs)
        return SimpleNamespace(removed_counts={"facts": 1})

    monkeypatch.setattr("mootloop.close.close_matter", fake_close)
    monkeypatch.setattr(
        "mootloop.cli.operations.pwd.getpwuid",
        lambda _uid: SimpleNamespace(pw_name="trusted-local-user"),
    )

    refused = runner.invoke(
        app,
        [
            "close",
            "matter-1",
            "--matters-root",
            str(tmp_path / "matters"),
            "--backup-dir",
            str(tmp_path / "backups"),
        ],
    )
    assert refused.exit_code == 1
    assert "--acknowledge-not-assured-destruction" in refused.output
    assert captured == {}

    result = runner.invoke(
        app,
        [
            "close",
            "matter-1",
            "--matters-root",
            str(tmp_path / "matters"),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--acknowledge-not-assured-destruction",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["actor"] == "trusted-local-user"
    assert "closed matter-1: 1 registered-store match(es) inventoried" in result.output
    assert "--by" not in runner.invoke(app, ["close", "--help"]).output


def test_hosted_driver_refuses_unbound_or_missing_matter(tmp_path: Path) -> None:
    base = ["driver", "run-once", "--matters-root", str(tmp_path), "--worker-id", "w1"]

    unbound = runner.invoke(app, [*base, "--mode", "hosted", "--matter-id", "unbound"])
    missing = runner.invoke(app, [*base, "--mode", "hosted", "--matter-id", "missing"])

    assert unbound.exit_code != 0 and "requires a matter id" in unbound.output
    assert missing.exit_code != 0 and "did work" not in missing.output


def test_hosted_driver_accepts_explicit_fixed_vault(tmp_path: Path) -> None:
    matters_root = tmp_path / "matters"
    matters_root.mkdir()
    vault = tmp_path / "mounted-matter"
    _init_from_fixture(vault)

    result = runner.invoke(
        app,
        [
            "driver",
            "run-once",
            "--matters-root",
            str(matters_root),
            "--worker-id",
            "w1",
            "--mode",
            "hosted",
            "--matter-id",
            "northfield-widgets-v-granite-supply",
            "--matter-vault",
            str(vault),
            "--fake",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output == "idle\n"


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


def test_run_start_hosted_vault_commits_and_drains_stable_outbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matters_root = tmp_path / "matters"
    vault = matters_root / "northfield-widgets-v-granite-supply"
    _seed_requests(vault)
    firm = tmp_path / "firm-preferences.yaml"
    firm.write_text(
        "schema_version: '1.0'\nrun_config:\n  max_attempts: 7\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(FIRM_PREFERENCES_ENV, str(firm))
    text = "Use the approved CLI chronology."
    ContextContributionStore(vault).put(
        ContextContribution(
            contribution_id="board-cli-approved",
            kind="board",
            text=text,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            provenance_locator="board://approved/cli",
            source_matter_id=MatterId("northfield-widgets-v-granite-supply"),
            task_scope=("discovery-responses",),
            permission="privileged",
            approval_state="approved",
        )
    )
    env = {**os.environ, "MOOTLOOP_MATTERS_ROOT": str(matters_root)}
    args = ["run", "start", str(vault), "--run-id", "hosted-start"]

    first = runner.invoke(app, args, env=env)
    assert first.exit_code == 0, first.output
    retry = runner.invoke(app, args, env=env)
    assert retry.exit_code == 0, retry.output

    queued = Queue(matters_root).snapshot()
    assert [item.item_id for item in queued] == [
        "run:northfield-widgets-v-granite-supply:hosted-start"
    ]
    events = read_events(vault, "hosted-start")
    started = next(event for event in events if isinstance(event, RunStarted))
    assert started.queue_intent is not None
    assert len([event for event in events if isinstance(event, RunEnqueued)]) == 1
    manifest = load_run_context(vault, "hosted-start").manifest
    assert manifest.max_attempts == 7
    assert [item.contribution_id for item in manifest.context_contributions] == [
        "board-cli-approved"
    ]


def test_run_start_fails_closed_for_escaping_hosted_registry_path(tmp_path: Path) -> None:
    matters_root = tmp_path / "matters"
    matters_root.mkdir()
    vault = tmp_path / "outside" / "northfield-widgets-v-granite-supply"
    _seed_requests(vault)
    (matters_root / vault.name).symlink_to(vault, target_is_directory=True)
    env = {**os.environ, "MOOTLOOP_MATTERS_ROOT": str(matters_root)}

    result = runner.invoke(
        app,
        ["run", "start", str(vault), "--run-id", "must-not-start"],
        env=env,
    )

    assert result.exit_code != 0
    assert "outside matters-root" in result.output
    assert read_events(vault, "must-not-start") == []


def test_run_start_fails_closed_for_hosted_path_config_identity_mismatch(
    tmp_path: Path,
) -> None:
    matters_root = tmp_path / "matters"
    vault = matters_root / "northfield-widgets-v-granite-supply"
    _seed_requests(vault)
    matter_path = vault / "matter.yaml"
    matter_path.write_text(
        matter_path.read_text(encoding="utf-8").replace(
            "matter_id: northfield-widgets-v-granite-supply",
            "matter_id: renamed-matter",
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "MOOTLOOP_MATTERS_ROOT": str(matters_root)}

    result = runner.invoke(
        app,
        ["run", "start", str(vault), "--run-id", "must-not-start"],
        env=env,
    )

    assert result.exit_code != 0
    assert "does not match matter identity" in result.output
    assert read_events(vault, "must-not-start") == []


def test_run_status_reports_non_replayable_context_without_failing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_requests(vault)
    run_id = runner.invoke(app, ["run", "start", str(vault)]).output.strip()
    (vault / "runs" / run_id / "context" / "corpus.json").write_text("{}\n", encoding="utf-8")

    status = runner.invoke(app, ["run", "status", str(vault), run_id, "--json"])

    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["replayable"] is False
    assert "corpus snapshot" in payload["context_blocker"]


def test_run_raise_cap_appends_event(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_requests(vault)
    run_id = runner.invoke(app, ["run", "start", str(vault)]).output.strip()
    result = runner.invoke(app, ["run", "raise-cap", str(vault), run_id, "--to", "500"])
    assert result.exit_code == 0, result.output
    assert "raised cap" in result.output


def _needs_attention_run(vault: Path) -> str:
    """A run halted by the counter cap: derail the first turn until it exhausts its
    attempts (the synthetic path into ``needs_attention``)."""
    from mootloop.orchestrator import plan_next, record_turn

    runner.invoke(app, ["facts", "add", str(vault), "--input", str(FIXTURE / "facts.json")])
    run_id = runner.invoke(app, ["run", "start", str(vault)]).output.strip()
    turn_id = plan_next(vault, run_id)[0].turn_id
    for _ in range(3):
        record_turn(vault, run_id, turn_id, "not valid json", None, "2026-07-11T00:00:00+00:00")
    return run_id


def test_run_blockers_lists_the_counter_capped_turn(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_requests(vault)
    run_id = _needs_attention_run(vault)

    result = runner.invoke(app, ["run", "blockers", str(vault), run_id, "--json"])
    assert result.exit_code == 0, result.output
    blockers = json.loads(result.output)
    assert [b["kind"] for b in blockers] == ["counter_capped_turn"]


def test_run_reopen_refuses_then_reopens_with_a_grant(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_requests(vault)
    run_id = _needs_attention_run(vault)

    refused = runner.invoke(
        app, ["run", "reopen", str(vault), run_id, "--reason", "fixed the persona body"]
    )
    assert refused.exit_code == 1
    assert "unresolved blocker" in refused.output

    granted = runner.invoke(
        app,
        [
            "run",
            "reopen",
            str(vault),
            run_id,
            "--reason",
            "fixed the persona body",
            "--grant-attempts",
            "2",
        ],
    )
    assert granted.exit_code == 0, granted.output
    assert "reopened" in granted.output
    assert "standalone vault" in granted.output

    status = runner.invoke(app, ["run", "status", str(vault), run_id, "--json"])
    assert json.loads(status.output)["status"] == "running"


def test_run_reopen_enqueues_when_vault_is_in_hosted_matters_root(tmp_path: Path) -> None:
    matters_root = tmp_path / "matters"
    vault = matters_root / "northfield-widgets-v-granite-supply"
    _seed_requests(vault)
    run_id = _needs_attention_run(vault)

    granted = runner.invoke(
        app,
        [
            "run",
            "reopen",
            str(vault),
            run_id,
            "--reason",
            "fixed the persona body",
            "--grant-attempts",
            "2",
        ],
        env={**os.environ, "MOOTLOOP_MATTERS_ROOT": str(matters_root)},
    )

    assert granted.exit_code == 0, granted.output
    assert "queued for the hosted driver" in granted.output
    queued = Queue(matters_root).snapshot()
    assert [(item.matter_id, item.run_id) for item in queued] == [
        ("northfield-widgets-v-granite-supply", run_id)
    ]


def test_run_reopen_retry_repairs_queue_after_first_enqueue_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matters_root = tmp_path / "matters"
    vault = matters_root / "northfield-widgets-v-granite-supply"
    _seed_requests(vault)
    run_id = _needs_attention_run(vault)
    original = Queue.ensure_enqueued
    calls = 0

    def fail_once(self: Queue, item: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise QueueError("injected queue write failure")
        return original(self, item)  # type: ignore[arg-type]

    monkeypatch.setattr(Queue, "ensure_enqueued", fail_once)
    args = [
        "run",
        "reopen",
        str(vault),
        run_id,
        "--reason",
        "fixed the persona body",
        "--grant-attempts",
        "2",
    ]
    env = {**os.environ, "MOOTLOOP_MATTERS_ROOT": str(matters_root)}

    first = runner.invoke(app, args, env=env)
    assert first.exit_code == 1
    assert Queue(matters_root).snapshot() == []

    retry = runner.invoke(app, args, env=env)
    assert retry.exit_code == 0, retry.output
    queued = Queue(matters_root).snapshot()
    assert [(item.matter_id, item.run_id) for item in queued] == [
        ("northfield-widgets-v-granite-supply", run_id)
    ]
    reopened = [event for event in read_events(vault, run_id) if isinstance(event, RunReopened)]
    assert len(reopened) == 1


def test_run_reopen_uses_local_os_identity_and_has_no_by_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _seed_requests(vault)
    run_id = _needs_attention_run(vault)

    class _LocalUser:
        pw_name = "trusted-local-user"

    monkeypatch.setattr("mootloop.cli.pwd.getpwuid", lambda _uid: _LocalUser())

    result = runner.invoke(
        app,
        [
            "run",
            "reopen",
            str(vault),
            run_id,
            "--reason",
            "fixed the persona body",
            "--grant-attempts",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    event = next(e for e in read_events(vault, run_id) if isinstance(e, RunReopened))
    assert event.reopened_by == "trusted-local-user"

    help_result = runner.invoke(app, ["run", "reopen", "--help"])
    assert help_result.exit_code == 0
    assert "--by" not in help_result.output
    assert "--force" not in help_result.output


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


def test_cite_check_enqueues_hosted_interactive_job(tmp_path: Path) -> None:
    matters_root = tmp_path / "matters"
    vault = matters_root / "northfield-widgets-v-granite-supply"
    _seed_requests(vault)
    run_id = orchestrator.start_run(
        vault, "discovery-responses", "2026-08-21T16:00:00+00:00", run_id="cite-check"
    )
    orchestrator.run_with_provider(
        vault,
        run_id,
        FakeLLMProvider(),
        "2026-08-21T16:00:00+00:00",
    )

    result = runner.invoke(
        app,
        ["cite", "check", str(vault), "--run", run_id],
        env={**os.environ, "MOOTLOOP_MATTERS_ROOT": str(matters_root)},
    )

    assert result.exit_code == 0, result.output
    assert result.output == (
        "queued cite:northfield-widgets-v-granite-supply:"
        "cite-check\n"
    )
    [item] = Queue(matters_root).snapshot()
    assert item.lane == "interactive"
    assert item.kind == "citation_propositions"


def test_judge_profile_enqueues_hosted_interactive_job(tmp_path: Path) -> None:
    matters_root = tmp_path / "matters"
    vault = matters_root / "northfield-widgets-v-granite-supply"
    _init_from_fixture(vault)

    result = runner.invoke(
        app,
        ["judge", "profile", str(vault)],
        env={**os.environ, "MOOTLOOP_MATTERS_ROOT": str(matters_root)},
    )

    assert result.exit_code == 0, result.output
    assert result.output == (
        "queued judge-profile:northfield-widgets-v-granite-supply\n"
    )
    [item] = Queue(matters_root).snapshot()
    assert item.lane == "interactive"
    assert item.kind == "judge_profile"
    assert item.run_id == "judge-profile"


def test_judge_profile_fails_closed_for_hosted_path_config_identity_mismatch(
    tmp_path: Path,
) -> None:
    matters_root = tmp_path / "matters"
    vault = matters_root / "northfield-widgets-v-granite-supply"
    _init_from_fixture(vault)
    matter_path = vault / "matter.yaml"
    matter_path.write_text(
        matter_path.read_text(encoding="utf-8").replace(
            "matter_id: northfield-widgets-v-granite-supply",
            "matter_id: renamed-matter",
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["judge", "profile", str(vault)],
        env={**os.environ, "MOOTLOOP_MATTERS_ROOT": str(matters_root)},
    )

    assert result.exit_code != 0
    assert "does not match matter identity" in result.output
    assert Queue(matters_root).snapshot() == []


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
