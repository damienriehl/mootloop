"""Panel fold math + the restructure trigger threshold (plan Phase 6)."""

from __future__ import annotations

from mootloop.models.panels import PanelReport
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


def test_duplicate_basis_objections_get_their_own_ruling() -> None:
    # Two objections on the same basis: each must be scored against its OWN ruling.
    # Re-using the first basis match scored the second objection as surviving when
    # the judge said it would not — and `RestructureStage` then never re-enters.
    objections = [
        Objection(basis="relevance", text="Overbroad as to time."),
        Objection(basis="relevance", text="Seeks unrelated product lines."),
    ]
    panel = [_judge(_ruling("relevance", True), _ruling("relevance", False))]
    first, second = fold_objection_results("run-1", "ROG-4", objections, panel)
    assert (first.objection_index, first.survive_votes, first.total_votes) == (0, 1, 1)
    assert (second.objection_index, second.survive_votes, second.total_votes) == (1, 0, 1)


def test_duplicate_basis_below_threshold_triggers_restructure() -> None:
    objections = [
        Objection(basis="relevance", text="a"),
        Objection(basis="relevance", text="b"),
    ]
    # Every judge sustains the first relevance objection and rejects the second.
    panel = [_judge(_ruling("relevance", True), _ruling("relevance", False)) for _ in range(3)]
    results = fold_objection_results("run-1", "ROG-5", objections, panel)
    report = PanelReport(run_id="run-1", results=results)
    weak = report.weak(DEFAULT_RESTRUCTURE_THRESHOLD)
    assert [r.objection_index for r in weak] == [1]


def test_ruling_is_never_counted_twice() -> None:
    # One ruling, two objections sharing its basis: the unmatched objection records
    # no vote rather than borrowing the first objection's ruling.
    objections = [Objection(basis="relevance", text="a"), Objection(basis="relevance", text="b")]
    panel = [_judge(_ruling("relevance", False))]
    first, second = fold_objection_results("run-1", "ROG-6", objections, panel)
    assert (first.total_votes, first.survive_votes) == (1, 0)
    assert (second.total_votes, second.survive_votes) == (0, 0)


def test_index_aligned_ruling_wins_among_same_basis() -> None:
    objections = [
        Objection(basis="privilege", text="a"),
        Objection(basis="relevance", text="b"),
        Objection(basis="relevance", text="c"),
    ]
    panel = [
        _judge(_ruling("privilege", True), _ruling("relevance", False), _ruling("relevance", True))
    ]
    results = fold_objection_results("run-1", "ROG-7", objections, panel)
    assert [r.survive_votes for r in results] == [1, 0, 1]
