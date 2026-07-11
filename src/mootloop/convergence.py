"""Drafting-loop convergence (plan D6).

Structure copied from alea-intake's weighted-signal `ConvergenceEvaluator`
(``backend/app/services/analysis/convergence.py``, MIT — see THIRD-PARTY.md); the
signals are **re-mapped for drafting** per plan D1's correction. alea's intake model
(``coverage``, ``confidence_plateau``, ``user_fatigue``, ``diminishing_gaps``) does
not carry over — ``user_fatigue`` is dropped and the loop rule is an explicit AND of
three floors rather than a weighted-threshold vote:

    converged  ⇔  score_delta below floor   (stopped *improving*)
             AND  material_change below floor (stopped *changing*)
             AND  coverage above floor        (is *complete*)
             OR   the iteration cap is hit    (reason="cap")

This guards both *vacuous* convergence (a draft that never became complete) and
critic-induced *oscillation* (a draft that keeps changing without improving).

No embedding dependency: material change is ``1 - similarity`` where similarity is a
deterministic token-level ``difflib.SequenceMatcher`` ratio (order-sensitive, so a
reshuffle counts as change) and Jaccard is available as an alternative.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from mootloop.models.common import StrictModel
from mootloop.models.task import ConvergenceConfig


@dataclass(frozen=True)
class RoundState:
    """One partner-loop round's convergence inputs."""

    score: float  # weighted rubric score in [0, 1]
    coverage: float  # fraction of presence criteria passing, [0, 1]
    text: str  # the round's operative draft text


class DraftingSignals(StrictModel):
    """The re-mapped drafting signals (cf. alea `ConvergenceSignals`)."""

    score_delta: float
    coverage: float
    material_change: float
    iteration_number: int
    max_iterations: int


class ConvergenceDecision(StrictModel):
    """The evaluator's verdict for a round, with the signals that produced it."""

    converged: bool
    reason: str
    signals: DraftingSignals


def _tokens(text: str) -> list[str]:
    return text.split()


def sequence_similarity(a: str, b: str) -> float:
    """Order-sensitive token similarity in [0, 1] (difflib ratio; no embeddings)."""
    return SequenceMatcher(None, _tokens(a), _tokens(b)).ratio()


def jaccard_similarity(a: str, b: str) -> float:
    """Set-based token similarity in [0, 1] (order-insensitive alternative)."""
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta and not tb:
        return 1.0
    union = ta | tb
    return len(ta & tb) / len(union) if union else 1.0


class ConvergenceEvaluator:
    """Decides whether a drafting loop should stop (plan D6)."""

    def __init__(self, config: ConvergenceConfig | None = None) -> None:
        self.config = config or ConvergenceConfig()

    def evaluate(self, history: list[RoundState], max_iterations: int) -> ConvergenceDecision:
        n = len(history)
        current = history[-1] if history else RoundState(0.0, 0.0, "")

        # Hard cap always terminates (reason="cap"), regardless of the floors.
        if n >= max_iterations:
            return ConvergenceDecision(
                converged=True,
                reason="cap",
                signals=DraftingSignals(
                    score_delta=0.0,
                    coverage=current.coverage,
                    material_change=0.0,
                    iteration_number=n,
                    max_iterations=max_iterations,
                ),
            )

        # Need a prior round to measure a delta or a material change.
        if n < 2:
            return ConvergenceDecision(
                converged=False,
                reason="insufficient-history",
                signals=DraftingSignals(
                    score_delta=0.0,
                    coverage=current.coverage,
                    material_change=1.0,
                    iteration_number=n,
                    max_iterations=max_iterations,
                ),
            )

        prev = history[-2]
        score_delta = current.score - prev.score
        material_change = 1.0 - sequence_similarity(prev.text, current.text)
        signals = DraftingSignals(
            score_delta=score_delta,
            coverage=current.coverage,
            material_change=material_change,
            iteration_number=n,
            max_iterations=max_iterations,
        )

        stopped_improving = abs(score_delta) < self.config.score_delta_floor
        stopped_changing = material_change < self.config.material_change_floor
        complete = current.coverage >= self.config.coverage_floor

        if stopped_improving and stopped_changing and complete:
            return ConvergenceDecision(converged=True, reason="converged", signals=signals)

        if not complete:
            reason = "incomplete"
        elif not stopped_changing:
            reason = "changing"
        else:
            reason = "improving"
        return ConvergenceDecision(converged=False, reason=reason, signals=signals)
