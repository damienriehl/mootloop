"""Verification clients + router + ledger (Phase 4 Unit 2): CourtListener status
semantics, MN Revisor scraping, egress allowlist, the staleness-aware ledger fold, the
cache (zero-HTTP re-run), research-queue routing, and research fulfillment — all with
``httpx.MockTransport``; no live network."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from mootloop.citations import courtlistener, http, mn_revisor
from mootloop.citations.extract import extract_citations
from mootloop.citations.http import HttpRequest, fetch
from mootloop.citations.ledger import ResearchQueue
from mootloop.citations.ratelimit import (
    CL_CAPACITY,
    TokenBucket,
    default_limiter,
    reset_default_limiter,
)
from mootloop.citations.verify import (
    CITATOR_DISCLOSURE,
    citation_gate,
    curated_path,
    fulfill_research_request,
    verify_all,
)
from mootloop.errors import CitationError, EgressError, OutboundPrivacyError
from mootloop.models.citations import (
    AuthorityType,
    VerificationRecord,
    VerificationStatus,
    fold_ledger,
    make_citation_id,
)

NOW = "2026-07-11T00:00:00+00:00"


def _case(text: str = "Roe v. Wade, 410 U.S. 113 (1973)"):
    return next(c for c in extract_citations(text) if c.authority_type == AuthorityType.CASE)


def _statute():
    return extract_citations("Minn. Stat. § 336.2-207")[0]


def _rule():
    return extract_citations("Minn. R. Civ. P. 33.01")[0]


def _counting_transport(response_factory, calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response_factory(request)
    return httpx.MockTransport(handler)


def _cl_transport(payload, status: int = 200, calls: list | None = None) -> httpx.MockTransport:
    return _counting_transport(
        lambda r: httpx.Response(status, json=payload),
        calls if calls is not None else [],
    )


def _rec(cid, status: VerificationStatus, verified_at: str = NOW) -> VerificationRecord:
    return VerificationRecord(
        citation_id=cid, status=status, source="courtlistener", verified_at=verified_at
    )


def _cl_result(case, status: int, url: str | None = None) -> dict:
    return {
        "citation": case.normalized,
        "normalized_citations": [case.normalized],
        "status": status,
        "clusters": [{"absolute_url": url}] if url else [],
    }


# --- egress control ---------------------------------------------------------


def test_fetch_rejects_off_allowlist_host() -> None:
    with pytest.raises(EgressError):
        fetch(HttpRequest("GET", "evil.example.com", "/"))


def test_fetch_rejects_non_absolute_path() -> None:
    with pytest.raises(EgressError):
        fetch(HttpRequest("GET", "www.courtlistener.com", "relative"))


@pytest.mark.parametrize(
    "outbound_request",
    [
        HttpRequest("GET", "www.courtlistener.com", "/"),
        HttpRequest("POST", "www.courtlistener.com", "/api/rest/v4/opinions/1/"),
        HttpRequest("GET", "www.revisor.mn.gov", "/statutes/search"),
        HttpRequest("POST", "www.revisor.mn.gov", "/statutes/cite/336.2-207"),
    ],
)
def test_fetch_rejects_allowed_host_with_unapproved_method_or_path(
    outbound_request: HttpRequest,
) -> None:
    with pytest.raises(EgressError, match="route"):
        fetch(outbound_request)


def test_hosted_legal_fetch_uses_only_authenticated_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "canaries.json"
    registry.write_text('{"canaries":{},"denylist":[]}', encoding="utf-8")
    monkeypatch.setenv("MOOTLOOP_RUNTIME_MODE", "hosted")
    monkeypatch.setenv("MOOTLOOP_CANARY_REGISTRY", str(registry))
    monkeypatch.setenv("MOOTLOOP_EGRESS_PROXY_URL", "http://egress-proxy:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient-proxy.invalid:8080")
    monkeypatch.setattr(
        "mootloop.engine.isolation.secrets.load_secret",
        lambda key: "legal pass" if "LEGAL" in key else "model pass",
    )
    observed: dict[str, object] = {}
    async_client = httpx.AsyncClient

    def capture_client(**kwargs: object) -> httpx.AsyncClient:
        observed.update(kwargs)
        return async_client(
            timeout=kwargs["timeout"],
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
            trust_env=kwargs["trust_env"],
        )

    monkeypatch.setattr(http.httpx, "AsyncClient", capture_client)

    response = fetch(
        HttpRequest("GET", "api.courtlistener.com", "/api/rest/v4/search/"),
    )

    assert response.status_code == 200
    assert observed["proxy"] == (
        "http://mootloop-legal:legal%20pass@egress-proxy:3128"
    )
    assert observed["trust_env"] is False
    assert observed["follow_redirects"] is False


@pytest.mark.parametrize(
    "outbound_request",
    [
        HttpRequest(
            "GET",
            "api.courtlistener.com",
            "/api/rest/v4/search/",
            auth_token_env="UNAPPROVED_SECRET",
        ),
        HttpRequest(
            "GET",
            "www.revisor.mn.gov",
            "/statutes/cite/336.2-207",
            auth_token_env="COURTLISTENER_TOKEN",
        ),
    ],
)
def test_legal_fetch_rejects_unapproved_secret_routing(outbound_request: HttpRequest) -> None:
    with pytest.raises(EgressError):
        fetch(outbound_request)


def test_hosted_legal_fetch_requires_dedicated_proxy_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "canaries.json"
    registry.write_text('{"canaries":{},"denylist":[]}', encoding="utf-8")
    monkeypatch.setenv("MOOTLOOP_RUNTIME_MODE", "hosted")
    monkeypatch.setenv("MOOTLOOP_CANARY_REGISTRY", str(registry))
    monkeypatch.setenv("MOOTLOOP_EGRESS_PROXY_URL", "http://egress-proxy:3128")
    monkeypatch.setattr("mootloop.engine.isolation.secrets.load_secret", lambda key: None)

    with pytest.raises(EgressError, match="LEGAL_EGRESS_PROXY_PASSWORD"):
        fetch(HttpRequest("GET", "api.courtlistener.com", "/api/rest/v4/search/"))


def test_outbound_legal_request_blocks_registered_canary_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "canaries.json"
    registry.write_text(
        '{"canaries":{"MOOTLOOP-CANARY-sibling":"sibling"},"denylist":[]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("MOOTLOOP_CANARY_REGISTRY", str(registry))
    called = False

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=[])

    with pytest.raises(OutboundPrivacyError, match="canary"):
        fetch(
            HttpRequest(
                "POST",
                "www.courtlistener.com",
                "/api/rest/v4/citation-lookup/",
                json_body={"text": "MOOTLOOP-CANARY-sibling"},
            ),
            transport=httpx.MockTransport(transport),
        )
    assert called is False


@pytest.mark.parametrize(
    "outbound_request",
    [
        HttpRequest(
            "GET",
            "api.courtlistener.com",
            "/api/rest/v4/search/",
            params={"q": "authorization Bearer shaped-token"},
        ),
        HttpRequest(
            "POST",
            "www.courtlistener.com",
            "/api/rest/v4/citation-lookup/",
            json_body={"text": "sk-abcdefgh"},
        ),
    ],
)
def test_outbound_legal_request_blocks_redactable_secret_shape_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outbound_request: HttpRequest,
) -> None:
    monkeypatch.setenv("MOOTLOOP_CANARY_REGISTRY", str(tmp_path / "missing-canaries.json"))
    called = False

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=[])

    with pytest.raises(OutboundPrivacyError, match="redaction"):
        fetch(outbound_request, transport=httpx.MockTransport(transport))
    assert called is False


# --- CourtListener status semantics -----------------------------------------


@pytest.mark.parametrize(
    "cl_status,expected",
    [
        (200, VerificationStatus.VERIFIED),
        (404, VerificationStatus.UNCONFIRMED),
        (400, VerificationStatus.INVALID),
        (300, VerificationStatus.AMBIGUOUS),
    ],
)
def test_courtlistener_status_mapping(cl_status: int, expected: VerificationStatus) -> None:
    case = _case()
    url = "/opinion/108713/roe-v-wade/" if cl_status == 200 else None
    payload = [_cl_result(case, cl_status, url)]
    [record] = courtlistener.verify_cases([case], now=NOW, transport=_cl_transport(payload))
    assert record.status == expected
    if expected == VerificationStatus.VERIFIED:
        assert record.source_url == "https://www.courtlistener.com/opinion/108713/roe-v-wade/"


def test_prefix_citation_never_borrows_another_results_verdict() -> None:
    """A reporter page number that prefixes another (`900 N.W.2d 1` vs `... 100`) must
    not inherit the longer cite's `200` and cluster URL. The substring match that did
    this wrote a HALLUCINATED cite into the ledger as `verified` with a real case's URL —
    the exact failure the citation subsystem exists to prevent."""
    real, fake = extract_citations(
        "Smith v. Jones, 900 N.W.2d 100 (Minn. 2017); Ghost v. Nobody, 900 N.W.2d 1 (Minn. 2017)."
    )
    payload = [
        {
            "citation": "900 N.W.2d 100",
            "normalized_citations": ["900 N.W.2d 100"],
            "status": 200,
            "clusters": [{"absolute_url": "/opinion/1/smith-v-jones/"}],
        },
        {
            "citation": "900 N.W.2d 1",
            "normalized_citations": ["900 N.W.2d 1"],
            "status": 404,
            "clusters": [],
        },
    ]
    records = {
        r.citation_id: r
        for r in courtlistener.verify_cases(
            [real, fake], now=NOW, transport=_cl_transport(payload)
        )
    }
    assert records[real.citation_id].status == VerificationStatus.VERIFIED
    assert records[fake.citation_id].status == VerificationStatus.UNCONFIRMED
    assert records[fake.citation_id].source_url is None


def test_citation_absent_from_the_response_is_unconfirmed_not_verified() -> None:
    """Fail closed: a cite the API simply did not answer for gets no other cite's row."""
    case = _case()
    payload = [
        {
            "citation": "999 U.S. 999",
            "normalized_citations": ["999 U.S. 999"],
            "status": 200,
            "clusters": [{"absolute_url": "/opinion/2/other/"}],
        }
    ]
    [record] = courtlistener.verify_cases([case], now=NOW, transport=_cl_transport(payload))
    assert record.status == VerificationStatus.UNCONFIRMED
    assert record.source_url is None


