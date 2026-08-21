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

import fcntl
import hashlib
import os
import secrets as secrets_mod
from pathlib import Path

from pydantic import ValidationError

from mootloop.errors import TaskSpecError
from mootloop.models.common import MatterId, TaskSpecId
from mootloop.models.taskspec import (
    TaskSpec,
    TaskSpecLock,
    canonical_sha256,
    task_spec_sha256,
)
from mootloop.resources import rubric_path, task_config_path
from mootloop.tasks import TaskBinding, get_binding, registered_tasks
from mootloop.vault import fsync_file_and_parent, load_matter, safe_vault_path

SPECS_SUBPATH: tuple[str, ...] = ("tasks", "specs.jsonl")
LOCKS_SUBPATH: tuple[str, ...] = ("tasks", "locks.jsonl")
LOCK_MUTEX_SUBPATH: tuple[str, ...] = ("tasks", ".locks.write.lock")

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
        seen: set[str] = set()
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                spec = TaskSpec.model_validate_json(line)
                key = str(spec.task_spec_id)
                if key in seen:
                    raise TaskSpecError(
                        f"TaskSpec store integrity failure: duplicate id {key!r}"
                    )
                seen.add(key)
                specs.append(spec)
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


class TaskSpecLockStore:
    """Append-only human TaskSpec approvals at ``tasks/locks.jsonl``."""

    def __init__(self, vault_root: Path | str) -> None:
        self.vault_root = vault_root
        self._path = safe_vault_path(vault_root, *LOCKS_SUBPATH)

    def list_all(self) -> list[TaskSpecLock]:
        if not self._path.is_file():
            return []
        records: list[TaskSpecLock] = []
        versions: dict[str, int] = {}
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if not line.strip():
                    continue
                record = TaskSpecLock.model_validate_json(line)
                key = str(record.task_spec_id)
                expected = versions.get(key, 0) + 1
                if record.lock_version != expected:
                    raise TaskSpecError(
                        f"TaskSpec lock store integrity failure for {key!r}: "
                        f"expected lock version {expected}, found {record.lock_version}"
                    )
                versions[key] = record.lock_version
                records.append(record)
        except (OSError, UnicodeError, ValidationError) as exc:
            raise TaskSpecError(f"TaskSpec lock store failed integrity validation: {exc}") from exc
        return records

    def latest(self, task_spec_id: str) -> TaskSpecLock | None:
        records = [
            record
            for record in self.list_all()
            if str(record.task_spec_id) == task_spec_id
        ]
        return records[-1] if records else None

    def append(self, record: TaskSpecLock) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
        fsync_file_and_parent(self._path)

    def reassert_durability(self) -> None:
        if self._path.is_file():
            fsync_file_and_parent(self._path)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _lock_material(
    task: str,
) -> tuple[bytes, bytes, bytes, str, str, str, TaskBinding]:
    """Load and validate exact adapter, rubric, and rubric-lock bytes."""
    binding = get_binding(task)
    adapter_file = task_config_path(task)
    rubric_file = rubric_path(binding.config.rubric_id)
    lock_file = rubric_file.with_suffix(".sha256")
    if not binding.rubric.locked:
        raise TaskSpecError(
            f"TaskSpec task {task!r} uses rubric {binding.rubric.rubric_id!r} "
            "that is not locked"
        )
    try:
        first = (
            adapter_file.read_bytes(),
            rubric_file.read_bytes(),
            lock_file.read_bytes(),
        )
        recorded = first[2].decode("utf-8").split()[0].strip()
        confirmed = get_binding(task)
        second = (
            adapter_file.read_bytes(),
            rubric_file.read_bytes(),
            lock_file.read_bytes(),
        )
    except (OSError, UnicodeError, IndexError) as exc:
        raise TaskSpecError(f"TaskSpec lock inputs could not be read: {exc}") from exc
    if first != second or confirmed.config != binding.config or confirmed.rubric != binding.rubric:
        raise TaskSpecError("TaskSpec lock inputs changed while approval was captured; retry")
    adapter_raw, rubric_raw, rubric_lock_raw = second
    if recorded != _sha256(rubric_raw):
        raise TaskSpecError("locked rubric sidecar does not match the exact rubric YAML")
    return (
        adapter_raw,
        rubric_raw,
        rubric_lock_raw,
        f"config/tasks/{adapter_file.name}",
        f"rubrics/{rubric_file.name}",
        f"rubrics/{lock_file.name}",
        binding,
    )


def _same_approval(
    record: TaskSpecLock,
    *,
    spec_digest: str,
    adapter_digest: str,
    rubric_digest: str,
    rubric_lock_digest: str,
    locked_by: str,
) -> bool:
    return (
        record.task_spec_sha256 == spec_digest
        and record.adapter_sha256 == adapter_digest
        and record.rubric_sha256 == rubric_digest
        and record.rubric_lock_sha256 == rubric_lock_digest
        and record.locked_by == locked_by
        and record.source == "human"
    )


