from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from mootloop.citations import courtlistener_opinions
from mootloop.citations.propositions import extract_citation_propositions
from mootloop.errors import CitationError
from mootloop.models.citations import OpinionAuthorityStoreRecord

NOW = "2026-08-21T16:00:00+00:00"
SOURCE_URL = "https://www.courtlistener.com/opinion/108713/roe-v-wade/"
CANONICAL_SOURCE_URL = "https://www.courtlistener.com/opinion/108713/"


def _transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/rest/v4/clusters/108713/":
            return httpx.Response(
                200,
                json={
                    "sub_opinions": [
                        "https://www.courtlistener.com/api/rest/v4/opinions/12345/"
                    ]
                },
            )
        if request.url.path == "/api/rest/v4/opinions/12345/":
            return httpx.Response(
                200,
                json={
                    "html_with_citations": (
                        "<p>The question presented is unrelated.</p>"
                        "<p>We hold that particularized notice is required before relief.</p>"
                    )
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    return httpx.MockTransport(handler)


def test_fetch_case_authority_uses_only_numeric_fixed_api_paths() -> None:
    calls: list[httpx.Request] = []
    heartbeats = 0

    def heartbeat() -> None:
        nonlocal heartbeats
        heartbeats += 1

    result = courtlistener_opinions.fetch_case_authority(
        citation_id="cit-abc",
        source_url=SOURCE_URL,
        fetched_at=NOW,
        transport=_transport(calls),
        heartbeat=heartbeat,
    )

    assert result.note == ""
    assert result.snapshot is not None
    assert result.snapshot.cluster_id == 108713
    assert result.snapshot.opinion_ids == [12345]
    assert result.snapshot.source_url == CANONICAL_SOURCE_URL
    assert result.snapshot.text == (
        "The question presented is unrelated.\n\n"
        "We hold that particularized notice is required before relief."
    )
    assert result.snapshot.content_sha256 == hashlib.sha256(
        result.snapshot.text.encode("utf-8")
    ).hexdigest()
    assert [request.url.path for request in calls] == [
        "/api/rest/v4/clusters/108713/",
        "/api/rest/v4/opinions/12345/",
    ]
    assert heartbeats == 4


def test_fetch_case_authority_rejects_noncanonical_source_without_http() -> None:
    calls: list[httpx.Request] = []

    with pytest.raises(CitationError, match="canonical CourtListener opinion URL"):
        courtlistener_opinions.fetch_case_authority(
            citation_id="cit-abc",
            source_url="https://evil.example/opinion/108713/redirect/",
            fetched_at=NOW,
            transport=_transport(calls),
        )

    assert calls == []


def test_passage_selection_is_bounded_provenance_tagged_and_relevant() -> None:
    result = courtlistener_opinions.fetch_case_authority(
        citation_id="cit-abc",
        source_url=SOURCE_URL,
        fetched_at=NOW,
        transport=_transport([]),
    )
    assert result.snapshot is not None
    [proposition] = extract_citation_propositions(
        "Particularized notice is required. Smith v. Jones, 123 F.3d 456 (8th Cir. 2000)."
    )

    passages = courtlistener_opinions.select_passages(result.snapshot, proposition)

    assert passages
    assert "particularized notice" in passages[0].text.lower()
    assert passages[0].authority_sha256 == result.snapshot.content_sha256
    assert passages[0].source_url == CANONICAL_SOURCE_URL
    assert sum(len(p.text) for p in passages) <= courtlistener_opinions.MAX_PASSAGE_TOTAL_CHARS
    assert all(
        result.snapshot.text[p.start : p.end] == p.text
        and p.text_sha256 == hashlib.sha256(p.text.encode("utf-8")).hexdigest()
        for p in passages
    )


def test_authority_store_is_content_addressed_write_once(tmp_path: Path) -> None:
    result = courtlistener_opinions.fetch_case_authority(
        citation_id="cit-abc",
        source_url=SOURCE_URL,
        fetched_at=NOW,
        transport=_transport([]),
    )
    assert result.snapshot is not None
    store = courtlistener_opinions.OpinionAuthorityStore(tmp_path)

    path = store.capture(result.snapshot)
    assert store.capture(
        result.snapshot.model_copy(update={"fetched_at": "2026-08-22T16:00:00+00:00"})
    ) == path

    loaded = store.load("cit-abc", result.snapshot.content_sha256)
    assert loaded == result.snapshot
    assert OpinionAuthorityStoreRecord.model_validate_json(path.read_text()) == result.snapshot


def test_long_passage_window_keeps_the_relevant_text_not_just_the_prefix() -> None:
    result = courtlistener_opinions.fetch_case_authority(
        citation_id="cit-abc",
        source_url=SOURCE_URL,
        fetched_at=NOW,
        transport=_transport([]),
    )
    assert result.snapshot is not None
    authority = result.snapshot.model_copy(
        update={
            "text": "x " * 3_000 + "particularized notice is required" + " y" * 3_000,
        }
    )
    authority = authority.model_copy(
        update={
            "content_sha256": hashlib.sha256(authority.text.encode("utf-8")).hexdigest()
        }
    )
    [proposition] = extract_citation_propositions(
        "Particularized notice is required. Smith v. Jones, 123 F.3d 456 (8th Cir. 2000)."
    )

    [passage] = courtlistener_opinions.select_passages(authority, proposition)

    assert "particularized notice is required" in passage.text
    assert len(passage.text) <= courtlistener_opinions.MAX_PASSAGE_CHARS
