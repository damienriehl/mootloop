"""Authored demo recipes, kept separate from operational state and editorial approval."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from mootloop.models.common import StrictModel, VersionedModel
from mootloop.models.demo import ClaimSupport, DemoDescriptor, Identity, SourceReference, Text
from mootloop.models.run import (
    CritiqueOutput,
    DraftOutput,
    JudgeOutput,
    NarrativeAssessment,
    RubricScoreOutput,
)


class AuthoredUnit(StrictModel):
    initial: DraftOutput
    revised: DraftOutput
    critique: CritiqueOutput
    assessment: NarrativeAssessment
    initial_rubric: RubricScoreOutput
    rubric: RubricScoreOutput
    judge: JudgeOutput | None = None


class AuthoredStrategy(StrictModel):
    strategy_id: Identity
    title: Text
    input_ids: tuple[Identity, ...] = ()
    input_summary: Text
    assumptions: tuple[Text, ...] = ()
    source_ids: tuple[Identity, ...] = ()
    outputs: dict[str, AuthoredUnit] = Field(min_length=1)


class DemoPreparation(VersionedModel):
    schema_version: Literal["1.0"] = "1.0"
    descriptor: DemoDescriptor
    strategies: tuple[AuthoredStrategy, ...] = Field(min_length=1)
    sources: tuple[SourceReference, ...] = ()
    claims: tuple[ClaimSupport, ...] = ()
    comparison: Text | None = None
    actual_outcome: Text | None = None
    outcome_source_ids: tuple[Identity, ...] = ()
    limitations: tuple[Text, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_strategy_inputs(self) -> DemoPreparation:
        if len({strategy.strategy_id for strategy in self.strategies}) != len(self.strategies):
            raise ValueError("duplicate preparation strategy")
        document = self.descriptor.task != "discovery-responses"
        if any(bool(strategy.input_ids) != document for strategy in self.strategies):
            raise ValueError("document strategies require explicit input identities")
        if self.descriptor.collection == "public-record" and len(self.strategies) < 2:
            raise ValueError("public-record preparation requires two alternatives")
        return self
