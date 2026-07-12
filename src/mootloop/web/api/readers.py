"""Shared fold-derived read helpers for the write-tier matter API.

These REUSE the same read patterns the read-only demo (`mootloop.web.app`) uses —
`gate_ledger.build_ledger`, `DecisionStore.list_all`, `orchestrator.load_request_units`,
and the journal `load_state` fold — but this module NEVER imports `web.app` (an
invariant test enforces the separation). Every helper is a pure read: no writes, no
LLM calls, no secrets.
"""

from __future__ import annotations

from pathlib import Path

from mootloop import gate_ledger, orchestrator
from mootloop.decisions import DecisionStore
from mootloop.journal import load_state, read_events
from mootloop.models.events import GateEvaluated, RunState
from mootloop.vault import load_matter
from mootloop.web.api import models


def effective_cap(vault: Path, state: RunState) -> float | None:
    """The cap now in force: a ``CapRaised`` override wins over ``matter.yaml`` (mirrors
    ``orchestrator._effective_cap`` without importing a private)."""
    if state.cap_raised_to is not None:
        return state.cap_raised_to
    return load_matter(vault).budget.hard_cap_usd


def run_status_summary(vault: Path, run_id: str) -> models.RunStatusSummary:
    """Fold a single run's status into the cockpit envelope."""
    state = load_state(vault, run_id)
    open_decisions = DecisionStore(vault, run_id).list_open()
    return models.RunStatusSummary(
        run_id=run_id,
        status=state.status,
        mode=state.mode,
        current_stage=state.current_stage,
        task=state.task,
        total_spend_usd=round(state.total_spend_usd, 6),
        hard_cap_usd=effective_cap(vault, state),
        completed_turns=len(state.completed_turns),
        discarded_turns=len(state.discarded),
        open_decisions=[d.decision_id for d in open_decisions],
    )


def gate_ledger_response(vault: Path, run_id: str) -> models.GateLedgerResponse:
    """The gate ledger (per-request statuses + export predicate) plus the raw per-turn
    `GateResult` discriminated union recorded on the journal."""
    doc = gate_ledger.build_ledger(vault, run_id)
    turn_gates = [
        event.result for event in read_events(vault, run_id) if isinstance(event, GateEvaluated)
    ]
    return models.GateLedgerResponse(
        run_id=run_id,
        export_ready=doc.export_ready,
        blockers=list(doc.blockers),
        overall=dict(doc.overall),
        gates=doc.gates,
        turn_gates=turn_gates,
    )


def decisions_response(vault: Path, run_id: str) -> models.DecisionsResponse:
    """The run's attorney-gate decisions, stably ordered by id."""
    decisions = DecisionStore(vault, run_id).list_all()
    decisions.sort(key=lambda d: d.decision_id)
    return models.DecisionsResponse(run_id=run_id, decisions=decisions)


def requests_response(vault: Path, run_id: str) -> models.RequestsResponse:
    """The served RFA/discovery request units in scope for the run (matter-scoped)."""
    units = orchestrator.load_request_units(vault)
    return models.RequestsResponse(run_id=run_id, requests=units)
