"""Every provider call that burned tokens must be metered — including the turns the
orchestrator throws away.

`record_turn` has three early returns that skip the normal `SpendRecorded` append: a
schema-invalid discard, a degeneracy discard, and the already-completed replay. Real
money was spent on all three, so all three must reach the ledger; only an exact
replay of an already-booked result may be suppressed.
"""

from __future__ import annotations

import json
from pathlib import Path

from mootloop.budget import MODEL_OPUS
from mootloop.journal import load_state, read_events
from mootloop.llm import FakeLLMProvider, RawTurnResult, TokenUsage
from mootloop.models.events import SpendRecorded
from mootloop.models.run import DiscardedTurn
from mootloop.orchestrator import plan_next, record_turn, start_run
from mootloop.stages import render_prompt
from tests.unit.test_orchestrator_planning import (
    NOW,
    _build_single_request_vault,
)

# A real, priced model — the fake provider's "fake" id is deliberately free.
USAGE = TokenUsage(
    input_tokens=200_000,
    cache_read=0,
    cache_write=0,
    output_tokens=40_000,
    model=MODEL_OPUS,
)
# 200_000 * $5/1e6 + 40_000 * $25/1e6 = $1.00 + $1.00
EXPECTED_USD = 2.0


def _spend_events(vault: Path, run_id: str) -> list[SpendRecorded]:
    return [e for e in read_events(vault, run_id) if isinstance(e, SpendRecorded)]


def test_schema_invalid_discard_still_books_its_spend(tmp_path: Path) -> None:
    """A turn whose output failed schema validation still burned provider tokens."""
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="spend-0001")
    spec = plan_next(vault, run_id)[0]

    result = record_turn(vault, run_id, spec.turn_id, "not valid json", USAGE, NOW)
    assert isinstance(result, DiscardedTurn)

    state = load_state(vault, run_id)
    assert state.total_spend_usd == EXPECTED_USD
    assert state.total_input_tokens == USAGE.input_tokens
    assert state.total_output_tokens == USAGE.output_tokens
    assert [e.turn_id for e in _spend_events(vault, run_id)] == [spec.turn_id]


def test_degeneracy_discard_still_books_its_spend(tmp_path: Path) -> None:
    """A schema-valid but degenerate draft is discarded — and was paid for."""
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="spend-0002")
    spec = plan_next(vault, run_id)[0]

    degenerate = json.dumps(
        {
            "response_text": "   ",  # empty -> degeneracy gate fails
            "objections": [],
            "candidate_citations": [],
            "fact_ids_used": [],
            "attorney_gate_items": [],
            "rfa_disposition": None,
            "self_assessment": "nothing to say",
        }
    )
    result = record_turn(vault, run_id, spec.turn_id, degenerate, USAGE, NOW)
    assert isinstance(result, DiscardedTurn)
    assert "degenerate" in result.reason

    state = load_state(vault, run_id)
    assert state.total_spend_usd == EXPECTED_USD
    assert [e.turn_id for e in _spend_events(vault, run_id)] == [spec.turn_id]


def test_replayed_completed_turn_books_new_spend_but_never_double_books(
    tmp_path: Path,
) -> None:
    """The already-completed early return is idempotent for the RECORD, never for the
    money: a *re-executed* turn burned fresh tokens and must be metered, while an
    exact replay of an already-booked result must not be counted twice."""
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="spend-0003")
    provider = FakeLLMProvider()
    spec = plan_next(vault, run_id)[0]
    turn: RawTurnResult = provider.run_turn(spec, render_prompt(spec))

    first = record_turn(
        vault, run_id, spec.turn_id, turn.text, USAGE, NOW, provider_call_id="call-1"
    )
    assert load_state(vault, run_id).total_spend_usd == EXPECTED_USD

    # Same turn, a second real provider call with identical usage — new money.
    again = record_turn(
        vault, run_id, spec.turn_id, turn.text, USAGE, NOW, provider_call_id="call-2"
    )
    assert again == first  # the stored record still wins
    assert load_state(vault, run_id).total_spend_usd == EXPECTED_USD * 2

    # Replaying the exact same provider call books nothing further.
    record_turn(
        vault, run_id, spec.turn_id, turn.text, USAGE, NOW, provider_call_id="call-2"
    )
    assert load_state(vault, run_id).total_spend_usd == EXPECTED_USD * 2
    assert len(_spend_events(vault, run_id)) == 2
