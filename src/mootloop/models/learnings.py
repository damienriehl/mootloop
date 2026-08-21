"""Edited-work-product recovery, proposal, review, and shared-learning records."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from mootloop.models.common import (
    LearningImportId,
    LearningProposalId,
    MatterId,
    MatterProvenanced,
    RunId,
    StrictModel,
    VersionedModel,
)
from mootloop.models.context import ContextContribution

AnchorRecoveryStatus = Literal["exact", "sentinel", "ambiguous", "missing"]
RevisionKind = Literal["insertion", "deletion"]
LearningTier = Literal["matter", "firm", "area"]
LearningStatus = Literal["needs_review", "accepted", "rejected"]
LearningReviewAction = Literal["accept", "reject", "promote"]
LearningReviewChannel = Literal["api", "cli"]


class DocxRevision(StrictModel):
    """One tracked OOXML insertion or deletion inside a recovered anchor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: RevisionKind
    text: str
    author: str | None = None
    date: str | None = None


class RecoveredAnchor(StrictModel):
    """The original/current text views recovered for one expected stable anchor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    anchor_id: str
    status: AnchorRecoveryStatus
    original_text: str = ""
    current_text: str = ""
    revisions: tuple[DocxRevision, ...] = ()


class DocxEditRecovery(StrictModel):
    """Bounded recovery result; blockers require human review before any routing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_sha256: str
    anchors: tuple[RecoveredAnchor, ...]
    auto_routable: bool
    blockers: tuple[str, ...] = ()


class LearningImportRecord(StrictModel):
    """One exact edited-document recovery attempt, including blocked imports."""

    import_id: LearningImportId
    source_matter_id: MatterId
    run_id: RunId
    source_name: str = Field(min_length=1, max_length=255)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    imported_at: str
    auto_routable: bool
    blockers: tuple[str, ...] = ()
    anchors: tuple[RecoveredAnchor, ...] = ()


class LearningProposal(StrictModel):
    """One deterministic anchored correction awaiting human disposition."""

    proposal_id: LearningProposalId
    import_id: LearningImportId
    source_matter_id: MatterId
    run_id: RunId
    task: str
    anchor_id: str
    baseline_text: str
    edited_text: str
    baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    edited_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    critic_markup: str
    word_changes: int = Field(ge=1)
    proposed_tier: LearningTier = "matter"
    proposed_text: str = Field(min_length=1, max_length=131_072)
    created_at: str


class LearningImportBundle(VersionedModel):
    """Write-once import transaction containing recovery and all derived proposals."""

    schema_version: str = "1.0"
    import_record: LearningImportRecord
    proposals: list[LearningProposal] = Field(default_factory=list)


class LearningReview(VersionedModel):
    """Append-only human acceptance, rejection, or cross-matter promotion."""

    schema_version: str = "1.0"
    review_id: str = Field(pattern=r"^learning-review-[0-9a-f]{16}$")
    source_matter_id: MatterId
    proposal_id: LearningProposalId
    action: LearningReviewAction
    actor: str = Field(min_length=1, max_length=320)
    channel: LearningReviewChannel
    recorded_at: str
    reviewed_text: str = Field(default="", max_length=131_072)
    target_tier: LearningTier | None = None
    reason: str = Field(default="", max_length=2_000)
    confirm_scrub_diff: bool = False
    scrub_diff_sha256: str | None = None
    excluded_matter_ids: tuple[MatterId, ...] = ()

    @model_validator(mode="after")
    def validate_action(self) -> LearningReview:
        if not self.actor.strip():
            raise ValueError("actor must identify the human reviewer")
        if self.action == "accept" and not self.reviewed_text.strip():
            raise ValueError("accept requires reviewed learning text")
        if self.action == "reject" and (self.target_tier is not None or self.reviewed_text):
            raise ValueError("reject cannot write learning text or select a tier")
        if self.action == "promote":
            if self.target_tier not in ("firm", "area"):
                raise ValueError("promote requires firm or area target tier")
            if not self.reviewed_text.strip():
                raise ValueError("promote requires reviewed learning text")
            if self.scrub_diff_sha256 is None or not re.fullmatch(
                r"[0-9a-f]{64}", self.scrub_diff_sha256
            ):
                raise ValueError("promote requires the exact rendered scrub-diff SHA-256")
            if len(set(self.excluded_matter_ids)) != len(self.excluded_matter_ids):
                raise ValueError("excluded_matter_ids may not contain duplicates")
        elif self.target_tier is not None:
            raise ValueError("only promote may select a target tier")
        elif self.scrub_diff_sha256 is not None:
            raise ValueError("only promote may bind a rendered scrub diff")
        elif self.excluded_matter_ids:
            raise ValueError("only promote may declare ethical-wall exclusions")
        return self


class LearningProposalView(StrictModel):
    """Current folded state plus immutable proposal and human history."""

    proposal_id: LearningProposalId
    import_id: LearningImportId
    source_matter_id: MatterId
    run_id: RunId
    task: str
    anchor_id: str
    baseline_text: str
    edited_text: str
    baseline_sha256: str
    edited_sha256: str
    critic_markup: str
    word_changes: int
    proposed_tier: LearningTier
    proposed_text: str
    created_at: str
    status: LearningStatus = "needs_review"
    accepted_text: str | None = None
    active_tiers: list[LearningTier] = Field(default_factory=list)
    review_history: list[LearningReview] = Field(default_factory=list)


class LearningImportResult(StrictModel):
    import_record: LearningImportRecord
    proposals: list[LearningProposalView] = Field(default_factory=list)


class LearningScrubPreview(StrictModel):
    rendered_diff: str
    rendered_diff_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FirmLearningEvent(MatterProvenanced, VersionedModel):
    """One immutable, human-reviewed event in the private shared profile."""

    schema_version: str = "1.0"
    review: LearningReview
    contribution: ContextContribution

    @model_validator(mode="after")
    def validate_review_binding(self) -> FirmLearningEvent:
        expected = f"{self.review.target_tier}-learning-{self.review.proposal_id}"
        if self.review.action != "promote" or self.contribution.contribution_id != expected:
            raise ValueError("firm learning event does not bind its promotion review")
        if self.contribution.source_matter_id != self.review.source_matter_id:
            raise ValueError("firm learning event source matter does not match its review")
        if self.source_matter_id != self.review.source_matter_id:
            raise ValueError("firm learning event provenance does not match its review")
        return self
