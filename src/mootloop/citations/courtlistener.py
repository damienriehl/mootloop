"""CourtListener v4 ``citation-lookup`` client (plan Phase 4).

POSTs cite strings to ``/api/rest/v4/citation-lookup/`` (token auth, 250-cite chunks,
one process-wide 60/min bucket) and maps the per-citation status the API returns to a
`VerificationRecord`:

- 200 -> ``verified`` (record the cluster's absolute URL)
- 404 -> ``unconfirmed`` (well-formed but not in the database)
- 400 -> ``invalid`` (unparseable citation)
- 300 -> ``ambiguous`` (multiple candidate clusters)
- 429 / HTTP error / timeout -> ``pending`` (retry) — **fail closed**, never verified.
"""

from __future__ import annotations

from typing import Any

from mootloop.citations import http
from mootloop.citations.ratelimit import TokenBucket, default_limiter
from mootloop.models.citations import (
    Citation,
    VerificationRecord,
    VerificationStatus,
)

CL_HOST = "www.courtlistener.com"
CITATION_LOOKUP_PATH = "/api/rest/v4/citation-lookup/"
TOKEN_ENV = "COURTLISTENER_TOKEN"
CHUNK_SIZE = 250

# CourtListener per-citation status code -> our verification status.
_STATUS_MAP: dict[int, VerificationStatus] = {
    200: VerificationStatus.VERIFIED,
    404: VerificationStatus.UNCONFIRMED,
    400: VerificationStatus.INVALID,
    300: VerificationStatus.AMBIGUOUS,
}


def _chunks(items: list[Citation], size: int) -> list[list[Citation]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _cluster_url(result: dict[str, Any]) -> str | None:
    clusters = result.get("clusters")
    if isinstance(clusters, list) and clusters:
        first = clusters[0]
        if isinstance(first, dict):
            url = first.get("absolute_url")
            if isinstance(url, str) and url:
                return url if url.startswith("http") else f"https://{CL_HOST}{url}"
    return None


def _result_matches(result: dict[str, Any], citation: Citation) -> bool:
    normalized = result.get("normalized_citations")
    if isinstance(normalized, list) and citation.normalized in normalized:
        return True
    cite = result.get("citation")
    return isinstance(cite, str) and citation.normalized in cite


def _record_for(
    citation: Citation, result: dict[str, Any] | None, now: str
) -> VerificationRecord:
    if result is None:
        return VerificationRecord(
            citation_id=citation.citation_id,
            status=VerificationStatus.UNCONFIRMED,
            source="courtlistener",
            verified_at=now,
            notes="no matching result returned by citation-lookup",
        )
    raw_status = result.get("status")
    status = _STATUS_MAP.get(raw_status if isinstance(raw_status, int) else -1)
    if status is None:
        return VerificationRecord(
            citation_id=citation.citation_id,
            status=VerificationStatus.PENDING,
            source="courtlistener",
            verified_at=now,
            notes=f"unmapped citation-lookup status {raw_status!r}; retry",
        )
    return VerificationRecord(
        citation_id=citation.citation_id,
        status=status,
        source="courtlistener",
        source_url=_cluster_url(result) if status == VerificationStatus.VERIFIED else None,
        verified_at=now,
        notes=str(result.get("error_message") or ""),
    )


def _pending_chunk(chunk: list[Citation], now: str, note: str) -> list[VerificationRecord]:
    return [
        VerificationRecord(
            citation_id=c.citation_id,
            status=VerificationStatus.PENDING,
            source="courtlistener",
            verified_at=now,
            notes=note,
        )
        for c in chunk
    ]


def _lookup_chunk(
    chunk: list[Citation],
    now: str,
    limiter: TokenBucket,
    transport: http.Transport | None,
) -> list[VerificationRecord]:
    limiter.acquire(1)
    request = http.HttpRequest(
        method="POST",
        host=CL_HOST,
        path=CITATION_LOOKUP_PATH,
        json_body={"text": "\n".join(c.normalized for c in chunk)},
        auth_token_env=TOKEN_ENV,
    )
    try:
        response = http.fetch(request, transport=transport)
    except http.HttpError as exc:  # network/timeout -> fail closed
        return _pending_chunk(chunk, now, f"http error: {type(exc).__name__}")
    if response.status_code == 429:
        return _pending_chunk(chunk, now, "rate limited (429); retry")
    if response.status_code != 200 or not isinstance(response.json_body, list):
        return _pending_chunk(chunk, now, f"unexpected http {response.status_code}; retry")

    results: list[dict[str, Any]] = [r for r in response.json_body if isinstance(r, dict)]
    records: list[VerificationRecord] = []
    for citation in chunk:
        match = next((r for r in results if _result_matches(r, citation)), None)
        records.append(_record_for(citation, match, now))
    return records


def verify_cases(
    citations: list[Citation],
    *,
    now: str,
    limiter: TokenBucket | None = None,
    transport: http.Transport | None = None,
) -> list[VerificationRecord]:
    """Verify case citations against CourtListener, chunked and rate-limited."""
    bucket = limiter or default_limiter()
    records: list[VerificationRecord] = []
    for chunk in _chunks(citations, CHUNK_SIZE):
        records.extend(_lookup_chunk(chunk, now, bucket, transport))
    return records
