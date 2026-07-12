"""Task-adapter registry (plan D1): task name -> (declarative config, behavior class).

The behavior class carries per-task *framing* — the task-specific instruction
snippets stages inject into persona prompts (how the drafting task is described, how
the Judge question is posed). The core orchestrator resolves an adapter by the task
name it was *given*; it never hard-codes a task name (invariant test enforces this).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from mootloop.errors import TaskConfigError
from mootloop.models.rubric import Rubric, load_rubric
from mootloop.models.task import TaskAdapterConfig, load_task_config
from mootloop.resources import rubric_path, task_config_path


class TaskAdapter(Protocol):
    """Per-task behavior seam. Framing only — no pipeline mechanics."""

    task: str

    def draft_directive(self) -> str:
        """Task-specific instruction injected into a drafting turn."""
        ...

    def judge_question(self) -> str:
        """Task-specific framing for a judge-panel turn."""
        ...


class DiscoveryResponsesAdapter:
    """Behavior for served-discovery responses (Rule 33/34/36; plan D7)."""

    task = "discovery-responses"

    def draft_directive(self) -> str:
        return (
            "Draft a response to the served request below. Answer or object per the "
            "governing discovery rule; state each objection's basis with particularity; "
            "ground every factual assertion in a listed fact_id or raise it as an "
            "attorney_gate_item. Do not invent facts or law."
        )

    def judge_question(self) -> str:
        return (
            "For each objection in the draft, rule whether it would survive a motion to "
            "compel, with reasoning and persuasion notes."
        )


@dataclass(frozen=True)
class TaskBinding:
    """A resolved task: its declarative config, its behavior adapter, and the LOCKED
    rubric its config pins (loaded once, hash-checked at bind time)."""

    config: TaskAdapterConfig
    adapter: TaskAdapter
    rubric: Rubric


# task name -> adapter factory. Add a task by registering here + shipping its YAML.
_REGISTRY: dict[str, Callable[[], TaskAdapter]] = {
    DiscoveryResponsesAdapter.task: DiscoveryResponsesAdapter,
}


def registered_tasks() -> tuple[str, ...]:
    """The registered task-adapter keys, sorted. The one place the on-ramp / freeform
    resolver reads the catalog of runnable tasks (never hard-codes a task name)."""
    return tuple(sorted(_REGISTRY))


def get_binding(task: str) -> TaskBinding:
    """Resolve a task name to its config + adapter (raises on an unknown task)."""
    factory = _REGISTRY.get(task)
    if factory is None:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise TaskConfigError(f"unknown task {task!r}; registered tasks: {known}")
    config = load_task_config(task_config_path(task))
    rubric = load_rubric(rubric_path(config.rubric_id))
    return TaskBinding(config=config, adapter=factory(), rubric=rubric)
