"""Typed inputs and immutable output for five-layer run configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from mootloop.models.common import StrictModel, VersionedModel

SCHEMA_VERSION = "1.0"

BudgetTier = Literal["no-budget", "moderate", "low"]
RunMode = Literal["autonomous", "gated", "observed"]
PipelineStrategy = Literal["thin-full", "deep-core", "adversarial-first"]

ConfigLayer = Literal[
    "defaults",
    "task_adapter",
    "firm_preferences",
    "matter_overlay",
    "invocation_flags",
]

# These are the adapter-owned task-shape leaves. Containers such as ``panels`` are
# intentionally absent: the allowlist is exact, never recursive or wildcard-based.
STRUCTURAL_LEAF_PATHS: frozenset[str] = frozenset(
    {
        "stages",
        "panels.judges",
        "panels.jury",
        "panels.jurors",
        "panels.rubric_judges",
        "gates",
        "deliverables",
    }
)
IMMUTABLE_IDENTITY_PATHS: frozenset[str] = frozenset(
    {"schema_version", "task", "rubric_id"}
)


class LoopCapsOverlay(StrictModel):
    associate_partner: int | None = Field(default=None, ge=1)
    oc: int | None = Field(default=None, ge=0)
    bolster: int | None = Field(default=None, ge=0)
    restructure: int | None = Field(default=None, ge=0)


class PanelOverlay(StrictModel):
    judges: int | None = Field(default=None, ge=1)
    jury: bool | None = None
    jurors: int | None = Field(default=None, ge=0)
    rubric_judges: int | None = Field(default=None, ge=1)


class ConvergenceOverlay(StrictModel):
    score_delta_floor: float | None = Field(default=None, ge=0.0)
    material_change_floor: float | None = Field(default=None, ge=0.0)
    coverage_floor: float | None = Field(default=None, ge=0.0, le=1.0)


class BudgetOverlay(StrictModel):
    tier: BudgetTier | None = None
    hard_cap_usd: float | None = Field(default=None, ge=0.0)


class RunConfigOverlay(StrictModel):
    """A partial runtime overlay accepted from firm, matter, or invocation layers."""

    stages: tuple[str, ...] | None = None
    loop_caps: LoopCapsOverlay | None = None
    panels: PanelOverlay | None = None
    convergence: ConvergenceOverlay | None = None
    gates: tuple[str, ...] | None = None
    rubric_id: str | None = None
    rubric_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    restructure_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    deliverables: tuple[str, ...] | None = None
    run_mode: RunMode | None = None
    max_attempts: int | None = Field(default=None, ge=1)
    budget: BudgetOverlay | None = None


class FirmPreferences(VersionedModel):
    """Versioned firm-level preferences loaded from an injected external path."""

    schema_version: str = SCHEMA_VERSION
    run_config: RunConfigOverlay = Field(default_factory=RunConfigOverlay)


class _FrozenModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResolvedLoopCaps(_FrozenModel):
    associate_partner: int = Field(ge=1)
    oc: int = Field(ge=0)
    bolster: int = Field(ge=0)
    restructure: int = Field(ge=0)


class ResolvedPanels(_FrozenModel):
    judges: int = Field(ge=1)
    jury: bool
    jurors: int = Field(default=0, ge=0)
    rubric_judges: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_jury_size(self) -> ResolvedPanels:
        if self.jury and self.jurors < 1:
            raise ValueError("panels.jurors must be at least 1 when jury is enabled")
        return self


class ResolvedConvergence(_FrozenModel):
    score_delta_floor: float = Field(ge=0.0)
    material_change_floor: float = Field(ge=0.0)
    coverage_floor: float = Field(ge=0.0, le=1.0)


class ResolvedBudget(_FrozenModel):
    tier: BudgetTier
    hard_cap_usd: float | None = Field(default=None, ge=0.0)


class DefaultRunConfig(VersionedModel):
    """Repo-authored defaults; adapter identity and task shape arrive in layer two."""

    schema_version: str = SCHEMA_VERSION
    run_mode: RunMode
    max_attempts: int = Field(ge=1)
    budget: ResolvedBudget
    loop_caps: ResolvedLoopCaps
    panels: ResolvedPanels
    convergence: ResolvedConvergence
    rubric_threshold: float = Field(ge=0.0, le=1.0)
    restructure_threshold: float = Field(ge=0.0, le=1.0)


class ConfigSource(_FrozenModel):
    layer: ConfigLayer
    locator: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    present: bool


class ResolvedRunConfig(VersionedModel):
    """The only run configuration downstream code should eventually consume."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SCHEMA_VERSION
    task: str
    stages: tuple[str, ...] = Field(min_length=1)
    loop_caps: ResolvedLoopCaps
    panels: ResolvedPanels
    convergence: ResolvedConvergence
    gates: tuple[str, ...]
    rubric_id: str
    rubric_threshold: float = Field(ge=0.0, le=1.0)
    restructure_threshold: float = Field(ge=0.0, le=1.0)
    deliverables: tuple[str, ...]
    run_mode: RunMode
    max_attempts: int = Field(ge=1)
    budget: ResolvedBudget
    overridable_structural_paths: tuple[str, ...]
    sources: tuple[ConfigSource, ...] = Field(min_length=5, max_length=5)
