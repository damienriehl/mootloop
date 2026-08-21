"""Citation vocabulary (plan D8/D9): an extracted `Citation`, a `VerificationRecord`
folded from the append-only `law/verifications.jsonl` ledger, and the `ResearchRequest`
that a citation the free stack cannot verify becomes.

Identity is content-derived: ``citation_id = "cit-<sha256[:12]>"`` of the normalized
cite string, so the same authority always folds to the same ledger entry across runs
(the persistent cache that keeps re-runs off the network, plan D5).

The ledger fold is *staleness-aware* (plan D9, malpractice-adjacent): a ``verified``
record older than ``max_cache_age`` days folds to ``pending`` with a ``stale`` reason,
forcing re-verification. The free stack cannot detect negative treatment, so every
surface that shows citations carries a standing citator disclosure (see
``mootloop.citations.verify.CITATOR_DISCLOSURE``).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from mootloop.models.common import CitationId, PropositionId, RunId, StrictModel, VersionedModel

SCHEMA_VERSION = "1.0"

STALE_REASON = "stale"


class AuthorityType(StrEnum):
    """The kind of legal authority a citation points to (drives the verify router)."""

    CASE = "case"
    STATE_STATUTE = "state_statute"
    FEDERAL_STATUTE = "federal_statute"
    REGULATION = "regulation"
    COURT_RULE = "court_rule"
    OTHER = "other"


class VerificationStatus(StrEnum):
    """A citation's verification state. Terminal-good states are ``verified`` (via a
    source) and (implicitly) curated-tier; everything else blocks or re-queues."""

    VERIFIED = "verified"
    UNCONFIRMED = "unconfirmed"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"
    PENDING = "pending"
    NEEDS_RESEARCH = "needs_research"


class PropositionVerificationStatus(StrEnum):
    """Whether exact authority text supports the proposition attributed to it."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"
    PENDING = "pending"
    NEEDS_RESEARCH = "needs_research"


# Where a VerificationRecord came from. ``manual`` covers research-queue routing.
VerificationSource = Literal["courtlistener", "mn_revisor", "curated", "manual"]


class Citation(StrictModel):
    """One extracted citation. ``normalized`` is the canonical cite string that seeds
    ``citation_id`` and keys the ledger; ``raw_text`` is the human-facing form."""

    citation_id: CitationId
    raw_text: str
    normalized: str
    authority_type: AuthorityType
    source_turn_id: str | None = None


class CitationProposition(StrictModel):
    """One citation occurrence and the exact original-text paragraph asserting it."""

    proposition_id: PropositionId
    citation_id: CitationId
    normalized_citation: str
    proposition_text: str = Field(min_length=1)
    proposition_start: int = Field(ge=0)
    proposition_end: int = Field(ge=0)
    citation_start: int = Field(ge=0)
    citation_end: int = Field(ge=0)
    source_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_turn_id: str | None = None

    @model_validator(mode="after")
    def validate_offsets(self) -> CitationProposition:
        if self.proposition_start >= self.proposition_end:
            raise ValueError("proposition offsets must describe a non-empty span")
        if self.citation_start >= self.citation_end:
            raise ValueError("citation offsets must describe a non-empty span")
        if not (
            self.proposition_start <= self.citation_start
            and self.citation_end <= self.proposition_end
        ):
            raise ValueError("citation offsets must lie inside the proposition span")
        return self


class AuthorityPassage(StrictModel):
    """One bounded, exact excerpt from a content-addressed public authority."""

    passage_id: str = Field(pattern=r"^passage-[0-9a-f]{16}$")
    text: str = Field(min_length=1, max_length=4096)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_url: str

    @model_validator(mode="after")
    def validate_span(self) -> AuthorityPassage:
        if self.start >= self.end:
            raise ValueError("authority passage offsets must describe a non-empty span")
        return self


class OpinionAuthorityStoreRecord(VersionedModel):
    """Exact normalized public opinion text captured from fixed CourtListener IDs."""

    schema_version: str = SCHEMA_VERSION
    citation_id: CitationId
    cluster_id: int = Field(gt=0)
    opinion_ids: list[int] = Field(min_length=1, max_length=8)
    source_url: str
    fetched_at: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str = Field(min_length=1, max_length=2_000_000)


def make_citation_id(normalized: str) -> CitationId:
    """``cit-<sha256[:12]>`` of the normalized cite string (content-addressed)."""
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return CitationId(f"cit-{digest}")


def make_proposition_id(citation_id: CitationId, proposition_text: str) -> PropositionId:
    """Content address an authority/proposition pair independent of run or whitespace."""
    normalized = " ".join(proposition_text.split())
    identity = f"{citation_id}\n{normalized}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return PropositionId(f"prop-{digest}")


class VerificationRecord(VersionedModel):
    """One append-only ledger entry: the outcome of verifying a citation once."""

    schema_version: str = SCHEMA_VERSION
    citation_id: CitationId
    status: VerificationStatus
    source: VerificationSource
    source_url: str | None = None
    verified_at: str
    content_sha256: str | None = None
    notes: str = ""


class PropositionVerificationRecord(VersionedModel):
    """One append-only cite-checker result bound to exact public authority bytes."""

    schema_version: str = SCHEMA_VERSION
    run_id: RunId
    proposition_id: PropositionId
    citation_id: CitationId
    source_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: PropositionVerificationStatus
    source: Literal["cite_checker", "manual"]
    checked_at: str
    authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority_source_url: str
    evidence_passage_ids: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def validate_supported_evidence(self) -> PropositionVerificationRecord:
        if (
            self.status == PropositionVerificationStatus.SUPPORTED
            and not self.evidence_passage_ids
        ):
            raise ValueError("supported proposition verification requires evidence passages")
        return self


class ResearchRequest(VersionedModel):
    """A citation the free stack cannot verify, queued for a human to fulfill from a
    paid citator (Westlaw/Lexis) into ``law/curated/`` (plan Phase 4)."""

    schema_version: str = SCHEMA_VERSION
    request_id: str
    citation_id: CitationId
    normalized: str
    reason: str
    status: Literal["open", "fulfilled"] = "open"


# --- pure ledger fold -------------------------------------------------------


def _is_stale(record: VerificationRecord, now: datetime, max_cache_age_days: int) -> bool:
    """True iff a ``verified`` record's ``verified_at`` is older than the cache age."""
    try:
        verified_at = datetime.fromisoformat(record.verified_at)
    except ValueError:
        return True  # fail closed: an unparseable timestamp is treated as expired
    return now - verified_at > timedelta(days=max_cache_age_days)


def fold_ledger(
    records: list[VerificationRecord],
    *,
    now: datetime,
    max_cache_age_days: int,
) -> dict[str, VerificationRecord]:
    """Replay the ledger into ``citation_id -> latest record`` (last write wins),
    then apply staleness: an expired ``verified`` entry folds to ``pending`` with a
    ``stale`` reason so the next verify pass re-checks it (plan D9). Pure and total.
    """
    latest: dict[str, VerificationRecord] = {}
    for record in records:
        latest[record.citation_id] = record
    folded: dict[str, VerificationRecord] = {}
    for cid, record in latest.items():
        if record.status == VerificationStatus.VERIFIED and _is_stale(
            record, now, max_cache_age_days
        ):
            folded[cid] = record.model_copy(
                update={
                    "status": VerificationStatus.PENDING,
                    "notes": f"{STALE_REASON}: verified_at exceeds max_cache_age; re-verify",
                }
            )
        else:
            folded[cid] = record
    return folded
