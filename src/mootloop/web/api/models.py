"""Request/response models for the write-tier matter API.

Response envelopes carry an explicit `Literal` discriminator (``kind``) and expose
the domain models' own `Literal` state fields (`Decision.status`, `RunStatus`,
`Attestation.valid`) so the generated OpenAPI schema yields real discriminated
unions for the typed TS client (plan FD-8). The lock-contention body is likewise a
discriminated error state (``error = "lock_held"``).
"""

from __future__ import annotations

from typing import Literal

from mootloop.models.attestations import Attestation
from mootloop.models.common import StrictModel, VersionedModel
from mootloop.models.decisions import Decision, ResolutionAction
from mootloop.models.events import RunMode, RunStatus

SCHEMA_VERSION = "1.0"


# --- requests ---------------------------------------------------------------


class ResolveRequest(StrictModel):
    """The body of a decision-resolve call. ``decided_by`` and the timestamp are
    server-derived (from the verified principal), never client-supplied."""

    action: ResolutionAction
    chosen_key: str | None = None
    note: str = ""


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


class LockContentionBody(StrictModel):
    """The typed HTTP 409 body when a run lock is held (plan: retry-backoff client)."""

    error: Literal["lock_held"] = "lock_held"
    detail: str
    retriable: bool = True
