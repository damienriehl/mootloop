"""Durable, review-only RFP production-suggestion contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from mootloop.models.common import (
    DocId,
    MatterId,
    ProductionSuggestionId,
    RequestId,
    RunId,
    StrictModel,
    VersionedModel,
)

ProductionClassification = Literal["responsive", "non_responsive"]
SuggestionReviewStatus = Literal["needs_review", "accepted", "rejected"]
ProductionDisposition = Literal["produce", "withhold", "defer"]
SuggestionReviewAction = Literal["accept", "reject", "production_review"]
SuggestionReviewChannel = Literal["api", "cli"]
SuggestionExclusionReason = Literal["privileged", "untriaged", "unavailable"]


class ProductionSuggestion(StrictModel):
    """One deterministic classification proposal; never a production authorization."""

    suggestion_id: ProductionSuggestionId
    source_matter_id: MatterId
    run_id: RunId
    request_id: RequestId
    doc_id: DocId
    original_name: str
    source_locator: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    classification: ProductionClassification
    score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=1000)
    created_at: str


class ProductionSuggestionExclusion(StrictModel):
    """Text-free proof that an ineligible document never entered classification."""

    request_id: RequestId
    doc_id: DocId
    original_name: str
    reason: SuggestionExclusionReason


class ProductionSuggestionBundle(VersionedModel):
    schema_version: str = "1.0"
    source_matter_id: MatterId
    run_id: RunId
    suggestions: list[ProductionSuggestion] = Field(default_factory=list)
    exclusions: list[ProductionSuggestionExclusion] = Field(default_factory=list)


class ProductionSuggestionReview(VersionedModel):
    """One append-only human action, separate from the generated classification."""

    schema_version: str = "1.0"
    review_id: str = Field(pattern=r"^prod-review-[0-9a-f]{16}$")
    source_matter_id: MatterId
    run_id: RunId
    suggestion_id: ProductionSuggestionId
    action: SuggestionReviewAction
    actor: str = Field(min_length=1, max_length=320)
    channel: SuggestionReviewChannel
    recorded_at: str
    reason: str = Field(default="", max_length=2000)
    production_disposition: ProductionDisposition | None = None

    @model_validator(mode="after")
    def validate_action(self) -> ProductionSuggestionReview:
        if not self.actor.strip():
            raise ValueError("actor must identify the human reviewer")
        if self.action == "production_review" and self.production_disposition is None:
            raise ValueError("production_review requires a production disposition")
        if self.action != "production_review" and self.production_disposition is not None:
            raise ValueError("classification review cannot record a production disposition")
        return self


class ProductionSuggestionView(StrictModel):
    suggestion_id: ProductionSuggestionId
    source_matter_id: MatterId
    run_id: RunId
    request_id: RequestId
    doc_id: DocId
    original_name: str
    source_locator: str
    request_sha256: str
    document_sha256: str
    classification: ProductionClassification
    score: float
    reason: str
    created_at: str
    review_status: SuggestionReviewStatus = "needs_review"
    production_disposition: ProductionDisposition | None = None
    review_history: list[ProductionSuggestionReview] = Field(default_factory=list)


class ProductionSuggestionResult(StrictModel):
    suggestions: list[ProductionSuggestionView] = Field(default_factory=list)
    exclusions: list[ProductionSuggestionExclusion] = Field(default_factory=list)