def test_courtlistener_http_429_is_pending() -> None:
    case = _case()
    [record] = courtlistener.verify_cases([case], now=NOW, transport=_cl_transport([], status=429))
    assert record.status == VerificationStatus.PENDING


def test_courtlistener_network_error_fails_closed_to_pending() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    [record] = courtlistener.verify_cases(
        [_case()], now=NOW, transport=httpx.MockTransport(boom)
    )
    assert record.status == VerificationStatus.PENDING


def test_courtlistener_chunks_at_250() -> None:
    cites = [_case(f"Foo v. Bar {n}, {n} U.S. {n} (2000)") for n in range(1, 252)]
    chunks = courtlistener._chunks(cites, courtlistener.CHUNK_SIZE)
    assert [len(c) for c in chunks] == [250, 1]


# --- MN Revisor -------------------------------------------------------------


def test_mn_statute_verified_when_page_has_section() -> None:
    statute = _statute()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/statutes/cite/336.2-207"
        return httpx.Response(200, text="MN Statutes 336.2-207 Additional terms ...")

    record = mn_revisor.verify_mn(statute, now=NOW, transport=httpx.MockTransport(handler))
    assert record.status == VerificationStatus.VERIFIED
    assert record.content_sha256 is not None


def test_mn_statute_404_is_invalid() -> None:
    record = mn_revisor.verify_mn(
        _statute(), now=NOW, transport=httpx.MockTransport(lambda r: httpx.Response(404))
    )
    assert record.status == VerificationStatus.INVALID


