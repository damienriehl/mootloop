"""Phase 5 end-to-end: attorney gates across run modes, batch resolution via the CLI,
attestation, and the gate ledger as the export gate.

Everything runs through the FakeLLMProvider — no live calls."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from mootloop import attest, gate_ledger
from mootloop.cli import app
from mootloop.decisions import DecisionStore
from mootloop.discovery_parser import parse_discovery_document, save_requests
from mootloop.facts import add_facts_from_file
from mootloop.ingest import ingest_folder
from mootloop.journal import load_state
from mootloop.llm import FakeLLMProvider
from mootloop.models.common import DocId
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestType
from mootloop.orchestrator import (
    continue_run,
    run_with_provider,
    start_run,
    verify_run_citations,
)
from mootloop.vault import init_vault

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "fixtures" / "synthetic-matter"
NOW = "2026-07-11T00:00:00+00:00"
LATER = "2026-07-20T00:00:00+00:00"
runner = CliRunner()

# ROG + RFP only: all decisions are policy-delegable, so the run finishes and the
# gates batch at the end (autonomous mode).
_DELEGABLE_SETS = [
    ("rogs-set1.txt", RequestType.INTERROGATORY),
    ("rfps-set1.txt", RequestType.RFP),
]


def _build(tmp_path: Path, sets: list[tuple[str, RequestType]], run_mode: str) -> Path:
    raw = yaml.safe_load((FIXTURE / "matter.yaml").read_text(encoding="utf-8"))
    raw["run_mode"] = run_mode
    matter = MatterConfig.model_validate(raw)
    vault = tmp_path / "vault"
    init_vault(vault, matter, registry_path=tmp_path / "canaries.json")
    ingest_folder(vault, FIXTURE / "source-docs", now=NOW, tags_file=FIXTURE / "tags.yaml")
    add_facts_from_file(vault, FIXTURE / "facts.json")
    for filename, request_type in sets:
        data = (FIXTURE / "served" / filename).read_bytes()
        report = parse_discovery_document(
            data.decode("utf-8"), request_type, DocId("doc-servedservedserv")
        )
        save_requests(vault, report.request_set)
    return vault


def test_autonomous_batches_then_resolve_attest_exports(tmp_path: Path) -> None:
    vault = _build(tmp_path, _DELEGABLE_SETS, "autonomous")
    run_id = start_run(vault, "discovery-responses", NOW, run_id="ph5-auto")
    state = run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    # Only delegable gates -> the run finishes, gates batched for one review.
    assert state.status == "finished"
    verify_run_citations(vault, run_id, NOW)

    open_decisions = DecisionStore(vault, run_id).list_open()
    assert open_decisions, "expected batched delegable decisions"
    ready, blockers = gate_ledger.export_ready(vault, run_id)
    assert ready is False
    assert "decisions" in blockers

    # Resolve every gate in one batch via the CLI --input path.
    batch = [
        {"decision_id": d.decision_id, "action": "approve", "choose": d.proposal.recommended}
        for d in open_decisions
    ]
    batch_file = tmp_path / "decisions.json"
    batch_file.write_text(json.dumps(batch), encoding="utf-8")
    result = runner.invoke(
        app, ["decide", "resolve", str(vault), run_id, "--input", str(batch_file)]
    )
    assert result.exit_code == 0, result.output
    assert not DecisionStore(vault, run_id).list_open()

    # Attest -> export ready.
    result = runner.invoke(app, ["attest", str(vault), run_id, "--by", "Jane Attorney"])
    assert result.exit_code == 0, result.output
    assert gate_ledger.export_ready(vault, run_id) == (True, [])


def test_gated_run_pauses_at_checkpoints_then_completes(tmp_path: Path) -> None:
    vault = _build(tmp_path, _DELEGABLE_SETS, "gated")
    run_id = start_run(vault, "discovery-responses", NOW, run_id="ph5-gated")

    pauses = 0
    for _ in range(40):
        state = run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
        if state.status == "checkpoint":
            pauses += 1
            continue_run(vault, run_id)
            continue
        break
    assert pauses >= 1, "a gated run must pause at least once"
    assert load_state(vault, run_id).status == "finished"


def test_post_attestation_edit_invalidates_and_reblocks(tmp_path: Path) -> None:
    vault = _build(tmp_path, _DELEGABLE_SETS, "autonomous")
    run_id = start_run(vault, "discovery-responses", NOW, run_id="ph5-edit")
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    verify_run_citations(vault, run_id, NOW)
    for d in DecisionStore(vault, run_id).list_open():
        args = ["decide", "resolve", str(vault), run_id, d.decision_id]
        runner.invoke(app, [*args, "--action", "approve", "--by", "J"])
    runner.invoke(app, ["attest", str(vault), run_id, "--by", "Jane"])
    assert gate_ledger.export_ready(vault, run_id)[0] is True

    # A substantive post-attestation edit re-imposes DRAFT.
    master = attest.master_deliverable_path(vault, run_id)
    assert master is not None
    master.write_text(master.read_text() + "\nNew paragraph after attestation.\n", encoding="utf-8")

    result = runner.invoke(app, ["attest-status", str(vault), run_id])
    assert "INVALIDATED" in result.output
    ready, blockers = gate_ledger.export_ready(vault, run_id)
    assert ready is False
    assert "attestation" in blockers
