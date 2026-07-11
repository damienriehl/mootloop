"""Attestation canonicalization/invalidation, the gate ledger fold, and the STATE
marker mapping (plan D9/H8/Phase 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mootloop import attest, gate_ledger, orchestrator
from mootloop.decisions import DecisionStore, open_by_taxonomy, resolve
from mootloop.discovery_parser import save_requests
from mootloop.errors import AttestationBlockedError
from mootloop.facts import FactStore
from mootloop.llm import FakeLLMProvider
from mootloop.models.common import DocId
from mootloop.models.requests import RequestItem, RequestSet, RequestType, make_request_id
from mootloop.orchestrator import run_with_provider, start_run, verify_run_citations
from mootloop.vault import init_vault, load_matter
from tests.conftest import make_matter

NOW = "2026-07-11T00:00:00+00:00"
LATER = "2026-07-12T00:00:00+00:00"


def _vault(tmp_path: Path, request_type: RequestType) -> Path:
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
    return vault


def _finished_and_resolved(tmp_path: Path, run_id: str) -> Path:
    """A ROG run driven to finished, all decisions resolved, citations verified."""
    vault = _vault(tmp_path, RequestType.INTERROGATORY)
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    matter = load_matter(vault)
    for decision in [
        *open_by_taxonomy(vault, run_id, matter, "hard-human"),
        *open_by_taxonomy(vault, run_id, matter, "policy-delegable"),
    ]:
        resolve(
            vault, run_id, decision.decision_id, "approve",
            decision.proposal.recommended, "", "Atty", "human", NOW,
        )
    verify_run_citations(vault, run_id, NOW)
    return vault


def test_state_marker_mapping() -> None:
    assert orchestrator.state_marker("running") == "working"
    assert orchestrator.state_marker("needs_decisions") == "ask-pending"
    assert orchestrator.state_marker("checkpoint") == "ask-pending"
    assert orchestrator.state_marker("needs_attention") == "blocked"
    assert orchestrator.state_marker("capped") == "blocked"
    assert orchestrator.state_marker("finished") == "done"


def test_attest_blocked_while_decisions_open(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.RFA)
    run_id = "att-blocked"
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    # Hard-human RFA gate is open -> attestation refused.
    with pytest.raises(AttestationBlockedError):
        attest.attest(vault, run_id, "Jane", NOW)


def test_whitespace_only_edit_does_not_invalidate(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-ws")
    attest.attest(vault, "att-ws", "Jane", NOW)
    master = attest.master_deliverable_path(vault, "att-ws")
    assert master is not None
    # Append trailing whitespace + a blank line — canonicalization strips both.
    master.write_text(master.read_text() + "   \n\n", encoding="utf-8")
    assert attest.check_attestation(vault, "att-ws", LATER).status == "valid"
    assert gate_ledger.export_ready(vault, "att-ws")[0] is True


def test_content_edit_invalidates_and_blocks_export(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-edit")
    attest.attest(vault, "att-edit", "Jane", NOW)
    assert gate_ledger.export_ready(vault, "att-edit")[0] is True

    master = attest.master_deliverable_path(vault, "att-edit")
    assert master is not None
    master.write_text(master.read_text() + "\nInjected substantive clause.\n", encoding="utf-8")

    check = attest.check_attestation(vault, "att-edit", LATER)
    assert check.status == "invalidated"
    ready, blockers = gate_ledger.export_ready(vault, "att-edit")
    assert ready is False
    assert "attestation" in blockers


def test_check_attestation_missing_before_attest(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-missing")
    assert attest.check_attestation(vault, "att-missing", NOW).status == "missing"


def test_gate_ledger_folds_decisions_and_attestation(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.INTERROGATORY)
    run_id = "gl-fold"
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    verify_run_citations(vault, run_id, NOW)

    # Open delegable decisions -> export blocked on decisions + attestation.
    ready, blockers = gate_ledger.export_ready(vault, run_id)
    assert ready is False
    assert "decisions" in blockers
    assert "attestation" in blockers

    for decision in DecisionStore(vault, run_id).list_open():
        resolve(
            vault, run_id, decision.decision_id, "approve",
            decision.proposal.recommended, "", "Atty", "human", NOW,
        )
    # Decisions clear; attestation still pending.
    ready, blockers = gate_ledger.export_ready(vault, run_id)
    assert "decisions" not in blockers
    assert blockers == ["attestation"]

    attest.attest(vault, run_id, "Jane", NOW)
    assert gate_ledger.export_ready(vault, run_id) == (True, [])


def test_gate_ledger_json_written(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "gl-write")
    path = gate_ledger.write_ledger(vault, "gl-write")
    assert path.is_file()
    assert path.name == "gate-ledger.json"