def test_mn_rule_uses_whole_rule_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/court_rules/cp/id/33/"
        return httpx.Response(200, text="Rule 33.01 Interrogatories ...")

    record = mn_revisor.verify_mn(_rule(), now=NOW, transport=httpx.MockTransport(handler))
    assert record.status == VerificationStatus.VERIFIED


def test_mn_missing_section_on_page_is_pending() -> None:
    record = mn_revisor.verify_mn(
        _statute(), now=NOW, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="x"))
    )
    assert record.status == VerificationStatus.PENDING


# --- ledger fold (pure, deterministic) --------------------------------------


def test_fold_latest_record_wins() -> None:
    cid = make_citation_id("x")
    old = _rec(cid, VerificationStatus.PENDING)
    new = _rec(cid, VerificationStatus.VERIFIED)
    folded = fold_ledger([old, new], now=datetime.fromisoformat(NOW), max_cache_age_days=30)
    assert folded[cid].status == VerificationStatus.VERIFIED


def test_fold_expires_stale_verified_to_pending() -> None:
    cid = make_citation_id("x")
    old_ts = (datetime.fromisoformat(NOW) - timedelta(days=90)).isoformat()
    rec = _rec(cid, VerificationStatus.VERIFIED, old_ts)
    folded = fold_ledger([rec], now=datetime.fromisoformat(NOW), max_cache_age_days=30)
    assert folded[cid].status == VerificationStatus.PENDING
    assert "stale" in folded[cid].notes


# --- router: cache, routing, curated ----------------------------------------


def _cl_verified_payload(case) -> list[dict]:
    return [_cl_result(case, 200, "/opinion/1/x/")]


def test_router_cache_hit_makes_zero_http_calls(tmp_path: Path) -> None:
    case = _case()
    calls: list[httpx.Request] = []
    transport = _cl_transport(_cl_verified_payload(case), calls=calls)

    first = verify_all(tmp_path, [case], NOW, transport=transport)
    assert first.counts().get("verified") == 1
    assert len(calls) == 1

    # Second pass: the ledger already has a fresh verified record -> no HTTP.
    calls.clear()
    second = verify_all(tmp_path, [case], NOW, transport=transport)
    assert second.counts().get("verified") == 1
    assert calls == []


