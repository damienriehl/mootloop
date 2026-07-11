"""The LOCKED, versioned rubric (plan D7/D12): criteria, the loader with a content-
hash lock check, and the scoring math the convergence loop and final gate share.

A rubric is *locked*: its content hash is recorded beside it in a ``.sha256`` file,
and the loader refuses to run if the two diverge. Changing a locked rubric is a new
*version file*, never an in-place edit — the pinned ``rubric_version`` in every
``RunStarted`` event stays meaningful across a matter's life.

Two criterion *kinds*: ``present`` criteria are deterministic-checkable (the
completeness gate scores them in code); ``correct`` criteria are judge-scored 0-5.
Only the ``correct`` criteria are ever shown to the rubric judge.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml

from mootloop.errors import RubricLockError, TaskConfigError
from mootloop.models.common import StrictModel
from mootloop.models.gates import GateFail, GateFinding, GatePass, GateResult

CriterionKind = Literal["present", "correct"]
AppliesTo = Literal["rog", "rfp", "rfa", "all"]

RUBRIC_GATE_NAME = "rubric"


class Criterion(StrictModel):
    """One rubric criterion. ``present`` -> code-checked; ``correct`` -> judge-scored."""

    id: str
    name: str
    description: str
    kind: CriterionKind
    weight: float
    applies_to: AppliesTo


class Rubric(StrictModel):
    """A parsed, LOCKED rubric file."""

    rubric_id: str
    version: str
    locked: bool
    criteria: list[Criterion]

    # -- criterion views --
    def _for_code(self, kind: CriterionKind, code: str) -> list[Criterion]:
        return [
            c for c in self.criteria if c.kind == kind and c.applies_to in (code, "all")
        ]

    def presence_criteria(self, code: str = "all") -> list[Criterion]:
        return self._for_code("present", code)

    def correctness_criteria(self, code: str = "all") -> list[Criterion]:
        return self._for_code("correct", code)

    # -- scoring math (shared by in-loop convergence + final gate) --
    def weighted_score(self, scores: dict[str, float], code: str) -> float:
        """Weighted, length-normalized correctness score in [0, 1].

        ``scores`` maps criterion id -> raw 0-5 score. Only applicable correctness
        criteria that have a score contribute; the denominator is their weight sum,
        so a partial score set still normalizes correctly (anti-bias: no criterion
        an off-topic judge skipped drags the mean).
        """
        applicable = self.correctness_criteria(code)
        num = 0.0
        denom = 0.0
        for crit in applicable:
            if crit.id in scores:
                num += crit.weight * (scores[crit.id] / 5.0)
                denom += crit.weight
        return num / denom if denom > 0 else 0.0


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def aggregate_panel(
    rubric: Rubric, panel: list[dict[str, int]], code: str
) -> tuple[float, dict[str, float]]:
    """Median-per-criterion aggregation across a decorrelated judge panel (D6).

    ``panel`` is a list of {criterion_id: score} maps (one per judge). Returns the
    weighted composite in [0, 1] plus the per-criterion medians it was built from.
    """
    per_criterion: dict[str, float] = {}
    for crit in rubric.correctness_criteria(code):
        vals = [judge[crit.id] for judge in panel if crit.id in judge]
        if vals:
            per_criterion[crit.id] = _median([float(v) for v in vals])
    return rubric.weighted_score(per_criterion, code), per_criterion


def final_gate(
    rubric: Rubric, panel: list[dict[str, int]], code: str, threshold: float
) -> GateResult:
    """The final rubric gate: pass iff the weighted median composite >= threshold."""
    composite, per_criterion = aggregate_panel(rubric, panel, code)
    detail = ", ".join(f"{cid}={val:g}" for cid, val in sorted(per_criterion.items()))
    if composite >= threshold:
        return GatePass(
            gate=RUBRIC_GATE_NAME,
            findings=[
                GateFinding(
                    code="rubric_met",
                    message=f"weighted median {composite:.3f} >= {threshold:.3f}",
                    locator=detail or None,
                )
            ],
        )
    return GateFail(
        gate=RUBRIC_GATE_NAME,
        findings=[
            GateFinding(
                code="rubric_below_threshold",
                message=f"weighted median {composite:.3f} < {threshold:.3f}",
                locator=detail or None,
            )
        ],
    )


# --- loader with the lock check ---------------------------------------------


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_rubric(path: Path | str, *, lock_path: Path | str | None = None) -> Rubric:
    """Load + validate a rubric YAML, refusing a LOCKED file whose content hash has
    drifted from its recorded ``.sha256`` sidecar (plan D12)."""
    p = Path(path)
    if not p.is_file():
        raise TaskConfigError(f"no rubric file at {p}")
    text = p.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TaskConfigError(f"{p} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise TaskConfigError(f"{p} must be a mapping, got {type(raw).__name__}")
    rubric = Rubric.model_validate(raw)

    if rubric.locked:
        sidecar = Path(lock_path) if lock_path is not None else p.with_suffix(".sha256")
        if not sidecar.is_file():
            raise RubricLockError(
                f"{p} is locked but its hash file {sidecar} is missing — "
                f"a locked rubric must ship its recorded hash"
            )
        recorded = sidecar.read_text(encoding="utf-8").split()[0].strip()
        actual = sha256_hex(text)
        if recorded != actual:
            raise RubricLockError(
                f"{p} is LOCKED and has been modified in place "
                f"(recorded {recorded[:12]}…, actual {actual[:12]}…). "
                f"Changing a locked rubric requires a new version file."
            )
    return rubric
