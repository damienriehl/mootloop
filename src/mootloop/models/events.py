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

# Run lifecycle statuses. ``capped`` is a graceful budget checkpoint (plan D5) —
# a finished state a later ``CapRaised`` can reopen to ``running``.
RunStatus = Literal["running", "finished", "needs_attention", "capped"]


class RunStarted(StrictModel):
    kind: Literal["run_started"] = "run_started"
    run_id: str
    matter_id: str
    task: str
    rubric_version: str
    config_digest: str


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


class RunFinished(StrictModel):
    kind: Literal["run_finished"] = "run_finished"
    status: RunStatus


class CapRaised(StrictModel):
    """The hard budget cap was raised (``mootloop run raise-cap``). Reopens a capped
    run to ``running`` and lifts the effective cap (plan D5, resumable checkpoint)."""

    kind: Literal["cap_raised"] = "cap_raised"
    to_usd: float


JournalEvent = Annotated[
    RunStarted
    | StageStarted
    | TurnCompleted
    | TurnDiscarded
    | GateEvaluated
    | SpendRecorded
    | RunFinished
    | CapRaised,
    Field(discriminator="kind"),
]


class RunState(StrictModel):
    """The derived view a `fold` produces — never persisted, always recomputed."""

    run_id: str | None = None
    matter_id: str | None = None
    task: str | None = None
    rubric_version: str | None = None
    status: RunStatus = "running"
    current_stage: str | None = None
    completed_turns: dict[str, TurnRecord] = Field(default_factory=dict)
    discarded: dict[str, int] = Field(default_factory=dict)
    total_spend_usd: float = 0.0
    total_input_tokens: int = 0
    total_cache_read: int = 0
    total_cache_write: int = 0
    total_output_tokens: int = 0
    cap_raised_to: float | None = None

    @property
    def finished(self) -> bool:
        return self.status != "running"

    def is_completed(self, turn_id: str) -> bool:
        return turn_id in self.completed_turns
