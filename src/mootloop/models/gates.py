"""Gate vocabulary: a `GateFinding` and the `GateResult` discriminated union
(`GatePass | GateFail | GatePending`).

Per plan D10, gate failures are *artifact states*, not exceptions — export does an
exhaustive ``match`` on ``status``. Exceptions stay reserved for infra errors.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from mootloop.models.common import StrictModel


class GateFinding(StrictModel):
    """One specific reason a gate reached its verdict (``code`` + human message)."""

    code: str
    message: str
    locator: str | None = None


class _GateBase(StrictModel):
    gate: str
    findings: list[GateFinding] = Field(default_factory=list)


class GatePass(_GateBase):
    """The artifact cleared this gate."""

    status: Literal["pass"] = "pass"


class GateFail(_GateBase):
    """The artifact is blocked by this gate — a terminal state for the artifact."""

    status: Literal["fail"] = "fail"


class GatePending(_GateBase):
    """The gate could not be evaluated yet (e.g. an external check is queued)."""

    status: Literal["pending"] = "pending"


# Discriminated on ``status`` so an exhaustive match is total and mypy-checkable.
GateResult = Annotated[GatePass | GateFail | GatePending, Field(discriminator="status")]


class GateLedger(StrictModel):
    """A bag of gate results with the one predicate export cares about."""

    results: list[GateResult] = Field(default_factory=list)

    def all_pass(self) -> bool:
        """True iff every recorded gate passed (empty ledger passes vacuously)."""
        return all(r.status == "pass" for r in self.results)

    def blocking(self) -> list[GateResult]:
        """The results that block export (``fail`` or ``pending``)."""
        return [r for r in self.results if r.status != "pass"]
