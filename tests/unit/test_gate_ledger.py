"""The gate ledger reports the OPERATIVE draft's blocking-gate status.

A fabrication finding raised on an early draft and genuinely cured in the draft that
supersedes it must stop blocking export — while a finding still present in the draft
that will actually be served must keep blocking it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from mootloop import gate_ledger, orchestrator
from mootloop.context import load_run_context
from mootloop.journal import journal_path, load_state, read_events
from mootloop.llm import FakeLLMProvider
from mootloop.models.events import GateEvaluated
from mootloop.orchestrator import run_with_provider, start_run
from mootloop.vault import atomic_write_text
from tests.unit.test_orchestrator_planning import (
    NOW,
    _build_single_request_vault,
)

REQUEST_ID = "ROG-1"

# A dollar amount that appears in no fact and no corpus document — provenance-required
# under fabrication check (b), so this draft fails the gate.
FABRICATED = "Defendant paid $999,999.00 under the agreement."


def _draft(spec: Any, prompt: str, *, fabricate: bool) -> dict[str, Any]:
    """A degeneracy-clean draft that optionally asserts an unsupported amount."""
    fact_ids = list(spec.prompt_context.get("fact_ids", []))
    text = FABRICATED if fabricate else "Defendant responds to the request as stated."
    return {
        "response_text": text,
        "objections": [{"basis": "relevance", "text": "Overbroad as to time."}],
        "candidate_citations": [],
        "fact_ids_used": fact_ids[:1],
        "attorney_gate_items": [] if fact_ids else ["verify factual basis"],
        "rfa_disposition": None,
        "self_assessment": "Grounded in the cited fact.",
    }


def _run(vault: Path, run_id: str, *, fabricating_stage: str) -> None:
    provider = FakeLLMProvider(
        script={
            ("associate", fabricating_stage): lambda s, p: _draft(s, p, fabricate=True),
        }
    )
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    run_with_provider(vault, run_id, provider, NOW)


def _recorded_fabrication_statuses(vault: Path, run_id: str) -> list[str]:
    return [
        e.result.status
        for e in read_events(vault, run_id)
        if isinstance(e, GateEvaluated) and e.result.gate == "fabrication"
    ]


def test_fabrication_cured_in_the_operative_draft_stops_blocking(tmp_path: Path) -> None:
    """The first associate draft fabricates; the bolster that supersedes it does not.
    The ledger must gate on the bolster — the draft that would actually be served."""
    vault = _build_single_request_vault(tmp_path)
    run_id = "ledger-cured"
    _run(vault, run_id, fabricating_stage="associate_draft")

    # The finding really was raised, and really was cured on a later draft.
    statuses = _recorded_fabrication_statuses(vault, run_id)
    assert "fail" in statuses, "the early draft should have failed the fabrication gate"
    assert statuses[-1] == "pass", "the operative (bolster) draft should be clean"

    doc = gate_ledger.build_ledger(vault, run_id)
    assert doc.gates[REQUEST_ID]["fabrication"] == "pass"
    assert "fabrication" not in doc.blockers

    # The audit trail is not erased: the ledger still shows the finding once existed.
    assert doc.superseded[REQUEST_ID]["fabrication"] == "fail"


def test_fabrication_in_the_operative_draft_still_blocks(tmp_path: Path) -> None:
    """The other direction — the served draft is the one that fabricates. This gate
    protects a court filing; curing it later is the only thing that may clear it."""
    vault = _build_single_request_vault(tmp_path)
    run_id = "ledger-uncured"
    _run(vault, run_id, fabricating_stage="bolster")

    assert _recorded_fabrication_statuses(vault, run_id)[-1] == "fail"

    doc = gate_ledger.build_ledger(vault, run_id)
    assert doc.gates[REQUEST_ID]["fabrication"] == "fail"
    assert "fabrication" in doc.blockers
    assert doc.export_ready is False


def test_ledger_selects_each_operative_draft_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = "ledger-one-pass"
    _run(vault, run_id, fabricating_stage="associate_draft")
    original = orchestrator._context_for
    calls = 0

    def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "_context_for", counted)
    doc = gate_ledger.build_ledger(vault, run_id)
    assert doc.gates[REQUEST_ID]["fabrication"] == "pass"
    assert calls == len(doc.gates)


def test_ledger_before_any_draft_fails_closed_and_reads_new_events(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = "ledger-unfinished"
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    before = gate_ledger.build_ledger(vault, run_id)
    assert before.export_ready is False
    assert before.gates[REQUEST_ID]["fabrication"] == "pending"

    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    after = gate_ledger.build_ledger(vault, run_id)
    assert after.gates[REQUEST_ID]["fabrication"] == "pass"
    assert before.gates[REQUEST_ID]["fabrication"] == "pending"


def test_selected_draft_records_are_detached_from_loaded_state(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = "ledger-detached"
    _run(vault, run_id, fabricating_stage="associate_draft")
    state = load_state(vault, run_id)
    snapshot = state.model_copy(deep=True)
    selected = orchestrator.operative_draft_records(run_id, load_run_context(vault, run_id), state)
    record = selected[0][1]
    assert record is not None
    record.output["response_text"] = "Mutation must remain local to the selected copy."
    assert state == snapshot


def test_ledger_rejects_corruption_after_a_successful_read(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = "ledger-corrupt"
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    gate_ledger.build_ledger(vault, run_id)
    path = journal_path(vault, run_id)
    atomic_write_text(path, path.read_text() + '{"broken":true}\n')
    with pytest.raises(ValidationError):
        gate_ledger.build_ledger(vault, run_id)
