"""Durable human review and tier routing for edit-derived learning proposals."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from mootloop.context_sources import ContextContributionStore
from mootloop.errors import LearningImportError, VaultBoundaryError
from mootloop.learn.diff import sha256_text
from mootloop.learn.merge import (
    FIRM_PROFILE_ROOT_ENV,
    FirmLearningStore,
    configured_firm_profile_root,
    shared_contribution,
)
from mootloop.learn.scrub import render_scrub_preview, sharing_scrub
from mootloop.models.common import (
    LearningProposalId,
    MatterId,
    PublicText,
    canonical_json_sha256,
)
from mootloop.models.context import ContextContribution
from mootloop.models.learnings import (
    LearningImportBundle,
    LearningImportRecord,
    LearningProposalView,
    LearningReview,
    LearningReviewAction,
    LearningReviewChannel,
    LearningScrubPreview,
    LearningStatus,
    LearningTier,
)
from mootloop.persistence import append_fsync_line, complete_jsonl_lines
from mootloop.vault import (
    RunLock,
    atomic_write_once_bytes,
    atomic_write_once_text,
    safe_vault_path,
    validate_id,
)


class LearningStore:
    """Write-once import bundles plus append-only human learning reviews."""

    def __init__(self, vault_root: Path | str) -> None:
        self.vault_root = vault_root
        self.imports_root = safe_vault_path(vault_root, "learnings", "imports")
        self.reviews_path = safe_vault_path(vault_root, "learnings", "reviews.jsonl")

    def list_bundles(self) -> list[LearningImportBundle]:
        if not self.imports_root.is_dir():
            return []
        bundles: list[LearningImportBundle] = []
        for discovered in sorted(self.imports_root.glob("*.json")):
            path = safe_vault_path(self.vault_root, "learnings", "imports", discovered.name)
            try:
                bundles.append(LearningImportBundle.model_validate_json(path.read_text()))
            except (OSError, UnicodeError, ValidationError) as exc:
                raise LearningImportError(f"learning import {path.name!r} is invalid") from exc
        return bundles

    def list_imports(self) -> list[LearningImportRecord]:
        return [bundle.import_record for bundle in self.list_bundles()]

    def load_bundle(self, import_id: str) -> LearningImportBundle | None:
        path = safe_vault_path(self.vault_root, "learnings", "imports", f"{import_id}.json")
        if not path.is_file():
            return None
        try:
            return LearningImportBundle.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError) as exc:
            raise LearningImportError(f"learning import {import_id!r} is invalid") from exc

    def publish(self, bundle: LearningImportBundle) -> LearningImportBundle:
        import_id = bundle.import_record.import_id
        path = safe_vault_path(self.vault_root, "learnings", "imports", f"{import_id}.json")
        existing = self.load_bundle(import_id)
        if existing is not None:
            if existing != bundle:
                raise LearningImportError("learning import identity conflicts with stored content")
            return existing
        atomic_write_once_text(path, bundle.model_dump_json(indent=2) + "\n")
        return bundle

    def publish_source(self, import_id: str, source: bytes, expected_sha256: str) -> None:
        path = safe_vault_path(self.vault_root, "learnings", "imports", f"{import_id}.docx")
        if path.is_file():
            try:
                existing = path.read_bytes()
            except OSError as exc:
                raise LearningImportError("stored edited DOCX could not be read") from exc
            if hashlib.sha256(existing).hexdigest() != expected_sha256:
                raise LearningImportError("stored edited DOCX conflicts with import identity")
            return
        atomic_write_once_bytes(path, source)

    def review_events(self) -> list[LearningReview]:
        if not self.reviews_path.is_file():
            return []
        try:
            return [
                LearningReview.model_validate_json(line)
                for line in complete_jsonl_lines(self.reviews_path)
                if line.strip()
            ]
        except (OSError, UnicodeError, ValidationError) as exc:
            raise LearningImportError("learning review log is invalid") from exc

    def append_review(self, review: LearningReview) -> None:
        prior = {item.review_id: item for item in self.review_events()}.get(review.review_id)
        if prior is not None:
            if prior != review:
                raise LearningImportError("learning review id conflicts with stored action")
            return
        append_fsync_line(self.reviews_path, review.model_dump_json())

    def list_all(self) -> list[LearningProposalView]:
        history: dict[str, list[LearningReview]] = {}
        for review in self.review_events():
            history.setdefault(str(review.proposal_id), []).append(review)
        views: list[LearningProposalView] = []
        for bundle in self.list_bundles():
            for proposal in bundle.proposals:
                events = history.get(str(proposal.proposal_id), [])
                status: LearningStatus = "needs_review"
                accepted_text: str | None = None
                tiers: list[LearningTier] = []
                for event in events:
                    if event.action == "accept":
                        status = "accepted"
                        accepted_text = event.reviewed_text
                        tiers = ["matter"]
                    elif event.action == "reject":
                        status = "rejected"
                    elif event.action == "promote" and event.target_tier not in tiers:
                        assert event.target_tier is not None
                        tiers.append(event.target_tier)
                views.append(
                    LearningProposalView(
                        **proposal.model_dump(),
                        status=status,
                        accepted_text=accepted_text,
                        active_tiers=tiers,
                        review_history=events,
                    )
                )
        views.sort(key=lambda item: (item.created_at, str(item.proposal_id)))
        return views

    def get(self, proposal_id: str) -> LearningProposalView | None:
        return next((item for item in self.list_all() if item.proposal_id == proposal_id), None)


def preview_learning_scrub(
    vault_root: Path | str, proposal_id: str, reviewed_text: str
) -> LearningScrubPreview:
    proposal = LearningStore(vault_root).get(proposal_id)
    if proposal is None:
        raise LearningImportError(f"unknown learning proposal {proposal_id!r}")
    return render_scrub_preview(proposal, sharing_scrub(vault_root, reviewed_text))


def _validate_profile_separation(vault_root: Path | str, root: Path) -> None:
    try:
        resolved_root = root.resolve(strict=False)
        resolved_vault = Path(vault_root).resolve()
        resolved_root.relative_to(resolved_vault)
    except ValueError:
        try:
            resolved_vault.relative_to(resolved_root)
        except ValueError:
            return
        raise LearningImportError("firm profile root must not contain the matter vault") from None
    raise LearningImportError("firm profile root must remain outside the matter vault")


def review_learning_proposal(
    vault_root: Path | str,
    proposal_id: str,
    *,
    action: LearningReviewAction,
    actor: str,
    channel: LearningReviewChannel,
    recorded_at: str,
    reviewed_text: str = "",
    target_tier: LearningTier | None = None,
    reason: str = "",
    confirm_scrub_diff: bool = False,
    scrub_diff_sha256: str | None = None,
    excluded_matter_ids: tuple[str, ...] = (),
    firm_root: Path | str | None = None,
) -> LearningProposalView:
    """Record a hard-human review and publish only the explicitly approved tier."""
    if not actor.strip():
        raise LearningImportError("actor must identify the human reviewer")
    store = LearningStore(vault_root)
    current = store.get(proposal_id)
    if current is None:
        raise LearningImportError(f"unknown learning proposal {proposal_id!r}")
    if action in ("accept", "reject") and current.status != "needs_review":
        raise LearningImportError("learning proposal already has a final review")
    if action == "promote" and current.status != "accepted":
        raise LearningImportError("learning proposal must be accepted before promotion")
    scrubbed_text: PublicText | None = None
    if action == "promote":
        scrubbed_text = sharing_scrub(vault_root, reviewed_text)
        preview = render_scrub_preview(current, scrubbed_text)
        if scrub_diff_sha256 != preview.rendered_diff_sha256:
            raise LearningImportError(
                "promotion scrub-diff SHA-256 does not match the exact rendered review diff"
            )
        if target_tier == "area" and not confirm_scrub_diff:
            raise LearningImportError("area promotion requires confirmed rendered scrub diff")
    payload = {
        "proposal_id": proposal_id,
        "action": action,
        "actor": actor,
        "channel": channel,
        "recorded_at": recorded_at,
        "reviewed_text": reviewed_text,
        "target_tier": target_tier,
        "reason": reason,
        "confirm_scrub_diff": confirm_scrub_diff,
        "scrub_diff_sha256": scrub_diff_sha256,
        "excluded_matter_ids": excluded_matter_ids,
    }
    try:
        validated_exclusions = tuple(
            MatterId(validate_id(matter_id, kind="excluded matter_id"))
            for matter_id in excluded_matter_ids
        )
    except VaultBoundaryError as exc:
        raise LearningImportError(str(exc)) from exc
    try:
        review = LearningReview(
            review_id=f"learning-review-{canonical_json_sha256(payload)[:16]}",
            source_matter_id=current.source_matter_id,
            proposal_id=LearningProposalId(proposal_id),
            action=action,
            actor=actor,
            channel=channel,
            recorded_at=recorded_at,
            reviewed_text=reviewed_text,
            target_tier=target_tier,
            reason=reason,
            confirm_scrub_diff=confirm_scrub_diff,
            scrub_diff_sha256=scrub_diff_sha256,
            excluded_matter_ids=validated_exclusions,
        )
    except ValidationError as exc:
        raise LearningImportError(str(exc)) from exc
    run_id = str(current.run_id)
    with RunLock(vault_root, run_id):
        store.append_review(review)
        if action == "accept":
            contribution = ContextContribution(
                contribution_id=f"learning:{proposal_id}",
                kind="learning",
                text=review.reviewed_text,
                sha256=sha256_text(review.reviewed_text),
                provenance_locator=f"learnings/reviews.jsonl#{review.review_id}",
                source_matter_id=current.source_matter_id,
                task_scope=(current.task,),
                permission="matter_confidential",
                approval_state="accepted",
            )
            ContextContributionStore(vault_root).put(contribution)
        elif action == "promote":
            assert scrubbed_text is not None
            root = Path(firm_root) if firm_root is not None else configured_firm_profile_root()
            if root is None:
                raise LearningImportError(
                    f"{FIRM_PROFILE_ROOT_ENV} is required for shared learning promotion"
                )
            _validate_profile_separation(vault_root, root)
            assert target_tier is not None
            FirmLearningStore(root).put(
                shared_contribution(
                    current,
                    review,
                    tier=target_tier,
                    scrubbed_text=scrubbed_text,
                ),
                review,
                public=target_tier == "area",
            )
    updated = store.get(proposal_id)
    assert updated is not None
    return updated
