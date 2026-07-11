"""PANEL_RESULT vocabulary (plan D12): the objection-survival distribution a judge
panel produces, folded from the panel's `JudgeOutput` turns, plus the per-request
`PanelReport` derived view (written to ``scores/panels/report.json``).

A `PanelResult` is one objection's distribution across the panel: how many of the
`total_votes` judges ruled it would survive a motion to compel, the derived
`survival_rate`, and a few reasoning samples. When an objection's survival rate falls
below the task's restructure threshold, the associate re-enters for a costed
restructure pass (plan Phase 6).
"""

from __future__ import annotations

from pydantic import Field

from mootloop.models.common import RequestId, StrictModel, VersionedModel

SCHEMA_VERSION = "1.0"


class PanelResult(StrictModel):
    """One objection's survival distribution across the judge panel."""

    run_id: str
    request_id: RequestId
    objection_index: int = Field(ge=0)
    objection_basis: str
    survive_votes: int = Field(ge=0)
    total_votes: int = Field(ge=0)
    survival_rate: float = Field(ge=0.0, le=1.0)
    reasoning_samples: list[str] = Field(default_factory=list)


class PanelReport(VersionedModel):
    """The run's objection-survival distribution report (a derived view)."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    results: list[PanelResult] = Field(default_factory=list)

    def for_request(self, request_id: str) -> list[PanelResult]:
        return [r for r in self.results if str(r.request_id) == request_id]

    def weak(self, threshold: float) -> list[PanelResult]:
        """Objections whose survival rate fell below ``threshold`` (with votes cast)."""
        return [r for r in self.results if r.total_votes > 0 and r.survival_rate < threshold]
