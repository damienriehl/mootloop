"""Immutable persona ownership and stage graph selected for one run."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from mootloop.models.common import StrictModel, VersionedModel
from mootloop.models.config import PipelineStrategy
from mootloop.models.run import PersonaName
from mootloop.models.task import TaskAdapterConfig

SCHEMA_VERSION = "1.0"
PipelineSourceKind = Literal["matter_config", "task_adapter"]
SELECTABLE_PIPELINE_PERSONAS: tuple[PersonaName, ...] = (
    PersonaName.ASSOCIATE,
    PersonaName.PARTNER,
    PersonaName.OC_ASSOCIATE,
    PersonaName.OC_PARTNER,
    PersonaName.JUDGE,
    PersonaName.RUBRIC_JUDGE,
)
AUTHORED_AUXILIARY_PERSONAS: tuple[PersonaName, ...] = (
    PersonaName.JUROR,
    PersonaName.CITE_CHECKER,
)
PIPELINE_STAGE_NAMES = frozenset(
    {
        "associate_draft",
        "partner_loop",
        "oc_attack",
        "bolster",
        "judge_panel",
        "restructure",
        "jury_panel",
        "rubric_gate",
        "assemble",
    }
)


def pipeline_graph_error(stages: tuple[str, ...] | list[str]) -> str | None:
    if len(set(stages)) != len(stages):
        return "pipeline contains duplicate stages"
    unknown = set(stages) - PIPELINE_STAGE_NAMES
    if unknown:
        return f"pipeline contains unknown stages: {', '.join(sorted(unknown))}"
    if not stages or stages[-1] != "assemble":
        return "pipeline must end with assemble"
    return None


def pipeline_turn_ceiling(config: TaskAdapterConfig, *, rubric_enabled: bool) -> int:
    """Maximum provider calls per request for an exact effective graph."""
    stages = set(config.stages)
    ap = config.loop_caps.associate_partner
    partner_calls = (
        2 * ap + (ap if rubric_enabled else 0) if "partner_loop" in stages else 1
    )
    return (
        partner_calls
        + (config.loop_caps.oc if "oc_attack" in stages else 0)
        + (config.loop_caps.bolster if "bolster" in stages else 0)
        + (config.panels.judges if "judge_panel" in stages else 0)
        + (config.loop_caps.restructure if "restructure" in stages else 0)
        + (
            config.panels.jurors
            if "jury_panel" in stages and config.panels.jury
            else 0
        )
        + (config.panels.rubric_judges if "rubric_gate" in stages else 0)
    )


class PipelineSource(StrictModel):
    kind: PipelineSourceKind
    locator: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResolvedPipeline(VersionedModel):
    """Exact graph, owners, cost ceiling, and provenance committed at launch."""

    schema_version: str = SCHEMA_VERSION
    strategy: PipelineStrategy
    effective_config: TaskAdapterConfig
    drafting_persona: PersonaName
    oc_personas: tuple[PersonaName, ...]
    active_personas: tuple[PersonaName, ...]
    bypassed_personas: tuple[PersonaName, ...]
    max_turns_per_request: int = Field(ge=1)
    sources: tuple[PipelineSource, PipelineSource] = Field(min_length=2, max_length=2)

    @property
    def rubric_judge_enabled(self) -> bool:
        return PersonaName.RUBRIC_JUDGE in self.active_personas

    @model_validator(mode="after")
    def validate_ownership(self) -> ResolvedPipeline:
        selectable = set(SELECTABLE_PIPELINE_PERSONAS)
        if self.drafting_persona not in self.active_personas:
            raise ValueError("drafting_persona must be active")
        if any(persona not in self.active_personas for persona in self.oc_personas):
            raise ValueError("every oc_persona must be active")
        expected_oc = tuple(
            persona
            for persona in (PersonaName.OC_ASSOCIATE, PersonaName.OC_PARTNER)
            if persona in self.active_personas
        )
        if self.oc_personas != expected_oc:
            raise ValueError("oc_personas must include every active opponent in canonical order")
        if set(self.active_personas) & set(self.bypassed_personas):
            raise ValueError("active and bypassed personas must be disjoint")
        if set(self.active_personas) | set(self.bypassed_personas) != selectable:
            raise ValueError("active and bypassed personas must partition selectable personas")
        if self.active_personas != tuple(
            persona for persona in SELECTABLE_PIPELINE_PERSONAS if persona in self.active_personas
        ):
            raise ValueError("active_personas must use canonical order")
        if self.bypassed_personas != tuple(
            persona for persona in SELECTABLE_PIPELINE_PERSONAS if persona in self.bypassed_personas
        ):
            raise ValueError("bypassed_personas must use canonical order")
        stages = set(self.effective_config.stages)
        graph_error = pipeline_graph_error(self.effective_config.stages)
        if graph_error is not None:
            raise ValueError(graph_error)
        if "associate_draft" not in stages:
            raise ValueError("pipeline requires an initial drafting stage")
        if PersonaName.PARTNER in self.active_personas and "partner_loop" not in stages:
            raise ValueError("active partner has no owned partner_loop stage")
        if bool(self.oc_personas) != ("oc_attack" in stages):
            raise ValueError("opposing-counsel ownership does not match oc_attack stage")
        if (PersonaName.JUDGE in self.active_personas) != ("judge_panel" in stages):
            raise ValueError("judge ownership does not match judge_panel stage")
        rubric_stage = "rubric_gate" in stages
        rubric_gate = "rubric" in self.effective_config.gates
        if self.rubric_judge_enabled != rubric_stage or rubric_stage != rubric_gate:
            raise ValueError("rubric-judge ownership, stage, and gate must match")
        if self.strategy == "deep-core":
            core = {PersonaName.ASSOCIATE, PersonaName.PARTNER}
            if not core.issubset(self.active_personas):
                raise ValueError("deep-core requires associate and partner")
            if self.effective_config.loop_caps.associate_partner < 3:
                raise ValueError("deep-core requires at least three core rounds")
            ordered = self.effective_config.stages
            external = [
                ordered.index(stage)
                for stage in ("oc_attack", "judge_panel", "rubric_gate")
                if stage in stages
            ]
            if external and ordered.index("partner_loop") > min(external):
                raise ValueError("deep-core must complete partner review before external stages")
        if self.strategy == "adversarial-first":
            if not self.oc_personas:
                raise ValueError("adversarial-first requires opposing counsel")
            ordered = self.effective_config.stages
            if "partner_loop" in stages and ordered.index("oc_attack") > ordered.index(
                "partner_loop"
            ):
                raise ValueError("adversarial-first must attack before partner review")
        expected_ceiling = pipeline_turn_ceiling(
            self.effective_config,
            rubric_enabled=self.rubric_judge_enabled,
        )
        if self.max_turns_per_request != expected_ceiling:
            raise ValueError("max_turns_per_request does not match the effective graph")
        if [source.kind for source in self.sources] != ["matter_config", "task_adapter"]:
            raise ValueError("pipeline sources must name matter then task adapter")
        return self
