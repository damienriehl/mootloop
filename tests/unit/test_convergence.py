"""Convergence evaluator (plan D6): stops only when a draft stopped improving AND
stopped changing AND is complete — or the cap is hit."""

from __future__ import annotations

from mootloop.convergence import (
    ConvergenceEvaluator,
    RoundState,
    jaccard_similarity,
    sequence_similarity,
)
from mootloop.models.task import ConvergenceConfig

_CFG = ConvergenceConfig(score_delta_floor=0.02, material_change_floor=0.10, coverage_floor=0.80)
_CAP = 4


def _ev() -> ConvergenceEvaluator:
    return ConvergenceEvaluator(_CFG)


def test_converging_sequence_stops_before_cap() -> None:
    # Round 2 barely improves, barely changes, and is complete -> converged.
    history = [
        RoundState(score=0.60, coverage=0.90, text="the quick brown fox jumps over"),
        RoundState(score=0.905, coverage=0.90, text="the quick brown fox jumps over lazily"),
        RoundState(score=0.91, coverage=0.90, text="the quick brown fox jumps over lazily"),
    ]
    decision = _ev().evaluate(history, _CAP)
    assert decision.converged is True
    assert decision.reason == "converged"


def test_oscillation_not_converged_until_cap() -> None:
    # Score is flat (stopped improving) but the draft keeps churning -> NOT converged.
    churn = _ev().evaluate(
        [
            RoundState(score=0.80, coverage=0.95, text="alpha beta gamma delta"),
            RoundState(score=0.80, coverage=0.95, text="omega psi chi phi upsilon tau"),
        ],
        _CAP,
    )
    assert churn.converged is False
    assert churn.reason == "changing"
    assert churn.signals.material_change > _CFG.material_change_floor

    # ... and it only terminates once the hard cap is reached.
    at_cap = _ev().evaluate(
        [RoundState(score=0.80, coverage=0.95, text=f"draft {i}") for i in range(_CAP)],
        _CAP,
    )
    assert at_cap.converged is True
    assert at_cap.reason == "cap"


def test_vacuous_empty_never_converges() -> None:
    # Coverage below the floor: complete=False, so it cannot converge even when the
    # score and text have both gone flat.
    decision = _ev().evaluate(
        [
            RoundState(score=0.90, coverage=0.20, text="same words here"),
            RoundState(score=0.90, coverage=0.20, text="same words here"),
        ],
        _CAP,
    )
    assert decision.converged is False
    assert decision.reason == "incomplete"


def test_single_round_is_insufficient_history() -> None:
    decision = _ev().evaluate([RoundState(score=0.9, coverage=1.0, text="one round")], _CAP)
    assert decision.converged is False
    assert decision.reason == "insufficient-history"


def test_still_improving_not_converged() -> None:
    decision = _ev().evaluate(
        [
            RoundState(score=0.40, coverage=0.95, text="the same stable text here now"),
            RoundState(score=0.80, coverage=0.95, text="the same stable text here now"),
        ],
        _CAP,
    )
    assert decision.converged is False
    assert decision.reason == "improving"
    assert decision.signals.score_delta > _CFG.score_delta_floor


def test_similarity_helpers_are_bounded_and_order_sensitive() -> None:
    assert sequence_similarity("a b c", "a b c") == 1.0
    assert jaccard_similarity("a b c", "c b a") == 1.0  # set-based: order-insensitive
    assert sequence_similarity("a b c", "c b a") < 1.0  # order-sensitive: reorder = change
    assert 0.0 <= sequence_similarity("a b c d", "a x y z") <= 1.0
