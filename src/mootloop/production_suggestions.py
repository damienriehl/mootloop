"""Deterministic RFP document classification proposals and human review lifecycle."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from mootloop.context import load_run_context, load_run_corpus
from mootloop.errors import ProductionSuggestionError
from mootloop.models.common import (
    MatterId,
    ProductionSuggestionId,
    RunId,
    canonical_json_sha256,
)
from mootloop.models.production import (
    ProductionClassification,
    ProductionDisposition,
    ProductionSuggestion,
    ProductionSuggestionBundle,
    ProductionSuggestionExclusion,
    ProductionSuggestionResult,
    ProductionSuggestionReview,
    ProductionSuggestionView,
    SuggestionReviewAction,
    SuggestionReviewChannel,
)
from mootloop.persistence import append_fsync_line, complete_jsonl_lines
from mootloop.vault import RunLock, atomic_write_once_text, safe_vault_path

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "and", "any", "all", "for", "from", "that", "the", "this", "with",
        "produce", "documents", "document", "including", "relating", "related",
        "your", "you", "its", "their", "each", "every", "request",
    }
)


class ProductionSuggestionLeaseLostError(ProductionSuggestionError):
    """The durable job no longer owns its queue lease."""


def _terms(text: str) -> set[str]:
    return {token for token in _TOKEN.findall(text.casefold()) if len(token) >= 3} - _STOP


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _suggestion_id(
    run_id: str, request_id: str, doc_id: str, doc_sha: str
) -> ProductionSuggestionId:
    digest = _sha(f"{run_id}\n{request_id}\n{doc_id}\n{doc_sha}")[:16]
    return ProductionSuggestionId(f"prod-suggestion-{digest}")


def production_suggestions_eligible(vault_root: Path | str, run_id: str) -> bool:
    """Return whether the immutable run snapshot contains at least one RFP."""
    context = load_run_context(vault_root, run_id)
    return any(str(unit.request_id).startswith("RFP-") for unit in context.units)


def require_production_suggestions_eligible(vault_root: Path | str, run_id: str) -> None:
    """Fail before queueing when a run has no RFP work to classify."""
    if not production_suggestions_eligible(vault_root, run_id):
        raise ProductionSuggestionError("run has no RFP requests")


class ProductionSuggestionStore:
    """Write-once generated bundle plus append-only human review events."""

    def __init__(self, vault_root: Path | str, run_id: str) -> None:
        self.vault_root = vault_root
        self.run_id = run_id
        self.bundle_path = safe_vault_path(
            vault_root, "runs", run_id, "production", "suggestions.json"
        )
        self.reviews_path = safe_vault_path(
            vault_root, "runs", run_id, "production", "reviews.jsonl"
        )

    def load_bundle(self) -> ProductionSuggestionBundle | None:
        if not self.bundle_path.is_file():
            return None
        try:
            return ProductionSuggestionBundle.model_validate_json(
                self.bundle_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError) as exc:
            raise ProductionSuggestionError("production suggestion bundle is invalid") from exc

    def publish(self, bundle: ProductionSuggestionBundle) -> ProductionSuggestionBundle:
        body = bundle.model_dump_json(indent=2) + "\n"
        existing = self.load_bundle()
        if existing is not None:
            return existing
        atomic_write_once_text(self.bundle_path, body)
        return bundle

    def review_events(self) -> list[ProductionSuggestionReview]:
        if not self.reviews_path.is_file():
            return []
        records: list[ProductionSuggestionReview] = []
        for line in complete_jsonl_lines(self.reviews_path):
            if line.strip():
                records.append(ProductionSuggestionReview.model_validate_json(line))
        return records

    def append_review(self, review: ProductionSuggestionReview) -> None:
        existing = {item.review_id: item for item in self.review_events()}
        prior = existing.get(review.review_id)
        if prior is not None:
            if prior != review:
                raise ProductionSuggestionError("review id conflicts with existing action")
            return
        append_fsync_line(self.reviews_path, review.model_dump_json())

    def list_all(self) -> list[ProductionSuggestionView]:
        bundle = self.load_bundle()
        if bundle is None:
            return []
        history: dict[str, list[ProductionSuggestionReview]] = {}
        for review in self.review_events():
            history.setdefault(str(review.suggestion_id), []).append(review)
        views: list[ProductionSuggestionView] = []
        for suggestion in bundle.suggestions:
            events = history.get(str(suggestion.suggestion_id), [])
            review_status: Literal["needs_review", "accepted", "rejected"] = "needs_review"
            disposition: ProductionDisposition | None = None
            for event in events:
                if event.action == "accept":
                    review_status = "accepted"
                elif event.action == "reject":
                    review_status = "rejected"
                elif event.action == "production_review":
                    disposition = event.production_disposition
            views.append(
                ProductionSuggestionView(
                    **suggestion.model_dump(),
                    review_status=review_status,
                    production_disposition=disposition,
                    review_history=events,
                )
            )
        return views

    def get(self, suggestion_id: str) -> ProductionSuggestionView | None:
        return next(
            (item for item in self.list_all() if item.suggestion_id == suggestion_id), None
        )


def build_production_suggestions(
    vault_root: Path | str,
    run_id: str,
    created_at: str,
    *,
    heartbeat: Callable[[], bool] | None = None,
) -> ProductionSuggestionResult:
    """Classify exact launch-snapshot documents for each RFP, never authorizing production."""
    context = load_run_context(vault_root, run_id)
    corpus = load_run_corpus(vault_root, context)
    store = ProductionSuggestionStore(vault_root, run_id)
    existing = store.load_bundle()
    if existing is not None:
        return ProductionSuggestionResult(
            suggestions=store.list_all(), exclusions=existing.exclusions
        )
    rfp_units = [unit for unit in context.units if str(unit.request_id).startswith("RFP-")]
    if not rfp_units:
        raise ProductionSuggestionError("run has no RFP requests")
    text_by_doc = {str(doc.doc_id): doc for doc in corpus.documents}
    suggestions: list[ProductionSuggestion] = []
    exclusions: list[ProductionSuggestionExclusion] = []
    matter_id = MatterId(str(context.manifest.matter_id))
    for request in rfp_units:
        request_terms = _terms(request.text)
        request_sha = _sha(request.text)
        for metadata in context.manifest.corpus_manifest.docs:
            if heartbeat is not None and not heartbeat():
                raise ProductionSuggestionLeaseLostError(
                    "production suggestion queue lease was lost"
                )
            if metadata.privileged is True:
                exclusions.append(
                    ProductionSuggestionExclusion(
                        request_id=request.request_id,
                        doc_id=metadata.doc_id,
                        original_name=metadata.original_name,
                        reason="privileged",
                    )
                )
                continue
            if not metadata.run_visible:
                exclusions.append(
                    ProductionSuggestionExclusion(
                        request_id=request.request_id,
                        doc_id=metadata.doc_id,
                        original_name=metadata.original_name,
                        reason="untriaged",
                    )
                )
                continue
            snapshot = text_by_doc.get(str(metadata.doc_id))
            if snapshot is None:
                exclusions.append(
                    ProductionSuggestionExclusion(
                        request_id=request.request_id,
                        doc_id=metadata.doc_id,
                        original_name=metadata.original_name,
                        reason="unavailable",
                    )
                )
                continue
            matched = sorted(request_terms & _terms(snapshot.text))
            score = len(matched) / max(1, len(request_terms))
            classification: ProductionClassification = (
                "responsive" if matched else "non_responsive"
            )
            reason = (
                "Matched request terms: " + ", ".join(matched[:12])
                if matched
                else "No material request terms matched the normalized document text."
            )
            suggestions.append(
                ProductionSuggestion(
                    suggestion_id=_suggestion_id(
                        run_id, str(request.request_id), str(metadata.doc_id), snapshot.sha256
                    ),
                    source_matter_id=matter_id,
                    run_id=RunId(run_id),
                    request_id=request.request_id,
                    doc_id=metadata.doc_id,
                    original_name=metadata.original_name,
                    source_locator=snapshot.locator,
                    request_sha256=request_sha,
                    document_sha256=snapshot.sha256,
                    classification=classification,
                    score=score,
                    reason=reason,
                    created_at=created_at,
                )
            )
    suggestions.sort(
        key=lambda item: (
            str(item.request_id),
            -item.score,
            item.original_name,
            str(item.doc_id),
        )
    )
    exclusions.sort(key=lambda item: (str(item.request_id), item.original_name, str(item.doc_id)))
    bundle = ProductionSuggestionBundle(
        source_matter_id=matter_id,
        run_id=RunId(run_id),
        suggestions=suggestions,
        exclusions=exclusions,
    )
    with RunLock(vault_root, run_id):
        published = store.publish(bundle)
    return ProductionSuggestionResult(
        suggestions=store.list_all(), exclusions=published.exclusions
    )


def review_production_suggestion(
    vault_root: Path | str,
    run_id: str,
    suggestion_id: str,
    *,
    action: SuggestionReviewAction,
    actor: str,
    channel: SuggestionReviewChannel,
    recorded_at: str,
    production_disposition: ProductionDisposition | None = None,
    reason: str = "",
) -> ProductionSuggestionView:
    """Append a human classification or separate production-review action."""
    if not actor.strip():
        raise ProductionSuggestionError("actor must identify the human reviewer")
    store = ProductionSuggestionStore(vault_root, run_id)
    with RunLock(vault_root, run_id):
        current = store.get(suggestion_id)
        if current is None:
            raise ProductionSuggestionError(f"unknown production suggestion {suggestion_id!r}")
        payload = {
            "run_id": run_id,
            "suggestion_id": suggestion_id,
            "action": action,
            "actor": actor,
            "channel": channel,
            "recorded_at": recorded_at,
            "reason": reason,
            "production_disposition": production_disposition,
        }
        try:
            review = ProductionSuggestionReview(
                review_id=f"prod-review-{canonical_json_sha256(payload)[:16]}",
                source_matter_id=current.source_matter_id,
                run_id=RunId(run_id),
                suggestion_id=ProductionSuggestionId(suggestion_id),
                action=action,
                actor=actor,
                channel=channel,
                recorded_at=recorded_at,
                reason=reason,
                production_disposition=production_disposition,
            )
        except ValidationError as exc:
            raise ProductionSuggestionError(str(exc)) from exc
        store.append_review(review)
        updated = store.get(suggestion_id)
        assert updated is not None
        return updated
