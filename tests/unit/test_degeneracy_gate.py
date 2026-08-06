"""Degeneracy-gate deterministic cases."""

from __future__ import annotations

import pytest

from mootloop.gates.degeneracy import evaluate
from mootloop.models.run import CritiqueOutput, DraftOutput, JudgeOutput, Objection


def _draft(**overrides: object) -> DraftOutput:
    base: dict[str, object] = {
        "response_text": "A substantive response.",
        "objections": [Objection(basis="relevance", text="Overbroad.")],
        "fact_ids_used": ["fact-abc"],
        "attorney_gate_items": [],
        "self_assessment": "ok",
    }
    base.update(overrides)
    return DraftOutput.model_validate(base)


def test_clean_draft_passes() -> None:
    assert evaluate(_draft()).status == "pass"


def test_empty_response_fails() -> None:
    result = evaluate(_draft(response_text="   "))
    assert result.status == "fail"
    assert any(f.code == "empty_response" for f in result.findings)


def test_objection_without_basis_fails() -> None:
    result = evaluate(_draft(objections=[Objection(basis="", text="x")]))
    assert result.status == "fail"
    assert any(f.code == "objection_no_basis" for f in result.findings)


def test_placeholder_marker_fails() -> None:
    result = evaluate(_draft(response_text="We respond [TODO fill in]."))
    assert result.status == "fail"
    assert any(f.code == "placeholder" for f in result.findings)


def test_ungrounded_draft_fails() -> None:
    result = evaluate(_draft(fact_ids_used=[], attorney_gate_items=[]))
    assert result.status == "fail"
    assert any(f.code == "ungrounded" for f in result.findings)


def test_attorney_gate_item_grounds_a_draft() -> None:
    result = evaluate(_draft(fact_ids_used=[], attorney_gate_items=["confirm delivery date"]))
    assert result.status == "pass"


def test_subject_to_hedge_fails() -> None:
    # "subject to and without waiving" is condemned (Liguria Foods, plan D7).
    result = evaluate(
        _draft(response_text="Subject to and without waiving the foregoing, Defendant answers.")
    )
    assert result.status == "fail"
    assert any(f.code == "hedge_subject_to" for f in result.findings)


def test_critique_needs_self_assessment() -> None:
    ok = CritiqueOutput(verdict="approve", self_assessment="fine")
    assert evaluate(ok).status == "pass"
    bad = CritiqueOutput(verdict="revise", self_assessment="  ")
    assert evaluate(bad).status == "fail"


def test_judge_output_passes_with_assessment() -> None:
    assert evaluate(JudgeOutput(rulings=[], self_assessment="ruled")).status == "pass"


@pytest.mark.parametrize(
    "text",
    [
        "Subject to and without waiving the foregoing, Defendant answers.",
        # The comma'd form is the MORE common drafting of the same boilerplate.
        "Subject to, and without waiving, the foregoing objections, Defendant answers.",
        "Subject to  and  without  waiving the foregoing, Defendant answers.",
        # A paste out of Word carries non-breaking spaces between the words.
        "Subject\xa0to\xa0and\xa0without\xa0waiving the foregoing, Defendant answers.",
        "Subject to and without waiver of the foregoing, Defendant answers.",
    ],
)
def test_subject_to_hedge_survives_punctuation_and_spacing_variants(text: str) -> None:
    result = evaluate(_draft(response_text=text))
    assert result.status == "fail"
    assert any(f.code == "hedge_subject_to" for f in result.findings)


def test_hedge_inside_an_objection_also_fails() -> None:
    """Objection text is rendered into the served document too (`export/master.py`)."""
    result = evaluate(
        _draft(
            response_text="Defendant answers as follows.",
            objections=[
                Objection(
                    basis="relevance",
                    text="Overbroad. Subject to, and without waiving, this objection, see below.",
                )
            ],
        )
    )
    assert result.status == "fail"
    assert any(f.code == "hedge_subject_to" for f in result.findings)


def test_hedge_matcher_does_not_fire_on_ordinary_prose() -> None:
    """Guard against the looser matcher over-firing on unrelated wording."""
    for benign in (
        "Defendant is subject to the protective order and will produce accordingly.",
        "Plaintiff waived the objection without further argument.",
        "This response is subject to the Court's scheduling order.",
    ):
        assert evaluate(_draft(response_text=benign)).status == "pass", benign
