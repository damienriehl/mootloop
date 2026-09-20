"""Versioned, strategy-bound document instructions and eligible evidence."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from mootloop.models.common import MATTER_ID_PATTERN, RequestId, StrictModel, VersionedModel

DocumentTask = Literal["complaint", "motion", "appellate-brief", "oral-argument", "business-advice"]


class DocumentEvidence(StrictModel):
    source_id: str = Field(pattern=MATTER_ID_PATTERN)
    text: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    available_on: date | None = None
    classification: Literal["record", "allegation", "disputed", "hypothetical", "outcome"]
    public: bool = False
    strategy_id: str | None = Field(default=None, pattern=MATTER_ID_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> DocumentEvidence:
        if hashlib.sha256(self.text.encode()).hexdigest() != self.sha256:
            raise ValueError("evidence sha256 does not match exact text")
        return self


class DocumentUnit(StrictModel):
    unit_id: str = Field(pattern=MATTER_ID_PATTERN)
    title: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    issues: tuple[str, ...] = ()

    @property
    def request_id(self) -> RequestId:
        return RequestId(self.unit_id)

    @property
    def text(self) -> str:
        return self.instructions


class DocumentTaskInput(VersionedModel):
    schema_version: Literal["1.0"] = "1.0"
    input_id: str = Field(pattern=MATTER_ID_PATTERN)
    task: DocumentTask
    represented_side: str = Field(min_length=1)
    jurisdiction: str = Field(min_length=1)
    court_level: Literal["trial", "appellate", "supreme", "advisory"]
    cutoff: date
    strategy_id: str = Field(pattern=MATTER_ID_PATTERN)
    assumptions: tuple[str, ...] = ()
    preservation_constraints: tuple[str, ...] = ()
    units: tuple[DocumentUnit, ...] = Field(min_length=1)
    evidence: tuple[DocumentEvidence, ...] = ()

    @model_validator(mode="after")
    def validate_boundaries(self) -> DocumentTaskInput:
        if len({u.unit_id for u in self.units}) != len(self.units):
            raise ValueError("duplicate document unit identity")
        if len({e.source_id for e in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate evidence source identity")
        if (self.task == "business-advice") != (self.court_level == "advisory"):
            raise ValueError("business-advice requires advisory court_level")
        for source in self.evidence:
            if source.classification == "outcome":
                raise ValueError("actual outcome material cannot enter strategy inputs")
            if source.strategy_id not in (None, self.strategy_id):
                raise ValueError("another strategy's evidence cannot enter strategy inputs")
            if source.classification != "hypothetical" and (
                source.available_on is None or source.available_on > self.cutoff
            ):
                raise ValueError("historical evidence must be dated on or before cutoff")
        return self
