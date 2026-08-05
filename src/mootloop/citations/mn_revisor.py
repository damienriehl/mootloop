"""MN Revisor verifier (plan Phase 4; the Revisor has no API, so this scrapes the
stable-URL pages the plan pins).

URL assumptions (documented so a break is obvious):

- **Statutes**: ``https://www.revisor.mn.gov/statutes/cite/<section>`` — ``<section>``
  is the number after the ``§`` (e.g. ``336.2-207``). This is the Revisor's canonical
  per-section permalink.
- **Court rules**: ``https://www.revisor.mn.gov/court_rules/<index>/id/<n>/`` — ``<n>``
  is the rule number *before the decimal* (e.g. ``33`` for ``Minn. R. Civ. P. 33.01``),
  since the Revisor indexes rules by whole-rule id, and ``<index>`` is the segment for
  the rule BODY the cite belongs to (``cp`` = Rules of Civil Procedure, ``gp`` =
  General Rules of Practice). Each body is a separate set of rules with its own
  independent numbering — see `_RULE_INDEXES`.

Verified iff HTTP 200 *and* the page text contains the cite's number (content check,
not just a 200 — the Revisor serves a soft page for unknown cites); ``content_sha256``
records the page for the staleness/audit trail. 404 -> ``invalid``; anything else ->
``pending`` (fail closed).
"""

from __future__ import annotations

import hashlib
import re

from mootloop.citations import http
from mootloop.models.citations import (
    AuthorityType,
    Citation,
    VerificationRecord,
    VerificationStatus,
)

MN_HOST = "www.revisor.mn.gov"

_STATUTE_SECTION_RE = re.compile(r"§+\s*([0-9][0-9A-Za-z.\-]*)")
_RULE_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")

# Rule-body token -> the Revisor's court-rule index segment. The Minnesota General
# Rules of Practice are a DIFFERENT body from the Rules of Civil Procedure, numbered
# independently: `Minn. Gen. R. Prac. 115.03` sent to the civil-procedure index 404s,
# and a 404 here means `invalid` — the tool telling an attorney that a correct cite is
# fabricated. Only a body with a pinned index is fetched; anything else is unsupported
# (see `revisor_index_for`) rather than guessed into some other body's numbering.
_RULE_INDEXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bGen\.\s*R\.\s*Prac\.", re.IGNORECASE), "gp"),
    (re.compile(r"\bR\.\s*Civ\.\s*P\.", re.IGNORECASE), "cp"),
)


def revisor_index_for(citation: Citation) -> str | None:
    """The Revisor court-rule index segment for ``citation``, or None if this rule body
    has no pinned index (the free stack cannot verify it — route it to research)."""
    if citation.authority_type != AuthorityType.COURT_RULE:
        return None
    for pattern, segment in _RULE_INDEXES:
        if pattern.search(citation.normalized):
            return segment
    return None


def can_verify(citation: Citation) -> bool:
    """True iff this citation is a shape the Revisor scraper can actually answer."""
    if citation.authority_type == AuthorityType.STATE_STATUTE:
        return _statute_section(citation.normalized) is not None
    if citation.authority_type == AuthorityType.COURT_RULE:
        return (
            revisor_index_for(citation) is not None
            and _rule_number(citation.normalized) is not None
        )
    return False


def _statute_section(normalized: str) -> str | None:
    match = _STATUTE_SECTION_RE.search(normalized)
    return match.group(1) if match else None


def _rule_number(normalized: str) -> tuple[str, str] | None:
    """Return ``(whole_rule, full_number)`` — e.g. ``("33", "33.01")`` — or None."""
    match = _RULE_NUMBER_RE.search(normalized)
    if not match:
        return None
    full = match.group(1)
    return full.split(".", 1)[0], full


def _pending(citation: Citation, now: str, note: str) -> VerificationRecord:
    return VerificationRecord(
        citation_id=citation.citation_id,
        status=VerificationStatus.PENDING,
        source="mn_revisor",
        verified_at=now,
        notes=note,
    )


def _build_request(citation: Citation) -> tuple[http.HttpRequest, str] | None:
    """The (request, needle) for a citation, or None if this cite is not MN Revisor
    territory. ``needle`` is the number the page must contain to count as verified."""
    if citation.authority_type == AuthorityType.STATE_STATUTE:
        section = _statute_section(citation.normalized)
        if section is None:
            return None
        return http.HttpRequest("GET", MN_HOST, f"/statutes/cite/{section}"), section
    if citation.authority_type == AuthorityType.COURT_RULE:
        index = revisor_index_for(citation)
        parsed = _rule_number(citation.normalized)
        if index is None or parsed is None:
            return None
        whole, full = parsed
        return http.HttpRequest("GET", MN_HOST, f"/court_rules/{index}/id/{whole}/"), full
    return None


def verify_mn(
    citation: Citation,
    *,
    now: str,
    transport: http.Transport | None = None,
) -> VerificationRecord:
    """Verify a MN statute or court-rule citation against the Revisor's stable URLs."""
    built = _build_request(citation)
    if built is None:
        # No pinned index for this shape: say so. Fetching it against a DIFFERENT rule
        # body's index would 404 and be recorded `invalid`, which is a false accusation
        # against a citation we simply cannot check.
        return _pending(citation, now, "no pinned MN Revisor index for this citation shape")
    request, needle = built
    source_url = f"https://{MN_HOST}{request.path}"
    try:
        response = http.fetch(request, transport=transport)
    except http.HttpError as exc:
        return _pending(citation, now, f"http error: {type(exc).__name__}")
    if response.status_code == 404:
        return VerificationRecord(
            citation_id=citation.citation_id,
            status=VerificationStatus.INVALID,
            source="mn_revisor",
            source_url=source_url,
            verified_at=now,
            notes="Revisor returned 404 for this cite",
        )
    if response.status_code == 200 and needle in response.text:
        return VerificationRecord(
            citation_id=citation.citation_id,
            status=VerificationStatus.VERIFIED,
            source="mn_revisor",
            source_url=source_url,
            verified_at=now,
            content_sha256=hashlib.sha256(response.text.encode("utf-8")).hexdigest(),
        )
    return _pending(citation, now, f"unexpected response (http {response.status_code}); retry")
