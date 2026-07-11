"""Fabrication gate: fact existence, provenance-required assertions (amounts / dates /
quotes), the grounding floor, and the citations-pending path (Phase 4 Unit 3)."""

from __future__ import annotations

from mootloop.gates.fabrication import check
from mootloop.models.facts import Fact, Provenance
from mootloop.models.run import DraftOutput

CORPUS = "The contract price was $148,500 and tender occurred on March 14, 2026."


def _fact() -> Fact:
    return Fact(
        fact_id="fact-abc",
        statement="The contract price of $148,500 was agreed.",
        provenance=[Provenance(doc_id="doc-1", quote="Buyer shall pay $148,500.")],
        confidence=0.9,
        version=1,
    )


def _draft(**kw) -> DraftOutput:
    base = {
        "response_text": "Response.",
        "objections": [],
        "candidate_citations": [],
        "fact_ids_used": ["fact-abc"],
        "attorney_gate_items": [],
        "self_assessment": "ok",
    }
    base.update(kw)
    return DraftOutput.model_validate(base)


def test_pass_when_amount_traces_to_cited_fact() -> None:
    draft = _draft(response_text="We agree the price was $148,500.")
    assert check(draft, [_fact()], CORPUS).status == "pass"


def test_unsupported_amount_fails() -> None:
    draft = _draft(response_text="The price was $999,999.")
    result = check(draft, [_fact()], CORPUS)
    assert result.status == "fail"
    assert any(f.code == "unsupported_amount" for f in result.findings)


def test_unknown_fact_id_fails() -> None:
    draft = _draft(fact_ids_used=["fact-missing"])
    result = check(draft, [_fact()], CORPUS)
    assert result.status == "fail"
    assert any(f.code == "unknown_fact" for f in result.findings)


def test_ungrounded_draft_fails_even_with_no_amounts() -> None:
    draft = _draft(fact_ids_used=[], attorney_gate_items=[])
    result = check(draft, [_fact()], CORPUS)
    assert result.status == "fail"
    assert any(f.code == "ungrounded" for f in result.findings)


def test_attorney_gate_item_satisfies_grounding_floor() -> None:
    draft = _draft(fact_ids_used=[], attorney_gate_items=["verify basis"])
    assert check(draft, [_fact()], CORPUS).status == "pass"


def test_unsupported_quote_fails() -> None:
    draft = _draft(response_text='The witness said "the moon is made of granite".')
    result = check(draft, [_fact()], CORPUS)
    assert result.status == "fail"
    assert any(f.code == "unsupported_quote" for f in result.findings)


def test_amount_supported_by_corpus_when_not_in_fact() -> None:
    # $148,500 is in the corpus text even if a cited fact omitted it.
    bare = Fact(fact_id="fact-abc", statement="Tender occurred.", confidence=0.9, version=1)
    draft = _draft(response_text="The price was $148,500.")
    assert check(draft, [bare], CORPUS).status == "pass"


def test_candidate_citations_yield_pending() -> None:
    draft = _draft(candidate_citations=["512 N.W.2d 999"])
    result = check(draft, [_fact()], CORPUS)
    assert result.status == "pending"
    assert any(f.code == "citations_pending" for f in result.findings)
