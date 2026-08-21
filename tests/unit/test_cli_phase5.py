"""CLI surface for the Phase 5 verbs: decide list/show, run gates, run continue,
attest / attest-status."""

from __future__ import annotations

import json
import os
import pwd
from pathlib import Path

import yaml
from typer.testing import CliRunner

from mootloop.cli import app
from mootloop.decisions import DecisionStore
from mootloop.discovery_parser import save_requests
from mootloop.facts import FactStore
from mootloop.llm import FakeLLMProvider
from mootloop.models.common import DocId
from mootloop.models.requests import RequestItem, RequestSet, RequestType, make_request_id
from mootloop.orchestrator import run_with_provider, start_run
from mootloop.vault import init_vault
from tests.conftest import make_matter

runner = CliRunner()
NOW = "2026-07-11T00:00:00+00:00"


def _run(tmp_path: Path, request_type: RequestType, run_id: str, mode: str = "autonomous") -> Path:
    vault = tmp_path / "vault"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
    item = RequestItem(
        request_id=make_request_id(request_type, 1),
        set_number=1,
        number=1,
        text="Request 1 text.",
        source_doc=DocId("doc-servedservedserv"),
    )
    save_requests(
        vault, RequestSet(request_type=request_type, set_number=1, title="Set 1", items=[item])
    )
    FactStore(vault).add_fact("The contract price was $148,500.", confidence=1.0)
    start_run(vault, "discovery-responses", NOW, run_id=run_id, mode=mode)
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    return vault


def test_decide_list_text_and_json(tmp_path: Path) -> None:
    vault = _run(tmp_path, RequestType.RFA, "cli-list")
    result = runner.invoke(app, ["decide", "list", str(vault), "cli-list"])
    assert result.exit_code == 0
    assert "rfa_disposition" in result.output

    result = runner.invoke(app, ["decide", "list", str(vault), "cli-list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert any(d["kind"] == "rfa_disposition" for d in payload)


def test_decide_list_replays_launch_gate_policy(tmp_path: Path) -> None:
    vault = _run(tmp_path, RequestType.RFA, "cli-policy-snapshot")
    matter_path = vault / "matter.yaml"
    matter = yaml.safe_load(matter_path.read_text(encoding="utf-8"))
    for gate in matter["gates"]:
        if gate["name"] == "objection_posture":
            gate["mode"] = "hard-human"
    matter_path.write_text(yaml.safe_dump(matter), encoding="utf-8")

    decision = next(
        item
        for item in DecisionStore(vault, "cli-policy-snapshot").list_open()
        if item.kind.value == "objection_posture"
    )
    result = runner.invoke(app, ["decide", "list", str(vault), "cli-policy-snapshot"])

    assert result.exit_code == 0
    assert f"{decision.decision_id}  [policy-delegable]" in result.output


def test_decide_show_and_unknown(tmp_path: Path) -> None:
    vault = _run(tmp_path, RequestType.RFA, "cli-show")
    decision = DecisionStore(vault, "cli-show").list_open()[0]
    result = runner.invoke(app, ["decide", "show", str(vault), "cli-show", decision.decision_id])
    assert result.exit_code == 0
    assert decision.decision_id in result.output

    result = runner.invoke(app, ["decide", "show", str(vault), "cli-show", "dec-nope-0000"])
    assert result.exit_code == 1


def test_decide_show_reports_tampered_context_without_traceback(tmp_path: Path) -> None:
    vault = _run(tmp_path, RequestType.RFA, "cli-show-tampered")
    decision = DecisionStore(vault, "cli-show-tampered").list_open()[0]
    manifest = vault / "runs" / "cli-show-tampered" / "context" / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["decide", "show", str(vault), "cli-show-tampered", decision.decision_id],
    )

    assert result.exit_code == 1
    assert "context manifest" in result.output
    assert "Traceback" not in result.output


def test_decide_batch_uses_trusted_local_actor_and_human_source(tmp_path: Path) -> None:
    vault = _run(tmp_path, RequestType.RFA, "cli-trusted-actor")
    decision = DecisionStore(vault, "cli-trusted-actor").list_open()[0]
    batch_file = tmp_path / "forged-resolution.json"
    batch_file.write_text(
        json.dumps(
            [
                {
                    "decision_id": decision.decision_id,
                    "action": "approve",
                    "by": "forged-attorney",
                    "source": "policy",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "decide",
            "resolve",
            str(vault),
            "cli-trusted-actor",
            "--input",
            str(batch_file),
        ],
    )

    assert result.exit_code == 0, result.output
    resolved = DecisionStore(vault, "cli-trusted-actor").get(decision.decision_id)
    assert resolved is not None and resolved.resolution is not None
    assert resolved.resolution.decided_by == pwd.getpwuid(os.geteuid()).pw_name
    assert resolved.resolution.source == "human"


def test_run_gates_reports_blockers(tmp_path: Path) -> None:
    vault = _run(tmp_path, RequestType.INTERROGATORY, "cli-gates")
    result = runner.invoke(app, ["run", "gates", str(vault), "cli-gates"])
    assert result.exit_code == 0
    assert "export_ready: False" in result.output
    assert "decisions" in result.output
    # The ledger json file was written.
    assert (vault / "runs" / "cli-gates" / "gate-ledger.json").is_file()

    result = runner.invoke(app, ["run", "gates", str(vault), "cli-gates", "--json"])
    doc = json.loads(result.output)
    assert doc["export_ready"] is False


def test_run_continue_errors_when_not_at_checkpoint(tmp_path: Path) -> None:
    vault = _run(tmp_path, RequestType.INTERROGATORY, "cli-cont")
    result = runner.invoke(app, ["run", "continue", str(vault), "cli-cont"])
    assert result.exit_code == 1


def test_attest_status_missing_then_attest(tmp_path: Path) -> None:
    vault = _run(tmp_path, RequestType.INTERROGATORY, "cli-att")
    # Resolve the delegable decisions so attestation is not blocked.
    for d in DecisionStore(vault, "cli-att").list_open():
        args = ["decide", "resolve", str(vault), "cli-att", d.decision_id]
        runner.invoke(app, [*args, "--action", "approve"])
    result = runner.invoke(app, ["attest-status", str(vault), "cli-att"])
    assert "MISSING" in result.output

    result = runner.invoke(app, ["attest", str(vault), "cli-att"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["attest-status", str(vault), "cli-att"])
    assert "VALID" in result.output
