"""The gate ledger reports the OPERATIVE draft's blocking-gate status.

A fabrication finding raised on an early draft and genuinely cured in the draft that
supersedes it must stop blocking export — while a finding still present in the draft
that will actually be served must keep blocking it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mootloop import gate_ledger
from mootloop.journal import read_events
from mootloop.llm import FakeLLMProvider
from mootloop.models.events import GateEvaluated
from mootloop.orchestrator import run_with_provider, start_run
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
