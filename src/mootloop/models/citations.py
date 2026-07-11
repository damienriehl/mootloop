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

from mootloop.models.common import CitationId, StrictModel, VersionedModel

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


def make_citation_id(normalized: str) -> CitationId:
    """``cit-<sha256[:12]>`` of the normalized cite string (content-addressed)."""
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return CitationId(f"cit-{digest}")


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
