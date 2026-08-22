"""Synthetic persona-oracle keys and deterministic evaluation results."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from mootloop.models.common import StrictModel, VersionedModel
from mootloop.models.run import PersonaName

OracleScalar = str | int | bool | None
OracleFailureCode = Literal[
    "missing_candidate",
    "candidate_identity_mismatch",
    "invalid_output_schema",
    "missing_required_text",
    "forbidden_text",
    "unexpected_value",
]


class PersonaOracleCase(StrictModel):
    """One hidden expectation for a structured synthetic persona result."""

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    persona: PersonaName
    stage: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    output_schema_name: str = Field(min_length=1, max_length=128)
    text_fields: tuple[str, ...]
    required_any: tuple[tuple[str, ...], ...] = ()
    forbidden: tuple[str, ...] = ()
    expected_values: dict[str, OracleScalar] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_expectations(self) -> PersonaOracleCase:
        if not self.text_fields:
            raise ValueError("oracle case must select at least one text field")
        if any(not group or any(not phrase for phrase in group) for group in self.required_any):
            raise ValueError("required_any groups and phrases must be nonempty")
        if any(not phrase for phrase in self.forbidden):
            raise ValueError("forbidden phrases must be nonempty")
        return self


class PersonaOracleAnswerKey(VersionedModel):
    """Versioned synthetic answer key loaded only by an explicit test harness."""

    schema_version: str = "1.0"
    key_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    synthetic_matter_id: str = Field(min_length=1, max_length=128)
    task: str = Field(min_length=1, max_length=128)
    isolation_sentinel: str = Field(min_length=16, max_length=128)
    cases: tuple[PersonaOracleCase, ...]

    @model_validator(mode="after")
    def validate_cases(self) -> PersonaOracleAnswerKey:
        if not self.cases:
            raise ValueError("oracle key must contain at least one case")
        case_ids = [case.case_id for case in self.cases]
        identities = [
            (case.persona, case.stage, case.request_id, case.output_schema_name)
            for case in self.cases
        ]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("oracle case ids must be unique")
        if len(identities) != len(set(identities)):
            raise ValueError("oracle case identities must be unique")
        return self


class OracleCandidate(StrictModel):
    """A completed structured turn supplied to the post-output evaluator."""

    persona: PersonaName
    stage: str
    request_id: str
    output_schema_name: str
    output: dict[str, object]


class OracleFailure(StrictModel):
    case_id: str
    code: OracleFailureCode
    detail: str


class OracleEvaluation(StrictModel):
    key_id: str
    passed: bool
    failures: tuple[OracleFailure, ...] = ()
