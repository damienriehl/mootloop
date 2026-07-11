"""DECISION vocabulary (plan P-28 / D11 / D12): the propose-then-approve attorney
gate. A persona proposes a call with reasoning and options; a human (or a signed
policy) resolves it. Decisions are their own source-of-truth entity — a schema
adapted from the qa DECISION pattern, never the qa-artifact renderer.

Decisions are persisted append-only (``runs/<run-id>/decisions/decisions.jsonl``)
with a write-once JSON sidecar per decision; the current view is the latest record
per ``decision_id``. A run cannot finish while a *hard-human* decision is open, and
nothing exports while any decision is open (the gate ledger enforces the latter).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from mootloop.models.common import DecisionId, RequestId, StrictModel, VersionedModel

SCHEMA_VERSION = "1.0"


class DecisionKind(StrEnum):
    """The four propose-then-approve attorney gates (plan P-28)."""

    OBJECTION_POSTURE = "objection_posture"
    PRIVILEGE_CALL = "privilege_call"
    RFA_DISPOSITION = "rfa_disposition"
    UNSUPPORTED_ASSERTION = "unsupported_assertion"


# Gate name (matter.yaml ``gates[].name``) each decision kind resolves its mode from.
GATE_NAME_FOR_KIND: dict[DecisionKind, str] = {
    DecisionKind.OBJECTION_POSTURE: "objection_posture",
    DecisionKind.PRIVILEGE_CALL: "privilege",
    DecisionKind.RFA_DISPOSITION: "rfa_disposition",
    DecisionKind.UNSUPPORTED_ASSERTION: "unsupported_assertion",
}

DecisionStatus = Literal["open", "approved", "modified", "denied"]
ResolutionAction = Literal["approve", "modify", "deny"]
ResolutionSource = Literal["human", "policy"]


class DecisionOption(StrictModel):
    """One selectable course of action, with the consequence of choosing it."""

    key: str
    label: str
    consequence: str


class DecisionProposal(StrictModel):
    """A persona's proposed call: what it is, why, and the options to choose among."""

    summary: str
    reasoning: str
    options: list[DecisionOption] = Field(default_factory=list)
    recommended: str


class DecisionResolution(StrictModel):
    """The recorded outcome of a decision — how it was resolved and by whom."""

    action: ResolutionAction
    chosen_key: str | None = None
    note: str = ""
    decided_by: str
    source: ResolutionSource
    decided_at: str


class Decision(VersionedModel):
    """One attorney-gate decision (proposal + optional resolution)."""

    schema_version: str = SCHEMA_VERSION
    decision_id: DecisionId
    run_id: str
    request_id: RequestId | None = None
    kind: DecisionKind
    proposal: DecisionProposal
    status: DecisionStatus = "open"
    resolution: DecisionResolution | None = None

    @property
    def dedupe_key(self) -> tuple[str, str]:
        """Logical identity used to keep generation idempotent across redraft turns:
        two decisions with the same (kind, proposal summary) are the same gate."""
        return (self.kind.value, self.proposal.summary)


def make_decision_id(run_id: str, seq: int) -> DecisionId:
    """``dec-<run>-<seq>`` (plan D12)."""
    return DecisionId(f"dec-{run_id}-{seq:04d}")


# Resolution action -> the status it drives the decision to.
STATUS_FOR_ACTION: dict[ResolutionAction, DecisionStatus] = {
    "approve": "approved",
    "modify": "modified",
    "deny": "denied",
}
