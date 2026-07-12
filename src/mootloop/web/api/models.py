"""Request/response models for the write-tier matter API.

Response envelopes carry an explicit `Literal` discriminator (``kind``) and expose
the domain models' own `Literal` state fields (`Decision.status`, `RunStatus`,
`Attestation.valid`) so the generated OpenAPI schema yields real discriminated
unions for the typed TS client (plan FD-8). The lock-contention body is likewise a
discriminated error state (``error = "lock_held"``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from mootloop.models.attestations import Attestation
from mootloop.models.common import StrictModel, VersionedModel
from mootloop.models.decisions import Decision, ResolutionAction
from mootloop.models.events import RunMode, RunStatus
from mootloop.models.gates import GateResult
from mootloop.models.requests import RequestItem

SCHEMA_VERSION = "1.0"


# --- requests ---------------------------------------------------------------


class ResolveRequest(StrictModel):
    """The body of a decision-resolve call. ``decided_by`` and the timestamp are
    server-derived (from the verified principal), never client-supplied."""

    action: ResolutionAction
    chosen_key: str | None = None
    note: str = ""


class PauseRequest(StrictModel):
    """The optional body of a run-pause call; ``reason`` defaults to ``manual``."""

    reason: str | None = None


class StartRunRequest(StrictModel):
    """The body of a run-start call; ``task`` and ``mode`` mirror ``mootloop run start``."""

    task: str = "discovery-responses"
    mode: RunMode | None = None


class RaiseCapRequest(StrictModel):
    """The body of a raise-cap call — exactly one of an absolute ``to_usd`` cap or a
    ``delta_usd`` increment over the run's current effective cap (plan D5)."""

    to_usd: float | None = None
    delta_usd: float | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> RaiseCapRequest:
        if (self.to_usd is None) == (self.delta_usd is None):
            raise ValueError("provide exactly one of `to_usd` or `delta_usd`")
        return self


# --- responses --------------------------------------------------------------


class CsrfToken(StrictModel):
    """The CSRF token returned alongside the double-submit cookie."""

    csrf_token: str


class RunSummary(VersionedModel):
    """Listing-safe summary of one run under a matter vault (derived from the fold)."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    status: RunStatus
    mode: RunMode
    current_stage: str | None = None
    task: str | None = None
    total_spend_usd: float = 0.0


class ResolveResponse(VersionedModel):
    """A resolved decision. ``decision.status`` is the `Literal` discriminated state."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["decision_resolved"] = "decision_resolved"
    decision: Decision


class AttestResponse(VersionedModel):
    """A recorded attestation. ``attestation.valid`` carries the state."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["attested"] = "attested"
    attestation: Attestation


class RunActionResponse(VersionedModel):
    """The result of a pause/resume/continue/raise-cap call, exposing the run's
    resulting `RunStatus` (the discriminated domain state)."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["run_paused", "run_resumed", "run_continued", "cap_raised"]
    run_id: str
    status: RunStatus


class RunStatusSummary(VersionedModel):
    """Single-run status envelope for the cockpit (folded from the journal). Exposes
    the `RunStatus` Literal; also returned by the start-run wrapper."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["run_status"] = "run_status"
    run_id: str
    status: RunStatus
    mode: RunMode
    current_stage: str | None = None
    task: str | None = None
    total_spend_usd: float = 0.0
    hard_cap_usd: float | None = None
    completed_turns: int = 0
    discarded_turns: int = 0
    open_decisions: list[str] = Field(default_factory=list)


class GateLedgerResponse(VersionedModel):
    """The run's gate ledger plus the per-turn `GateResult` discriminated union
    (`GatePass | GateFail | GatePending`, discriminated on ``status``)."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["gate_ledger"] = "gate_ledger"
    run_id: str
    export_ready: bool
    blockers: list[str] = Field(default_factory=list)
    overall: dict[str, str] = Field(default_factory=dict)
    gates: dict[str, dict[str, str]] = Field(default_factory=dict)
    turn_gates: list[GateResult] = Field(default_factory=list)


class DecisionsResponse(VersionedModel):
    """The run's attorney-gate decisions; each ``decision.status`` is the discriminated
    `DecisionStatus` Literal."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["decisions"] = "decisions"
    run_id: str
    decisions: list[Decision] = Field(default_factory=list)


class RequestsResponse(VersionedModel):
    """The served RFA/discovery request units in scope for the run (matter-scoped)."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["requests"] = "requests"
    run_id: str
    requests: list[RequestItem] = Field(default_factory=list)


class LockContentionBody(StrictModel):
    """The typed HTTP 409 body when a run lock is held (plan: retry-backoff client)."""

    error: Literal["lock_held"] = "lock_held"
    detail: str
    retriable: bool = True
