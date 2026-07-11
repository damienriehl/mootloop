"""Budget vocabulary (plan D5): the dated price table's row type, the per-call token
assumptions an estimate rests on, and the estimate range/breakdown a pre-run
estimate returns.

The metering formula and the price table itself live in ``mootloop.budget``; these
are the value types that flow across the CLI and journal boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pydantic import Field

from mootloop.models.common import StrictModel

# The four budget model-mix roles (plan D5). Every persona maps to exactly one.
ROLES: tuple[str, ...] = ("personas", "judges", "rubric", "cite")


@dataclass(frozen=True)
class PriceWindow:
    """A dated price row: $/1e6 tokens in effect on ``[effective_from, effective_to]``
    (``effective_to=None`` means open-ended)."""

    effective_from: date
    effective_to: date | None
    input_per_mtok: float
    output_per_mtok: float

    def covers(self, on: date) -> bool:
        if on < self.effective_from:
            return False
        return self.effective_to is None or on <= self.effective_to


class EstimateAssumptions(StrictModel):
    """Per-call token assumptions a pre-run estimate rests on (plan D5)."""

    uncached_in: int = 8_000
    cache_read: int = 15_000
    out_personas: int = 1_500
    out_judges: int = 800
    out_rubric: int = 600
    out_cite: int = 400
    objections_per_request: int = 3
    derail_factor: float = 1.1

    def output_for(self, role: str) -> int:
        return {
            "personas": self.out_personas,
            "judges": self.out_judges,
            "rubric": self.out_rubric,
            "cite": self.out_cite,
        }[role]


class StageEstimate(StrictModel):
    """One stage's contribution to the estimate range."""

    stage: str
    role: str
    model: str
    min_calls: int
    max_calls: int
    min_usd: float
    max_usd: float


class EstimateRange(StrictModel):
    """A pre-run cost range: convergence-early (min) to all-caps (max), with a
    per-stage breakdown (plan D5)."""

    tier: str
    requests: int
    min_usd: float
    max_usd: float
    breakdown: list[StageEstimate] = Field(default_factory=list)