def lock_task_spec(
    vault_root: Path | str,
    matter_id: str,
    task_spec_id: str,
    locked_by: str,
    locked_at: str,
) -> TaskSpecLock:
    """Append a human lock for the exact current TaskSpec launch inputs.

    ``locked_by`` is a trusted service input. Public CLI/API adapters derive it from
    the local OS or authenticated Access principal and never accept a caller field.
    """
    if not locked_by.strip():
        raise TaskSpecError("TaskSpec lock requires an identified human actor")
    mutex = safe_vault_path(vault_root, *LOCK_MUTEX_SUBPATH)
    mutex.parent.mkdir(parents=True, exist_ok=True)
    with mutex.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            matter = load_matter(vault_root)
            if str(matter.matter_id) != matter_id:
                raise TaskSpecError(
                    f"TaskSpec lock matter identity {matter_id!r} does not match vault matter "
                    f"{matter.matter_id!r}"
                )
            spec_store = TaskSpecStore(vault_root)
            spec = spec_store.get(task_spec_id)
            if spec is None:
                raise TaskSpecError(f"TaskSpec {task_spec_id!r} was not found")
            if str(spec.matter_id) != matter_id:
                raise TaskSpecError(
                    f"TaskSpec {task_spec_id!r} matter identity {spec.matter_id!r} does not "
                    f"match {matter_id!r}"
                )
            if spec.task is None:
                raise TaskSpecError(
                    f"TaskSpec {task_spec_id!r} is not resolved and cannot be locked"
                )
            (
                adapter_raw,
                rubric_raw,
                lock_raw,
                adapter_locator,
                rubric_locator,
                lock_locator,
                binding,
            ) = _lock_material(spec.task)
            confirmed_spec = spec_store.get(task_spec_id)
            if confirmed_spec != spec:
                raise TaskSpecError(
                    "TaskSpec lock inputs changed while approval was captured; retry"
                )
            spec_digest = task_spec_sha256(spec)
            adapter_digest = _sha256(adapter_raw)
            rubric_digest = _sha256(rubric_raw)
            lock_digest = _sha256(lock_raw)
            store = TaskSpecLockStore(vault_root)
            latest = store.latest(task_spec_id)
            if latest is not None and _same_approval(
                latest,
                spec_digest=spec_digest,
                adapter_digest=adapter_digest,
                rubric_digest=rubric_digest,
                rubric_lock_digest=lock_digest,
                locked_by=locked_by,
            ):
                store.reassert_durability()
                return latest
            version = 1 if latest is None else latest.lock_version + 1
            payload: dict[str, object] = {
                "schema_version": "1.0",
                "task_spec_lock_id": f"taskspeclock-{task_spec_id}-v{version}",
                "lock_version": version,
                "task_spec_id": task_spec_id,
                "matter_id": matter_id,
                "task": spec.task,
                "task_spec_sha256": spec_digest,
                "adapter_locator": adapter_locator,
                "adapter_sha256": adapter_digest,
                "rubric_id": str(binding.rubric.rubric_id),
                "rubric_locator": rubric_locator,
                "rubric_sha256": rubric_digest,
                "rubric_lock_locator": lock_locator,
                "rubric_lock_sha256": lock_digest,
                "rubric_recorded_sha256": rubric_digest,
                "locked_by": locked_by,
                "source": "human",
                "locked_at": locked_at,
            }
            payload["record_sha256"] = canonical_sha256(payload)
            record = TaskSpecLock.model_validate(payload)
            store.append(record)
            return record
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def require_current_lock(
    vault_root: Path | str,
    matter_id: str,
    spec: TaskSpec,
    *,
    adapter_raw: bytes,
    rubric_raw: bytes,
    rubric_lock_raw: bytes,
) -> TaskSpecLock:
    """Return the latest exact human lock, or fail with an actionable re-lock error."""
    try:
        record = TaskSpecLockStore(vault_root).latest(str(spec.task_spec_id))
    except TaskSpecError:
        raise
    if record is None:
        raise TaskSpecError(
            f"TaskSpec {spec.task_spec_id!r} has no human lock; review and re-lock it"
        )
    if str(record.matter_id) != matter_id or record.task != spec.task:
        raise TaskSpecError(
            f"TaskSpec {spec.task_spec_id!r} lock has the wrong matter/task identity; re-lock it"
        )
    checks = (
        (record.task_spec_sha256, task_spec_sha256(spec), "TaskSpec source"),
        (record.adapter_sha256, _sha256(adapter_raw), "adapter source"),
        (record.rubric_sha256, _sha256(rubric_raw), "rubric source"),
        (record.rubric_lock_sha256, _sha256(rubric_lock_raw), "rubric lock source"),
    )
    for recorded, current, label in checks:
        if recorded != current:
            raise TaskSpecError(
                f"TaskSpec {spec.task_spec_id!r} {label} changed after human approval; re-lock it"
            )
    return record


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
