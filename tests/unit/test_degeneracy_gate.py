"""Degeneracy-gate deterministic cases."""

from __future__ import annotations

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
