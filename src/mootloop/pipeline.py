"""Compile matter persona choices and an adapter strategy into one exact graph."""

from __future__ import annotations

from mootloop.errors import PipelineConfigError
from mootloop.models.common import RubricId
from mootloop.models.config import ResolvedRunConfig
from mootloop.models.matter import MatterConfig
from mootloop.models.pipeline import (
    SELECTABLE_PIPELINE_PERSONAS,
    PipelineSource,
    ResolvedPipeline,
    pipeline_graph_error,
    pipeline_turn_ceiling,
)
from mootloop.models.run import PersonaName
from mootloop.models.task import TaskAdapterConfig

ACTIVE_PIPELINE_PERSONAS = SELECTABLE_PIPELINE_PERSONAS


def _selected_personas(matter: MatterConfig) -> tuple[PersonaName, ...]:
    selected = matter.personas
    enabled = {
        PersonaName.ASSOCIATE: selected.associate,
        PersonaName.PARTNER: selected.partner,
        PersonaName.OC_ASSOCIATE: selected.oc_associate,
        PersonaName.OC_PARTNER: selected.oc_partner,
        PersonaName.JUDGE: selected.judge,
        PersonaName.RUBRIC_JUDGE: selected.rubric_judge,
    }
    return tuple(persona for persona in ACTIVE_PIPELINE_PERSONAS if enabled[persona])


def _without(stages: list[str], *removed: str) -> list[str]:
    blocked = set(removed)
    return [stage for stage in stages if stage not in blocked]


def legacy_fixed_pipeline(
    adapter: TaskAdapterConfig,
    *,
    matter_locator: str,
    matter_sha256: str,
    adapter_locator: str,
    adapter_sha256: str,
) -> ResolvedPipeline:
    """Describe the fixed graph used before persona/strategy selection existed."""
    stages = set(adapter.stages)
    active_set: set[PersonaName] = set()
    if stages & {"associate_draft", "bolster", "restructure"}:
        active_set.add(PersonaName.ASSOCIATE)
    if "partner_loop" in stages:
        active_set.add(PersonaName.PARTNER)
    if "oc_attack" in stages:
        active_set.add(PersonaName.OC_ASSOCIATE)
    if "judge_panel" in stages:
        active_set.add(PersonaName.JUDGE)
    if stages & {"partner_loop", "rubric_gate"}:
        active_set.add(PersonaName.RUBRIC_JUDGE)
    active = tuple(persona for persona in ACTIVE_PIPELINE_PERSONAS if persona in active_set)
    bypassed = tuple(persona for persona in ACTIVE_PIPELINE_PERSONAS if persona not in active_set)
    return ResolvedPipeline(
        strategy="thin-full",
        effective_config=adapter,
        drafting_persona=(
            PersonaName.ASSOCIATE
            if PersonaName.ASSOCIATE in active
            else PersonaName.PARTNER
        ),
        oc_personas=(PersonaName.OC_ASSOCIATE,) if "oc_attack" in stages else (),
        active_personas=active,
        bypassed_personas=bypassed,
        max_turns_per_request=pipeline_turn_ceiling(
            adapter,
            rubric_enabled=PersonaName.RUBRIC_JUDGE in active,
        ),
        sources=(
            PipelineSource(
                kind="matter_config",
                locator=matter_locator,
                sha256=matter_sha256,
            ),
            PipelineSource(
                kind="task_adapter",
                locator=adapter_locator,
                sha256=adapter_sha256,
            ),
        ),
    )