def test_router_stale_entry_reverifies(tmp_path: Path) -> None:
    case = _case()
    old_ts = (datetime.fromisoformat(NOW) - timedelta(days=90)).isoformat()
    calls: list[httpx.Request] = []
    transport = _cl_transport(_cl_verified_payload(case), calls=calls)
    verify_all(tmp_path, [case], old_ts, transport=transport)
    calls.clear()
    # A now well past max_cache_age folds the verified entry stale -> re-verify (HTTP).
    verify_all(tmp_path, [case], NOW, transport=transport, max_cache_age_days=30)
    assert len(calls) == 1


def test_router_routes_needs_research_and_queues(tmp_path: Path) -> None:
    fed = extract_citations("42 U.S.C. § 1983")[0]
    summary = verify_all(tmp_path, [fed], NOW)
    assert summary.counts().get("needs_research") == 1
    assert summary.research_request_ids
    assert ResearchQueue(tmp_path).open_requests()


def test_router_curated_tier_verifies_without_network(tmp_path: Path) -> None:
    fed = extract_citations("42 U.S.C. § 1983")[0]
    path = curated_path(tmp_path, fed.normalized)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 42 U.S.C. 1983\nCurated authority text.\n", encoding="utf-8")
    summary = verify_all(tmp_path, [fed], NOW)
    assert summary.counts().get("verified") == 1
    assert summary.outcomes[0].status == VerificationStatus.VERIFIED


def test_summary_carries_citator_disclosure(tmp_path: Path) -> None:
    assert verify_all(tmp_path, [], NOW).disclosure == CITATOR_DISCLOSURE


# --- citation gate + research fulfillment ------------------------------------


def test_citation_gate_blocks_unconfirmed(tmp_path: Path) -> None:
    case = _case()
    verify_all(tmp_path, [case], NOW, transport=_cl_transport([_cl_result(case, 404)]))
    gate = citation_gate(tmp_path, [case], now=NOW)
    assert gate.status == "fail"
    assert case.raw_text in gate.findings[0].message or case.citation_id == gate.findings[0].locator


def test_citation_gate_passes_when_all_verified(tmp_path: Path) -> None:
    case = _case()
    verify_all(tmp_path, [case], NOW, transport=_cl_transport(_cl_verified_payload(case)))
    assert citation_gate(tmp_path, [case], now=NOW).status == "pass"


def test_fulfill_research_request_clears_the_gate(tmp_path: Path) -> None:
    fed = extract_citations("42 U.S.C. § 1983")[0]
    summary = verify_all(tmp_path, [fed], NOW)
    request_id = summary.research_request_ids[0]
    assert citation_gate(tmp_path, [fed], now=NOW).status == "pending"

    authority = tmp_path / "authority.md"
    authority.write_text("# 42 U.S.C. 1983 — curated\n", encoding="utf-8")
    record = fulfill_research_request(tmp_path, request_id, file=authority, now=NOW, url="https://x/")
    assert record.status == VerificationStatus.VERIFIED
    assert ResearchQueue(tmp_path).get(request_id).status == "fulfilled"
    assert citation_gate(tmp_path, [fed], now=NOW).status == "pass"


def test_fulfill_unknown_request_raises(tmp_path: Path) -> None:
    authority = tmp_path / "a.md"
    authority.write_text("x", encoding="utf-8")
    with pytest.raises(CitationError):
        fulfill_research_request(tmp_path, "research-nope", file=authority, now=NOW)


# --- token bucket -----------------------------------------------------------


def test_token_bucket_sleeps_only_when_empty() -> None:
    clock_time = [0.0]
    slept: list[float] = []
    bucket = TokenBucket(
        capacity=2,
        refill_per_second=1.0,
        clock=lambda: clock_time[0],
        sleep=lambda s: (slept.append(s), clock_time.__setitem__(0, clock_time[0] + s)),
    )
    bucket.acquire()
    bucket.acquire()
    assert slept == []  # first two are free
    bucket.acquire()
    assert slept and slept[0] == pytest.approx(1.0)  # third waits one refill


def test_default_limiter_is_one_shared_process_wide_bucket() -> None:
    """The documented 60/min budget is only real if every caller draws on ONE bucket.

    A fresh bucket per call meant N concurrent verifications could issue 60N
    requests a minute at CourtListener while each looked compliant.
    """
    reset_default_limiter()
    try:
        first = default_limiter()
        assert default_limiter() is first  # same instance, not a look-alike
        first.acquire(CL_CAPACITY)  # drain the whole minute's budget
        # A caller that asks for "the" limiter now inherits the drained budget.
        assert default_limiter().tokens < 1.0
    finally:
        reset_default_limiter()
