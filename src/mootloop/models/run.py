"""Run vocabulary: personas, the `TurnSpec` the orchestrator hands a driver, the
per-schema `TurnOutput` models a persona must return, and the `TurnRecord` a
completed turn folds into.

A turn *completes* only when its raw text parses as valid JSON for its declared
output schema (the derailment contract, plan D11). The schema registry
(`OUTPUT_SCHEMAS`) is the single place output-schema names bind to models.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from mootloop.models.common import RequestId, StrictModel
from mootloop.models.gates import GateResult


class PersonaName(StrEnum):
    """The personas that can own a turn (plan D12 canonical vocabulary)."""

    ASSOCIATE = "associate"
    PARTNER = "partner"
    OC_ASSOCIATE = "oc_associate"
    OC_PARTNER = "oc_partner"
    JUDGE = "judge"
    JUROR = "juror"
    RUBRIC_JUDGE = "rubric_judge"
    CITE_CHECKER = "cite_checker"

    @property
    def body_slug(self) -> str:
        """Filename stem under ``personas/`` (``oc_associate`` -> ``oc-associate``)."""
        return self.value.replace("_", "-")


# --- output schema names ----------------------------------------------------
SCHEMA_DRAFT = "draft"
SCHEMA_CRITIQUE = "critique"
SCHEMA_JUDGE = "judge"


class TurnSpec(StrictModel):
    """Everything a driver needs to run one persona turn — and nothing more.

    ``prompt_context`` carries the injected inputs (request text, facts, prior
    outputs) as plain data; the rendered prompt is assembled from the persona body
    plus this context, never stored here.
    """

    turn_id: str
    run_id: str
    persona: PersonaName
    request_id: RequestId | None = None
    stage: str
    prompt_context: dict[str, Any] = Field(default_factory=dict)
    output_schema_name: str
    attempt: int = 1


# --- persona output schemas -------------------------------------------------


class Objection(StrictModel):
    """A single objection raised in a draft response."""

    basis: str
    text: str


class DraftOutput(StrictModel):
    """An Associate (or bolster) draft response to one request."""

    response_text: str
    objections: list[Objection] = Field(default_factory=list)
    candidate_citations: list[str] = Field(default_factory=list)
    fact_ids_used: list[str] = Field(default_factory=list)
    attorney_gate_items: list[str] = Field(default_factory=list)
    self_assessment: str


class CritiqueOutput(StrictModel):
    """A Partner review or an Opposing-Counsel attack on a draft."""

    verdict: Literal["approve", "revise"]
    critiques: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    self_assessment: str


class ObjectionRuling(StrictModel):
    """One judge's ruling on whether a single objection survives a motion to compel."""

    objection_basis: str
    would_objection_survive: bool
    reasoning: str
    persuasion_notes: str


class JudgeOutput(StrictModel):
    """A judge-panel member's rulings across a request's objections (thin mode)."""

    rulings: list[ObjectionRuling] = Field(default_factory=list)
    self_assessment: str


# The one place schema names bind to models. Drivers/validators look up here.
OUTPUT_SCHEMAS: dict[str, type[StrictModel]] = {
    SCHEMA_DRAFT: DraftOutput,
    SCHEMA_CRITIQUE: CritiqueOutput,
    SCHEMA_JUDGE: JudgeOutput,
}


class TurnRecord(StrictModel):
    """A completed turn: its spec, the validated output (as a dict), the gate
    results it cleared, and when it landed."""

    spec: TurnSpec
    output: dict[str, Any]
    gate_results: list[GateResult] = Field(default_factory=list)
    completed_at: str


class DiscardedTurn(StrictModel):
    """The outcome of a derailed turn — recorded, counter-capped, never repaired."""

    turn_id: str
    reason: str
    attempt: int
