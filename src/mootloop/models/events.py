"""Journal events (a discriminated union on ``kind``) and the `RunState` that a pure
`fold` derives from them.

The journal is the single source of truth for a run; state is *always* derived by
replaying events, so a resume after a kill is exactly a re-fold (plan D10).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from mootloop.models.common import StrictModel
from mootloop.models.gates import GateResult
from mootloop.models.run import TurnRecord

# Run lifecycle statuses (plan D5/Phase 5). ``capped`` is a graceful budget
# checkpoint reopened by ``CapRaised``; ``needs_decisions`` is a finish blocked on an
# open hard-human attorney gate (reopened when it resolves); ``checkpoint`` is a
# gated-mode stage-boundary pause reopened by ``CheckpointCleared``; ``paused`` is an
# operator/worker pause reopened by ``RunResumed`` (plan FE-1).
RunStatus = Literal[
    "running",
    "finished",
    "needs_attention",
    "capped",
    "needs_decisions",
    "checkpoint",
    "paused",
]

# Run execution mode (plan D12): autonomous batches gates, gated pauses at
# checkpoints, observed streams a STATUS.md view.
RunMode = Literal["autonomous", "gated", "observed"]


class RunStarted(StrictModel):
    kind: Literal["run_started"] = "run_started"
    run_id: str
    matter_id: str
    task: str
    rubric_version: str
    config_digest: str
    mode: RunMode = "autonomous"


class StageStarted(StrictModel):
    kind: Literal["stage_started"] = "stage_started"
    stage: str


class TurnCompleted(StrictModel):
    kind: Literal["turn_completed"] = "turn_completed"
    record: TurnRecord


class TurnDiscarded(StrictModel):
    kind: Literal["turn_discarded"] = "turn_discarded"
    turn_id: str
    reason: str
    attempt: int


class GateEvaluated(StrictModel):
    kind: Literal["gate_evaluated"] = "gate_evaluated"
    turn_id: str
    result: GateResult


class SpendRecorded(StrictModel):
    kind: Literal["spend_recorded"] = "spend_recorded"
    turn_id: str
    input_tokens: int
    cache_read: int
    cache_write: int
    output_tokens: int
    model: str
    usd_equiv: float
    billing_mode: Literal["subscription", "api"] = "subscription"


class RunFinished(StrictModel):
    kind: Literal["run_finished"] = "run_finished"
    status: RunStatus


class CapRaised(StrictModel):
    """The hard budget cap was raised (``mootloop run raise-cap``). Reopens a capped
    run to ``running`` and lifts the effective cap (plan D5, resumable checkpoint)."""

    kind: Literal["cap_raised"] = "cap_raised"
    to_usd: float


class DecisionRecorded(StrictModel):
    """An attorney-gate decision was resolved (plan P-28/D11). The authoritative copy
    lives in ``decisions/decisions.jsonl``; this is the journal's audit trail."""

    kind: Literal["decision_recorded"] = "decision_recorded"
    decision_id: str
    decision_kind: str
    action: str
    status: str
    decided_by: str
    source: str
    decided_at: str


class CheckpointReached(StrictModel):
    """A gated-mode run paused at a stage boundary (or on open policy-delegable
    decisions). Cleared by ``CheckpointCleared`` (``mootloop run continue``)."""

    kind: Literal["checkpoint_reached"] = "checkpoint_reached"
    boundary: str


class CheckpointCleared(StrictModel):
    """The operator cleared a gated-mode checkpoint; the run reopens to ``running``."""

    kind: Literal["checkpoint_cleared"] = "checkpoint_cleared"
    boundary: str


class RunPaused(StrictModel):
    """The run paused without finishing (plan FE-1). ``reason`` is generic free-form
    (e.g. ``"capacity"`` | ``"drain"`` | ``"manual"``). A paused run is non-terminal:
    it stops ticking but is not complete, and ``RunResumed`` reopens it to ``running``."""

    kind: Literal["run_paused"] = "run_paused"
    reason: str


class RunResumed(StrictModel):
    """The operator/worker resumed a paused run; it reopens to ``running`` (plan FE-1)."""

    kind: Literal["run_resumed"] = "run_resumed"


class TurnIntent(StrictModel):
    """A spend write-ahead ledger entry (plan FD-6): recorded *before* a turn calls the
    provider. It is folded as *pending* spend until the matching ``TurnCompleted`` /
    ``SpendRecorded`` for the same ``turn_id`` reconciles it. The cap counts every
    unreconciled intent at ``max_plausible_usd`` (conservative), so an in-flight turn
    can never push a run past its budget cap unnoticed."""

    kind: Literal["turn_intent"] = "turn_intent"
    turn_id: str
    model: str
    billing_mode: Literal["subscription", "api"]
    max_plausible_usd: float


JournalEvent = Annotated[
    RunStarted
    | StageStarted
    | TurnCompleted
    | TurnDiscarded
    | GateEvaluated
    | SpendRecorded
    | RunFinished
    | CapRaised
    | DecisionRecorded
    | CheckpointReached
    | CheckpointCleared
    | RunPaused
    | RunResumed
    | TurnIntent,
    Field(discriminator="kind"),
]


class RunState(StrictModel):
    """The derived view a `fold` produces — never persisted, always recomputed."""

    run_id: str | None = None
    matter_id: str | None = None
    task: str | None = None
    rubric_version: str | None = None
    mode: RunMode = "autonomous"
    status: RunStatus = "running"
    current_stage: str | None = None
    completed_turns: dict[str, TurnRecord] = Field(default_factory=dict)
    discarded: dict[str, int] = Field(default_factory=dict)
    cleared_checkpoints: set[str] = Field(default_factory=set)
    total_spend_usd: float = 0.0
    total_input_tokens: int = 0
    total_cache_read: int = 0
    total_cache_write: int = 0
    total_output_tokens: int = 0
    cap_raised_to: float | None = None
    # Write-ahead spend ledger (plan FD-6): turn_id -> max_plausible_usd for intents
    # that have NOT yet been reconciled by their matching TurnCompleted/SpendRecorded.
    pending_intents: dict[str, float] = Field(default_factory=dict)

    @property
    def finished(self) -> bool:
        """Not schedulable *right now* — any non-``running`` status. This includes the
        non-terminal pauses (``paused`` / ``checkpoint`` / ``needs_decisions``): the
        planner emits no work while they hold. Use ``is_terminal`` to ask whether the
        run has completed for good."""
        return self.status != "running"

    @property
    def is_terminal(self) -> bool:
        """The run is complete for good — a terminal state no resume reopens.
        ``paused`` / ``checkpoint`` / ``needs_decisions`` / ``running`` are all
        NON-terminal (they stop ticking but are not done); only ``finished`` /
        ``needs_attention`` / ``capped`` are terminal (plan FE-1)."""
        return self.status in ("finished", "needs_attention", "capped")

    def is_completed(self, turn_id: str) -> bool:
        return turn_id in self.completed_turns
