"""Citation extraction: eyecite coverage, the MN court-rule regex fallback, dedupe,
and authority-type classification (Phase 4 Unit 1)."""

from __future__ import annotations

from mootloop.citations.extract import extract_citations
from mootloop.models.citations import AuthorityType, make_citation_id

SAMPLE = (
    "Under Minn. Stat. § 336.2-207 and Minn. R. Civ. P. 33.01, see "
    "Nordwind v. Cassini, 512 N.W.2d 999 (Minn. 1994). Also 42 U.S.C. § 1983 "
    "and 17 C.F.R. § 240.10b-5."
)


def _by_type(text: str) -> dict[AuthorityType, list[str]]:
    out: dict[AuthorityType, list[str]] = {}
    for cite in extract_citations(text):
        out.setdefault(cite.authority_type, []).append(cite.normalized)
    return out


def test_classifies_each_authority_type() -> None:
    by_type = _by_type(SAMPLE)
    assert "512 N.W.2d 999" in by_type[AuthorityType.CASE]
    assert "Minn. Stat. § 336.2-207" in by_type[AuthorityType.STATE_STATUTE]
    assert by_type[AuthorityType.FEDERAL_STATUTE] == ["42 U.S.C. § 1983"]
    assert AuthorityType.REGULATION in by_type  # 17 C.F.R. ...
    # eyecite does not tokenize MN court rules — the regex fallback must catch it.
    assert "Minn. R. Civ. P. 33.01" in by_type[AuthorityType.COURT_RULE]


def test_case_raw_text_names_the_parties() -> None:
    [case] = [c for c in extract_citations(SAMPLE) if c.authority_type == AuthorityType.CASE]
    assert "Nordwind" in case.raw_text and "Cassini" in case.raw_text


def test_citation_id_is_content_addressed_and_stable() -> None:
    cites = extract_citations("Minn. Stat. § 336.2-207")
    assert len(cites) == 1
    assert cites[0].citation_id == make_citation_id("Minn. Stat. § 336.2-207")


def test_dedupe_by_normalized_form() -> None:
    text = "Minn. Stat. § 336.2-207 ... again Minn. Stat. § 336.2-207."
    cites = extract_citations(text)
    assert [c.normalized for c in cites] == ["Minn. Stat. § 336.2-207"]


def test_source_turn_id_propagates() -> None:
    cites = extract_citations("Minn. R. Civ. P. 33.01", source_turn_id="run-1-t0001")
    assert cites[0].source_turn_id == "run-1-t0001"


def test_empty_text_yields_nothing() -> None:
    assert extract_citations("no citations here at all") == []
