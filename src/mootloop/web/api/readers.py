"""Shared fold-derived read helpers for the write-tier matter API.

These REUSE the same read patterns the read-only demo (`mootloop.web.app`) uses —
`gate_ledger.build_ledger`, `DecisionStore.list_all`, `load_run_context`, and the
journal `load_state` fold — but this module NEVER imports `web.app` (an
invariant test enforces the separation). Every helper is a pure read: no writes, no
LLM calls, no secrets.
"""

from __future__ import annotations

from pathlib import Path

from mootloop import gate_ledger, orchestrator
from mootloop.context import load_run_context
from mootloop.decisions import DecisionStore
from mootloop.errors import OrchestratorError, RunNotFoundError
from mootloop.journal import load_state, read_events
from mootloop.models.events import GateEvaluated, RunStarted, RunState
from mootloop.web.api import models


def effective_cap(vault: Path, run_id: str, state: RunState) -> float | None:
    """The cap now in force: a journaled override wins over launch context."""
    return orchestrator.effective_cap(state, load_run_context(vault, run_id))


def run_status_summary(vault: Path, run_id: str) -> models.RunStatusSummary:
    """Fold a single run's status into the cockpit envelope."""
    events = read_events(vault, run_id)
    if not any(isinstance(event, RunStarted) for event in events):
        if not events:
            raise RunNotFoundError(f"run {run_id!r} was not found")
        raise OrchestratorError(f"run {run_id!r} has journal events but no RunStarted event")
    state = load_state(vault, run_id)
    open_decisions = DecisionStore(vault, run_id).list_open()
    context_blocker: str | None = None
    try:
        hard_cap_usd = effective_cap(vault, run_id, state)
    except OrchestratorError as exc:
        hard_cap_usd = state.cap_raised_to
        context_blocker = str(exc)
    return models.RunStatusSummary(
        run_id=run_id,
        status=state.status,
        mode=state.mode,
        current_stage=state.current_stage,
        task=state.task,
        total_spend_usd=round(state.total_spend_usd, 6),
        hard_cap_usd=hard_cap_usd,
        replayable=context_blocker is None,
        context_blocker=context_blocker,
        pause_reason=state.pause_reason,
        completed_turns=len(state.completed_turns),
        discarded_turns=len(state.discarded),
        open_decisions=[d.decision_id for d in open_decisions],
        attention_blockers=(
            orchestrator.attention_blockers(vault, run_id)
            if state.status == "needs_attention" and context_blocker is None
            else []
        ),
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
    load_run_context(vault, run_id)
    decisions = DecisionStore(vault, run_id).list_all()
    decisions.sort(key=lambda d: d.decision_id)
    return models.DecisionsResponse(run_id=run_id, decisions=decisions)


def requests_response(vault: Path, run_id: str) -> models.RequestsResponse:
    """The served RFA/discovery request units in scope for the run (matter-scoped)."""
    units = load_run_context(vault, run_id).units
    return models.RequestsResponse(run_id=run_id, requests=units)
