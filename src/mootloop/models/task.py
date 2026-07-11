"""`TaskAdapterConfig` — the declarative half of a task adapter (plan D1).

The YAML declares pipeline shape (stages), per-request loop caps, panel counts, the
gate list, the locked rubric id, and the deliverable set. The *behavior* half (how a
Judge question is framed, etc.) is a registered Python class (see `mootloop.tasks`).
The core orchestrator depends on this config + the adapter protocol — never on a
task name literal.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field, ValidationError

from mootloop.errors import TaskConfigError
from mootloop.models.common import StrictModel, VersionedModel

SCHEMA_VERSION = "1.0"


class LoopCaps(StrictModel):
    """Per-request loop caps (plan D6 — keep caps low)."""

    associate_partner: int = Field(default=2, ge=1)
    oc: int = Field(default=1, ge=0)
    bolster: int = Field(default=1, ge=0)
    # Costed associate restructure turns after the judge panel, when an objection's
    # survival rate falls below ``restructure_threshold`` (plan Phase 6).
    restructure: int = Field(default=1, ge=0)


class PanelConfig(StrictModel):
    """Panel sizing for the thin pipeline."""

    judges: int = Field(default=3, ge=1)
    jury: bool = False
    # Decorrelated rubric-scoring panel at the final gate (plan D6 — stays at N≈3
    # regardless of tier; reliability plateaus for correlated judges).
    rubric_judges: int = Field(default=3, ge=1)


class ConvergenceConfig(StrictModel):
    """Loop-termination floors (plan D6). A partner loop converges only when the
    draft *stopped improving* AND *stopped changing* AND is *complete* — or the cap
    is hit. Each floor is user-configurable per task."""

    score_delta_floor: float = Field(default=0.02, ge=0.0)
    material_change_floor: float = Field(default=0.10, ge=0.0)
    coverage_floor: float = Field(default=0.80, ge=0.0, le=1.0)


class TaskAdapterConfig(VersionedModel):
    """The declarative task adapter loaded from ``config/tasks/<task>.yaml``."""

    schema_version: str = SCHEMA_VERSION
    task: str
    stages: list[str]
    loop_caps: LoopCaps = Field(default_factory=LoopCaps)
    panels: PanelConfig = Field(default_factory=PanelConfig)
    convergence: ConvergenceConfig = Field(default_factory=ConvergenceConfig)
    gates: list[str] = Field(default_factory=list)
    rubric_id: str
    rubric_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    # An objection surviving fewer than this fraction of the panel triggers a
    # restructure pass on its request (plan Phase 6).
    restructure_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    deliverables: list[str] = Field(default_factory=list)


def load_task_config(path: Path | str) -> TaskAdapterConfig:
    """Load + validate a task-adapter YAML, naming each bad field on failure."""
    p = Path(path)
    if not p.is_file():
        raise TaskConfigError(f"no task config at {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TaskConfigError(f"{p} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise TaskConfigError(f"{p} must be a mapping, got {type(raw).__name__}")
    try:
        return TaskAdapterConfig.model_validate(raw)
    except ValidationError as exc:
        raise TaskConfigError(f"{p} failed validation: {exc}") from exc
