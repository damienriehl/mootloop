"""Begin-task on-ramp service (plan FE-2.5 thin on-ramp): deterministic freeform
resolution + the append-only TaskSpec store.

The freeform lane maps an attorney's free-text intent to a registered task-adapter key
by a DETERMINISTIC keyword/registry match — no LLM in v1 (LLM concept-resolution lands
in FE-3). An intent that maps to nothing is still recorded, as a TaskSpec with
``task=None`` (not runnable), so every begin-task attempt leaves an audit trail.

Specs persist append-only at ``<vault>/tasks/specs.jsonl`` — the path is built only
through `safe_vault_path` (the realpath-containment choke-point), and appends are
fsync'd, mirroring the decision store.
"""

from __future__ import annotations

import os
import secrets as secrets_mod
from pathlib import Path

from mootloop.models.common import MatterId, TaskSpecId
from mootloop.models.taskspec import TaskSpec
from mootloop.tasks import registered_tasks
from mootloop.vault import safe_vault_path

SPECS_SUBPATH: tuple[str, ...] = ("tasks", "specs.jsonl")

# Deterministic keyword -> task-adapter key map (plan FE-2.5). Substring matches are
# intentional so "interrogatory"/"interrogatories"/"interrogator" all catch on
# ``interrogator``, and "requests for production" catches on the phrase.
_KEYWORD_TASK: dict[str, str] = {
    "discovery": "discovery-responses",
    "interrogator": "discovery-responses",
    "interrogatory": "discovery-responses",
    "rfp": "discovery-responses",
    "rfa": "discovery-responses",
    "request for production": "discovery-responses",
    "request for admission": "discovery-responses",
}


def resolve_freeform(intent_text: str) -> str | None:
    """Resolve free-text intent to a registered task key, or ``None`` if unmapped.

    Deterministic in v1: an exact registered-task-key mention wins first, then the
    keyword map. Returns ``None`` when nothing matches — the caller records a
    non-runnable TaskSpec. LLM concept-resolution lands in FE-3.
    """
    text = intent_text.casefold()
    for key in registered_tasks():
        if key.casefold() in text:
            return key
    for keyword, task in _KEYWORD_TASK.items():
        if keyword in text:
            return task
    return None


def _compact_ts(now: str) -> str:
    """The digits of an ISO timestamp — a sortable, path-safe id stem."""
    return "".join(ch for ch in now if ch.isdigit())


def make_task_spec_id(now: str) -> TaskSpecId:
    """A collision-resistant, path-safe TaskSpec id (time stem + short random)."""
    return TaskSpecId(f"taskspec-{_compact_ts(now)}-{secrets_mod.token_hex(3)}")


class TaskSpecStore:
    """Append-only JSONL TaskSpec store at ``tasks/specs.jsonl`` (matter-scoped)."""

    def __init__(self, vault_root: Path | str) -> None:
        self.vault_root = vault_root
        self._path = safe_vault_path(vault_root, *SPECS_SUBPATH)

    def list_all(self) -> list[TaskSpec]:
        if not self._path.is_file():
            return []
        specs: list[TaskSpec] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                specs.append(TaskSpec.model_validate_json(line))
        return specs

    def get(self, task_spec_id: str) -> TaskSpec | None:
        for spec in self.list_all():
            if spec.task_spec_id == task_spec_id:
                return spec
        return None

    def append(self, spec: TaskSpec) -> None:
        """Append one spec as an fsync'd line (append-only, single logical writer)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(spec.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def create_freeform(vault_root: Path | str, matter_id: str, intent_text: str, now: str) -> TaskSpec:
    """Resolve free-text intent and persist the resulting TaskSpec (resolved or not)."""
    task = resolve_freeform(intent_text)
    spec = TaskSpec(
        task_spec_id=make_task_spec_id(now),
        matter_id=MatterId(matter_id),
        task=task,
        source_lane="freeform",
        intent_text=intent_text,
        created_at=now,
    )
    TaskSpecStore(vault_root).append(spec)
    return spec


def list_specs(vault_root: Path | str) -> list[TaskSpec]:
    """Every recorded TaskSpec for the matter, in append order."""
    return TaskSpecStore(vault_root).list_all()
