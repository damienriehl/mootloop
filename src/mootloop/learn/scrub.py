"""Fail-closed cross-matter and public-learning trust conversion."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import ValidationError

from mootloop.errors import LearningImportError, OutboundPrivacyError
from mootloop.facts import FactStore
from mootloop.learn.diff import critic_markup, sha256_text
from mootloop.models.common import PublicText
from mootloop.models.corpus import Manifest
from mootloop.models.learnings import LearningProposalView, LearningScrubPreview
from mootloop.privacy import scrub_outbound
from mootloop.vault import load_matter, safe_vault_path

_DATE_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2}(?:,\s*|\s+)\d{4}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(r"(?:\$\s*\d|\b\d+(?:\.\d+)?\s*(?:dollars?|usd)\b)", re.IGNORECASE)
_INJECTION_RE = re.compile(
    r"\b(?:ignore|override|disregard)\b.{0,40}\b(?:instruction|prompt|system)\b|"
    r"\b(?:reveal|print|exfiltrate)\b.{0,40}\b(?:prompt|secret|tool|credential)\b",
    re.IGNORECASE | re.DOTALL,
)
_WORD_RE = re.compile(r"[a-z0-9]+")
_SHARED_STOP = frozenset(
    {
        "about", "after", "also", "answer", "answers", "before", "client",
        "direct", "discovery", "document", "from", "interrogatory", "language",
        "matter", "prefer", "response", "responses", "should", "that", "their",
        "this", "timing", "when", "with",
    }
)


def sharing_scrub(vault_root: Path | str, text: str) -> PublicText:
    normalized = text.strip()
    matter = load_matter(vault_root)
    blocked: list[str] = []
    lowered = normalized.casefold()
    for value in [matter.matter_id, *(party.name for party in matter.parties)]:
        if value and value.casefold() in lowered:
            blocked.append("matter identity")
    if _INJECTION_RE.search(normalized):
        blocked.append("instruction-like text")
    if _DATE_RE.search(normalized) or _AMOUNT_RE.search(normalized):
        blocked.append("case-specific date or amount")
    manifest_path = safe_vault_path(vault_root, "corpus", "manifest.json")
    if manifest_path.is_file():
        try:
            manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError) as exc:
            raise LearningImportError(
                "sharing scrub could not validate the corpus manifest"
            ) from exc
        for document in manifest.docs:
            if document.original_name.casefold() in lowered:
                blocked.append("matter filename")
    context_words = set(_WORD_RE.findall(lowered)) - _SHARED_STOP
    for fact in FactStore(vault_root).get_current():
        fact_words = set(_WORD_RE.findall(fact.statement.casefold())) - _SHARED_STOP
        overlap = context_words & fact_words
        if fact.statement.casefold() in lowered or (
            len(overlap) >= 3 and len(overlap) * 2 >= len(fact_words)
        ):
            blocked.append("matter fact fingerprint")
            break
    if blocked:
        raise LearningImportError(
            "sharing scrub blocked the proposed learning: " + ", ".join(sorted(set(blocked)))
        )
    try:
        return scrub_outbound(text)
    except (OutboundPrivacyError, OSError, UnicodeError) as exc:
        raise LearningImportError(f"sharing scrub blocked outbound data: {exc}") from exc


def render_scrub_preview(
    proposal: LearningProposalView, scrubbed_text: PublicText
) -> LearningScrubPreview:
    baseline = proposal.accepted_text or proposal.proposed_text
    rendered, _ = critic_markup(baseline, scrubbed_text)
    return LearningScrubPreview(
        rendered_diff=rendered,
        rendered_diff_sha256=sha256_text(rendered),
    )