def compile_pipeline(
    adapter: TaskAdapterConfig,
    matter: MatterConfig,
    *,
    matter_sha256: str,
    adapter_sha256: str,
    resolved_config: ResolvedRunConfig | None = None,
) -> ResolvedPipeline:
    """Return the sole stage/owner contract runtime code may execute."""
    active = _selected_personas(matter)
    bypassed = tuple(persona for persona in ACTIVE_PIPELINE_PERSONAS if persona not in active)
    if not ({PersonaName.ASSOCIATE, PersonaName.PARTNER} & set(active)):
        raise PipelineConfigError("pipeline has no drafting owner; enable associate or partner")
    if not adapter.pipeline_strategies:
        raise PipelineConfigError(
            f"task adapter {adapter.task!r} does not define selectable pipeline strategies"
        )
    strategy = matter.pipeline_strategy
    if strategy == "deep-core" and not {
        PersonaName.ASSOCIATE,
        PersonaName.PARTNER,
    }.issubset(active):
        raise PipelineConfigError("deep-core requires the associate and partner personas")
    oc_personas = tuple(
        persona
        for persona in (PersonaName.OC_ASSOCIATE, PersonaName.OC_PARTNER)
        if persona in active
    )
    if strategy == "adversarial-first" and not oc_personas:
        raise PipelineConfigError("adversarial-first requires opposing counsel")

    selected = adapter.pipeline_strategies[strategy]
    graph_error = pipeline_graph_error(selected.stages)
    if graph_error is not None:
        raise PipelineConfigError(f"{strategy} {graph_error}")
    required = {"associate_draft"}
    if PersonaName.PARTNER in active:
        required.add("partner_loop")
    if oc_personas:
        required.update({"oc_attack", "bolster"})
    if PersonaName.JUDGE in active:
        required.update({"judge_panel", "restructure"})
    if PersonaName.RUBRIC_JUDGE in active:
        required.add("rubric_gate")
    missing = required - set(selected.stages)
    if missing:
        raise PipelineConfigError(
            f"{strategy} pipeline leaves active persona work unowned: "
            f"{', '.join(sorted(missing))}"
        )
    effective = adapter.model_copy(deep=True)
    if resolved_config is not None:
        effective.loop_caps = effective.loop_caps.model_validate(
            resolved_config.loop_caps.model_dump()
        )
        effective.panels = effective.panels.model_validate(resolved_config.panels.model_dump())
        effective.convergence = effective.convergence.model_validate(
            resolved_config.convergence.model_dump()
        )
        effective.gates = list(resolved_config.gates)
        effective.rubric_id = RubricId(resolved_config.rubric_id)
        effective.rubric_threshold = resolved_config.rubric_threshold
        effective.restructure_threshold = resolved_config.restructure_threshold
        effective.deliverables = list(resolved_config.deliverables)
    effective.stages = list(selected.stages)
    effective.loop_caps.associate_partner = max(
        effective.loop_caps.associate_partner,
        selected.associate_partner_cap,
    )

    if PersonaName.PARTNER not in active:
        effective.stages = _without(effective.stages, "partner_loop")
    if oc_personas:
        effective.loop_caps.oc = max(effective.loop_caps.oc, len(oc_personas))
    else:
        effective.stages = _without(effective.stages, "oc_attack", "bolster")
        effective.loop_caps.oc = 0
        effective.loop_caps.bolster = 0
    if PersonaName.JUDGE not in active:
        effective.stages = _without(effective.stages, "judge_panel", "restructure")
        effective.loop_caps.restructure = 0
    if PersonaName.RUBRIC_JUDGE not in active:
        effective.stages = _without(effective.stages, "rubric_gate")
        effective.gates = [gate for gate in effective.gates if gate != "rubric"]

    drafting_persona = (
        PersonaName.ASSOCIATE
        if PersonaName.ASSOCIATE in active
        else PersonaName.PARTNER
    )
    return ResolvedPipeline(
        strategy=strategy,
        effective_config=effective,
        drafting_persona=drafting_persona,
        oc_personas=oc_personas,
        active_personas=active,
        bypassed_personas=bypassed,
        max_turns_per_request=pipeline_turn_ceiling(
            effective,
            rubric_enabled=PersonaName.RUBRIC_JUDGE in active,
        ),
        sources=(
            PipelineSource(
                kind="matter_config",
                locator="matter.yaml#personas,pipeline_strategy",
                sha256=matter_sha256,
            ),
            PipelineSource(
                kind="task_adapter",
                locator=f"config/tasks/{adapter.task}.yaml#pipeline_strategies",
                sha256=adapter_sha256,
            ),
        ),
    )
