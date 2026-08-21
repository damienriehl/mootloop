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
from mootloop.models.production import (
    ProductionDisposition,
    ProductionSuggestionExclusion,
    ProductionSuggestionView,
    SuggestionReviewAction,
)
from mootloop.models.requests import RequestItem
from mootloop.models.run import AttentionBlocker
from mootloop.models.taskspec import TaskSpec, TaskSpecLock

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
    """The body of a run-start call; ``task`` and ``mode`` mirror ``mootloop run start``.

    ``task_spec_id`` optionally binds the run to a resolved on-ramp TaskSpec (plan
    FE-2.5); it is recorded on the ``RunStarted`` journal event."""

    run_id: str
    task: str = "discovery-responses"
    mode: RunMode | None = None
    task_spec_id: str | None = None


class FreeformTaskRequest(StrictModel):
    """The body of the freeform on-ramp: an attorney's free-text task intent."""

    intent_text: str


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


class ReopenRunRequest(StrictModel):
    """The body of a reopen call — the operator's logged ``reason`` (required), an
    optional grant of extra retry attempts to clear counter-capped turns."""

    reason: str
    grant_attempts: int = 0

    @model_validator(mode="after")
    def _non_empty_reason(self) -> ReopenRunRequest:
        if not self.reason.strip():
            raise ValueError("`reason` must be non-empty — it is the audit trail")
        if self.grant_attempts < 0:
            raise ValueError("`grant_attempts` must be >= 0")
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


class CurrentAttestation(Attestation):
    """The complete v2 commitment returned by a successful human attest action."""

    schema_version: Literal["2.0"] = "2.0"
    hash_scope: Literal["run-review-state:v2"] = "run-review-state:v2"
    journal_sha256: str
    decisions_sha256: str
    fact_state_sha256: str
    access_audit_head_sha256: str
    commitment_sha256: str


class AttestResponse(VersionedModel):
    """A recorded complete v2 attorney commitment."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["attested"] = "attested"
    attestation: CurrentAttestation


class RunActionResponse(VersionedModel):
    """The result of a pause/resume/continue/raise-cap/reopen call, exposing the run's
    resulting `RunStatus` (the discriminated domain state)."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["run_paused", "run_resumed", "run_continued", "cap_raised", "run_reopened"]
    run_id: str
    status: RunStatus


class CitationCheckQueuedResponse(VersionedModel):
    """A durable hosted cite-checker job accepted for one run."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["citation_check_queued"] = "citation_check_queued"
    run_id: str
    item_id: str
    status: Literal["queued"] = "queued"


class JudgeProfileQueuedResponse(VersionedModel):
    schema_version: str = SCHEMA_VERSION
    kind: Literal["judge_profile_queued"] = "judge_profile_queued"
    item_id: str
    status: Literal["queued"] = "queued"


class ProductionSuggestionsQueuedResponse(VersionedModel):
    schema_version: str = SCHEMA_VERSION
    kind: Literal["production_suggestions_queued"] = "production_suggestions_queued"
    run_id: str
    item_id: str
    status: Literal["queued"] = "queued"


class ProductionSuggestionsResponse(VersionedModel):
    schema_version: str = SCHEMA_VERSION
    kind: Literal["production_suggestions"] = "production_suggestions"
    run_id: str
    suggestions: list[ProductionSuggestionView] = Field(default_factory=list)
    exclusions: list[ProductionSuggestionExclusion] = Field(default_factory=list)


class ProductionSuggestionResponse(VersionedModel):
    schema_version: str = SCHEMA_VERSION
    kind: Literal["production_suggestion"] = "production_suggestion"
    suggestion: ProductionSuggestionView


class ProductionSuggestionReviewRequest(StrictModel):
    action: SuggestionReviewAction
    production_disposition: ProductionDisposition | None = None
    reason: str = Field(default="", max_length=2000)


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
    replayable: bool
    context_blocker: str | None = None
    completed_turns: int = 0
    discarded_turns: int = 0
    open_decisions: list[str] = Field(default_factory=list)
    attention_blockers: list[AttentionBlocker] = Field(default_factory=list)


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


# --- on-ramp: TaskSpec ------------------------------------------------------


class TaskSpecResponse(VersionedModel):
    """A TaskSpec with distinct resolution, human-lock, and launch-ready states."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["task_spec"] = "task_spec"
    task_spec: TaskSpec
    resolved: bool
    locked: bool
    runnable: bool


class TaskSpecLockResponse(VersionedModel):
    """The exact human approval now governing a TaskSpec launch."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["task_spec_locked"] = "task_spec_locked"
    task_spec_lock: TaskSpecLock


class TaskSpecsResponse(VersionedModel):
    """Every recorded TaskSpec for a matter (append order)."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["task_specs"] = "task_specs"
    specs: list[TaskSpec] = Field(default_factory=list)


# --- export: deliverables + signed links ------------------------------------


class DeliverableInfo(StrictModel):
    """One deliverable file with its DRAFT/clean state and download eligibility."""

    name: str
    size_bytes: int
    is_draft: bool
    requires_export_ready: bool
    downloadable: bool


class DeliverablesResponse(VersionedModel):
    """The run's deliverables plus the export-ready predicate (the colophon gate)."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["deliverables"] = "deliverables"
    run_id: str
    export_ready: bool
    deliverables: list[DeliverableInfo] = Field(default_factory=list)


class SignedLinkResponse(VersionedModel):
    """A minted short-expiry download link bound to one (matter, run, deliverable)."""

    schema_version: str = SCHEMA_VERSION
    kind: Literal["signed_link"] = "signed_link"
    run_id: str
    doc: str
    url: str
    token: str
    is_draft: bool
    expires_at: str


class ExportNotReadyBody(StrictModel):
    """The typed HTTP 403 body when a clean deliverable is not yet export-ready."""

    error: Literal["export_not_ready"] = "export_not_ready"
    detail: str
    blockers: list[str] = Field(default_factory=list)


class InvalidLinkBody(StrictModel):
    """The typed HTTP 400 body when a download token is tampered/expired/unknown."""

    error: Literal["invalid_link"] = "invalid_link"
    detail: str
