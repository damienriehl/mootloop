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

    @property
    def role(self) -> str:
        """Budget model-mix role (plan D5): ``personas`` | ``judges`` | ``rubric`` |
        ``cite``. A run's tier maps each role to a concrete model at plan time."""
        return _PERSONA_ROLE[self]


# Persona -> budget model-mix role (plan D5 tiers vary model per role).
_PERSONA_ROLE: dict[PersonaName, str] = {
    PersonaName.ASSOCIATE: "personas",
    PersonaName.PARTNER: "personas",
    PersonaName.OC_ASSOCIATE: "personas",
    PersonaName.OC_PARTNER: "personas",
    PersonaName.JUDGE: "judges",
    PersonaName.JUROR: "judges",
    PersonaName.RUBRIC_JUDGE: "rubric",
    PersonaName.CITE_CHECKER: "cite",
}


# --- output schema names ----------------------------------------------------
SCHEMA_DRAFT = "draft"
SCHEMA_CRITIQUE = "critique"
SCHEMA_JUDGE = "judge"
SCHEMA_RUBRIC = "rubric_score"


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
    model: str | None = None  # resolved from the run's budget tier at plan time (D5)


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


class CriterionScore(StrictModel):
    """One rubric-judge score for a single (correctness) criterion, 0-5."""

    criterion_id: str
    score: int = Field(ge=0, le=5)
    evidence: str


class RubricScoreOutput(StrictModel):
    """A rubric judge's numeric scores against the injected LOCKED rubric.

    Only *correctness* criteria are scored here — presence criteria are checked
    deterministically in code and never sent to the judge (plan D6/D7).
    """

    scores: list[CriterionScore] = Field(default_factory=list)
    overall_notes: str
    self_assessment: str


# The one place schema names bind to models. Drivers/validators look up here.
OUTPUT_SCHEMAS: dict[str, type[StrictModel]] = {
    SCHEMA_DRAFT: DraftOutput,
    SCHEMA_CRITIQUE: CritiqueOutput,
    SCHEMA_JUDGE: JudgeOutput,
    SCHEMA_RUBRIC: RubricScoreOutput,
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
