"""Verification router (plan Phase 4): curated tier first, then by authority type
(case -> CourtListener, MN statute/rule -> Revisor, everything else -> research
queue), consulting the append-only ledger cache first so re-runs stay off the network.

Also home of the standing **citator disclosure** (plan D9) that every citation-bearing
surface must show, and the deterministic **citation gate** the final/export gate reads
from the immutable ledger (a persona can never assert "verified"; plan H8).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mootloop.citations import courtlistener, http, mn_revisor
from mootloop.citations.ledger import (
    DEFAULT_MAX_CACHE_AGE_DAYS,
    ResearchQueue,
    VerificationLedger,
)
from mootloop.citations.ratelimit import TokenBucket
from mootloop.errors import CitationError
from mootloop.models.citations import (
    AuthorityType,
    Citation,
    ResearchRequest,
    VerificationRecord,
    VerificationStatus,
    make_citation_id,
)
from mootloop.models.gates import GateFail, GateFinding, GatePass, GatePending, GateResult
from mootloop.vault import atomic_copy, safe_vault_path

CITATOR_DISCLOSURE = (
    "Citation currency not checked against a citator (KeyCite/Shepard's) — "
    "attorney must confirm good-law status."
)

CURATED_DIR = ("law", "curated")
GATE_NAME = "citation"

# MN Revisor territory; anything not case/curated/MN routes to the human research
# queue (federal statute, regulation, other — the free stack cannot verify them).
_MN_REVISOR = frozenset({AuthorityType.STATE_STATUTE, AuthorityType.COURT_RULE})


def curated_slug(normalized: str) -> str:
    """A filesystem-safe slug for a curated-cite filename (``law/curated/<slug>.md``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or "cite"


def curated_path(vault_root: Path | str, normalized: str) -> Path:
    return safe_vault_path(vault_root, *CURATED_DIR, f"{curated_slug(normalized)}.md")


def _research_request_id(normalized: str) -> str:
    return f"research-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]}"


@dataclass
class CitationOutcome:
    citation: Citation
    status: VerificationStatus
    source_url: str | None = None
    notes: str = ""


@dataclass
class VerifySummary:
    outcomes: list[CitationOutcome] = field(default_factory=list)
    research_request_ids: list[str] = field(default_factory=list)
    disclosure: str = CITATOR_DISCLOSURE

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for outcome in self.outcomes:
            tally[outcome.status.value] = tally.get(outcome.status.value, 0) + 1
        return tally

    def blocking(self) -> list[CitationOutcome]:
        return [o for o in self.outcomes if o.status != VerificationStatus.VERIFIED]


def _curated_record(vault_root: Path | str, citation: Citation, now: str) -> VerificationRecord:
    path = curated_path(vault_root, citation.normalized)
    return VerificationRecord(
        citation_id=citation.citation_id,
        status=VerificationStatus.VERIFIED,
        source="curated",
        source_url=path.as_uri(),
        verified_at=now,
        content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        notes="tier-1 curated authority",
    )


def _needs_research_record(citation: Citation, now: str, request_id: str) -> VerificationRecord:
    return VerificationRecord(
        citation_id=citation.citation_id,
        status=VerificationStatus.NEEDS_RESEARCH,
        source="manual",
        verified_at=now,
        notes=f"routed to research queue as {request_id}",
    )


def verify_all(
    vault_root: Path | str,
    citations: list[Citation],
    now: str,
    *,
    max_cache_age_days: int = DEFAULT_MAX_CACHE_AGE_DAYS,
    limiter: TokenBucket | None = None,
    transport: http.Transport | None = None,
) -> VerifySummary:
    """Verify every citation, consulting the ledger cache first, appending new records
    and research requests, and returning a summary + the standing citator disclosure."""
    ledger = VerificationLedger(vault_root)
    queue = ResearchQueue(vault_root)
    now_dt = datetime.fromisoformat(now)
    cached = ledger.folded(now=now_dt, max_cache_age_days=max_cache_age_days)
    open_ids = {r.request_id for r in queue.open_requests()}

    # Dedupe the input by citation_id — the same authority verifies once per pass.
    unique: dict[str, Citation] = {}
    for citation in citations:
        unique.setdefault(citation.citation_id, citation)

    cases_to_lookup: list[Citation] = []
    new_records: list[VerificationRecord] = []

    for citation in unique.values():
        prior = cached.get(citation.citation_id)
        if prior is not None and prior.status != VerificationStatus.PENDING:
            continue  # cache hit (fresh) — no network, no new record
        if curated_path(vault_root, citation.normalized).is_file():
            new_records.append(_curated_record(vault_root, citation, now))
        elif citation.authority_type == AuthorityType.CASE:
            cases_to_lookup.append(citation)
        elif citation.authority_type in _MN_REVISOR:
            new_records.append(mn_revisor.verify_mn(citation, now=now, transport=transport))
        else:  # federal statute / regulation / other -> research queue
            request_id = _research_request_id(citation.normalized)
            new_records.append(_needs_research_record(citation, now, request_id))
            if request_id not in open_ids:
                queue.append(
                    ResearchRequest(
                        request_id=request_id,
                        citation_id=citation.citation_id,
                        normalized=citation.normalized,
                        reason=f"{citation.authority_type.value} not verifiable via the free stack",
                    )
                )
                open_ids.add(request_id)

    if cases_to_lookup:
        new_records.extend(
            courtlistener.verify_cases(
                cases_to_lookup, now=now, limiter=limiter, transport=transport
            )
        )

    for record in new_records:
        ledger.append(record)

    return _summarize(vault_root, list(unique.values()), now, max_cache_age_days)


def _summarize(
    vault_root: Path | str,
    citations: list[Citation],
    now: str,
    max_cache_age_days: int,
) -> VerifySummary:
    ledger = VerificationLedger(vault_root)
    queue = ResearchQueue(vault_root)
    folded = ledger.folded(now=datetime.fromisoformat(now), max_cache_age_days=max_cache_age_days)
    summary = VerifySummary()
    for citation in citations:
        record = folded.get(citation.citation_id)
        if record is None:
            summary.outcomes.append(CitationOutcome(citation, VerificationStatus.PENDING))
            continue
        summary.outcomes.append(
            CitationOutcome(citation, record.status, record.source_url, record.notes)
        )
        if record.status == VerificationStatus.NEEDS_RESEARCH:
            rid = _research_request_id(citation.normalized)
            if queue.get(rid) is not None and rid not in summary.research_request_ids:
                summary.research_request_ids.append(rid)
    return summary


def citation_gate(
    vault_root: Path | str,
    citations: list[Citation],
    *,
    now: str,
    max_cache_age_days: int = DEFAULT_MAX_CACHE_AGE_DAYS,
) -> GateResult:
    """The export-readiness citation gate: reads the immutable ledger (never re-runs
    verification) and blocks unless every citation is ``verified``/curated (plan H8).

    ``invalid``/``unconfirmed``/``ambiguous`` are hard fails; ``pending``/
    ``needs_research`` are ``GatePending`` (blocked pending human/network work)."""
    ledger = VerificationLedger(vault_root)
    folded = ledger.folded(now=datetime.fromisoformat(now), max_cache_age_days=max_cache_age_days)
    fail_findings: list[GateFinding] = []
    pending_findings: list[GateFinding] = []
    for citation in citations:
        record = folded.get(citation.citation_id)
        status = record.status if record else VerificationStatus.PENDING
        if status == VerificationStatus.VERIFIED:
            continue
        finding = GateFinding(
            code=f"citation_{status.value}",
            message=f"{citation.raw_text!r} is {status.value}",
            locator=citation.citation_id,
        )
        if status in (
            VerificationStatus.INVALID,
            VerificationStatus.UNCONFIRMED,
            VerificationStatus.AMBIGUOUS,
        ):
            fail_findings.append(finding)
        else:
            pending_findings.append(finding)
    if fail_findings:
        return GateFail(gate=GATE_NAME, findings=fail_findings + pending_findings)
    if pending_findings:
        return GatePending(gate=GATE_NAME, findings=pending_findings)
    return GatePass(gate=GATE_NAME)


def fulfill_research_request(
    vault_root: Path | str,
    request_id: str,
    *,
    file: Path | str,
    now: str,
    url: str | None = None,
) -> VerificationRecord:
    """Fulfill a research request: copy the authority into ``law/curated/``, mark the
    request fulfilled, and append a curated ``verified`` record (plan Phase 4)."""
    queue = ResearchQueue(vault_root)
    request = queue.get(request_id)
    if request is None:
        raise CitationError(f"unknown research request: {request_id}")
    src = Path(file)
    if not src.is_file():
        raise CitationError(f"authority file not found: {src}")
    dst = curated_path(vault_root, request.normalized)
    atomic_copy(src, dst)
    queue.append(request.model_copy(update={"status": "fulfilled"}))
    record = VerificationRecord(
        citation_id=make_citation_id(request.normalized),
        status=VerificationStatus.VERIFIED,
        source="curated",
        source_url=url or dst.as_uri(),
        verified_at=now,
        content_sha256=hashlib.sha256(dst.read_bytes()).hexdigest(),
        notes=f"fulfilled from {request_id}",
    )
    VerificationLedger(vault_root).append(record)
    return record
