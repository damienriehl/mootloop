"""MN court-rule routing: the General Rules of Practice are their own body of rules.

`Minn. Gen. R. Prac. 115.03` is not a Rule of Civil Procedure. Routing it to the
Revisor's civil-procedure index (`/court_rules/cp/`) 404s — and a 404 is reported
``invalid``, i.e. the tool tells the attorney a correct citation is fake. That is the
damaging direction: it invites stripping a good cite or distrusting the verifier.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mootloop.citations import mn_revisor
from mootloop.citations.extract import extract_citations
from mootloop.citations.ledger import ResearchQueue
from mootloop.citations.verify import verify_all
from mootloop.models.citations import (
    AuthorityType,
    Citation,
    VerificationStatus,
    make_citation_id,
)

NOW = "2026-07-11T00:00:00+00:00"

GEN_R_PRAC = "Minn. Gen. R. Prac. 115.03"
CIV_P = "Minn. R. Civ. P. 33.01"


def _cite(text: str) -> Citation:
    [found] = [c for c in extract_citations(text) if c.authority_type == AuthorityType.COURT_RULE]
    return found


def _revisor(paths: list[str]) -> httpx.MockTransport:
    """A transport standing in for the Revisor: only the index that actually holds the
    rule serves it; every other court-rule index 404s, exactly as the site does."""

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/court_rules/gp/id/115/":
            return httpx.Response(200, text="Rule 115.03 Motion Practice ...")
        if request.url.path == "/court_rules/cp/id/33/":
            return httpx.Response(200, text="Rule 33.01 Interrogatories ...")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_gen_r_prac_resolves_against_the_general_rules_index() -> None:
    paths: list[str] = []
    record = mn_revisor.verify_mn(_cite(GEN_R_PRAC), now=NOW, transport=_revisor(paths))
    assert paths == ["/court_rules/gp/id/115/"]
    assert record.status == VerificationStatus.VERIFIED


def test_gen_r_prac_is_never_reported_invalid() -> None:
    """The failure this exists to prevent: a valid cite marked ``invalid``."""
    record = mn_revisor.verify_mn(_cite(GEN_R_PRAC), now=NOW, transport=_revisor([]))
    assert record.status != VerificationStatus.INVALID


def test_civil_procedure_rule_still_routes_to_the_civil_procedure_index() -> None:
    paths: list[str] = []
    record = mn_revisor.verify_mn(_cite(CIV_P), now=NOW, transport=_revisor(paths))
    assert paths == ["/court_rules/cp/id/33/"]
    assert record.status == VerificationStatus.VERIFIED


def test_unknown_rule_body_is_not_guessed_into_an_index() -> None:
    """A MN rule body with no pinned Revisor index must not be fetched against some
    other body's index — a 404 there would be a false ``invalid``, not an answer."""
    normalized = "Minn. R. Evid. 401"
    unknown = Citation(
        citation_id=make_citation_id(normalized),
        raw_text=normalized,
        normalized=normalized,
        authority_type=AuthorityType.COURT_RULE,
    )
    assert mn_revisor.revisor_index_for(unknown) is None
    paths: list[str] = []
    record = mn_revisor.verify_mn(unknown, now=NOW, transport=_revisor(paths))
    assert paths == []  # nothing fetched
    assert record.status not in (VerificationStatus.INVALID, VerificationStatus.VERIFIED)


def test_router_queues_an_unverifiable_rule_body_for_research(tmp_path: Path) -> None:
    """End to end through the router: a rule body the Revisor scraper cannot answer
    becomes a human research request, the designed path for "the free stack cannot
    verify this" — not a network guess whose 404 reads as ``invalid``."""
    normalized = "Minn. R. Evid. 401"
    unknown = Citation(
        citation_id=make_citation_id(normalized),
        raw_text=normalized,
        normalized=normalized,
        authority_type=AuthorityType.COURT_RULE,
    )
    paths: list[str] = []
    summary = verify_all(tmp_path, [unknown], NOW, transport=_revisor(paths))

    assert paths == []  # never fetched
    [outcome] = summary.outcomes
    assert outcome.status == VerificationStatus.NEEDS_RESEARCH
    assert summary.research_request_ids
    assert [r.normalized for r in ResearchQueue(tmp_path).open_requests()] == [normalized]


@pytest.mark.parametrize("text", [GEN_R_PRAC, CIV_P])
def test_both_mn_rule_bodies_still_extract_as_court_rules(text: str) -> None:
    assert _cite(text).normalized == text
