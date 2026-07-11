"""Panel fold math + the restructure trigger threshold (plan Phase 6)."""

from __future__ import annotations

from mootloop.models.run import JudgeOutput, Objection, ObjectionRuling
from mootloop.panels import DEFAULT_RESTRUCTURE_THRESHOLD, fold_objection_results


def _ruling(basis: str, survive: bool) -> ObjectionRuling:
    return ObjectionRuling(
        objection_basis=basis,
        would_objection_survive=survive,
        reasoning=f"{basis} reasoning",
        persuasion_notes="notes",
    )


def _judge(*rulings: ObjectionRuling) -> JudgeOutput:
    return JudgeOutput(rulings=list(rulings), self_assessment="ruled")


def test_fold_counts_votes_and_rate() -> None:
    objections = [Objection(basis="relevance", text="Overbroad.")]
    panel = [
        _judge(_ruling("relevance", True)),
        _judge(_ruling("relevance", False)),
        _judge(_ruling("relevance", True)),
    ]
    [result] = fold_objection_results("run-1", "ROG-1", objections, panel)
    assert result.total_votes == 3
    assert result.survive_votes == 2
    assert result.survival_rate == 2 / 3
    assert result.objection_index == 0
    assert result.objection_basis == "relevance"
    assert len(result.reasoning_samples) == 3


def test_fold_matches_by_basis_out_of_order() -> None:
    objections = [
        Objection(basis="relevance", text="a"),
        Objection(basis="privilege", text="b"),
    ]
    # Judge lists the two rulings in the opposite order — basis match still aligns them.
    panel = [_judge(_ruling("privilege", False), _ruling("relevance", True))]
    results = fold_objection_results("run-1", "RFP-2", objections, panel)
    by_basis = {r.objection_basis: r for r in results}
    assert by_basis["relevance"].survive_votes == 1
    assert by_basis["privilege"].survive_votes == 0


def test_fold_positional_fallback_when_basis_absent() -> None:
    objections = [Objection(basis="relevance", text="a")]
    panel = [_judge(_ruling("overbreadth", True))]  # basis differs -> positional
    [result] = fold_objection_results("run-1", "ROG-3", objections, panel)
    assert result.total_votes == 1
    assert result.survive_votes == 1


def test_unanimous_survival_is_above_threshold() -> None:
    objections = [Objection(basis="relevance", text="a")]
    panel = [_judge(_ruling("relevance", True)) for _ in range(3)]
    [result] = fold_objection_results("run-1", "ROG-1", objections, panel)
    assert result.survival_rate == 1.0
    assert result.survival_rate >= DEFAULT_RESTRUCTURE_THRESHOLD  # no restructure


def test_minority_survival_is_below_threshold() -> None:
    objections = [Objection(basis="relevance", text="a")]
    panel = [
        _judge(_ruling("relevance", True)),
        _judge(_ruling("relevance", False)),
        _judge(_ruling("relevance", False)),
    ]
    [result] = fold_objection_results("run-1", "ROG-1", objections, panel)
    assert result.survival_rate == 1 / 3
    assert result.survival_rate < DEFAULT_RESTRUCTURE_THRESHOLD  # triggers restructure


def test_no_objections_folds_to_empty() -> None:
    assert fold_objection_results("run-1", "ROG-1", [], [_judge()]) == []
