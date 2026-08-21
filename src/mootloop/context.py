"""Write-once run-context capture and exact replay."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from mootloop import budget
from mootloop.errors import MigrationError, OrchestratorError
from mootloop.facts import FACTS_PATH
from mootloop.facts import fold as fold_facts
from mootloop.migrations import load_versioned_json
from mootloop.models.common import MatterId, RunId
from mootloop.models.context import (
    SCHEMA_VERSION as RUN_CONTEXT_SCHEMA_VERSION,
)
from mootloop.models.context import (
    AdapterBehavior,
    ContextSource,
    ContextSourceKind,
    CorpusSnapshot,
    CorpusTextSnapshot,
    RunContextManifest,
)
from mootloop.models.corpus import MANIFEST_PATH, Manifest
from mootloop.models.events import RunMode, RunStarted
from mootloop.models.facts import Fact
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestItem, RequestSet
from mootloop.models.rubric import Rubric, sha256_hex
from mootloop.models.task import TaskAdapterConfig
from mootloop.models.taskspec import TaskSpec
from mootloop.resources import load_persona_bodies, rubric_path, task_config_path
from mootloop.tasks import TaskBinding
from mootloop.taskspec import TaskSpecStore
from mootloop.vault import atomic_write_once_text, fsync_file_and_parent, safe_vault_path

MANIFEST_SUBPATH = ("context", "manifest.json")
CORPUS_SNAPSHOT_SUBPATH = ("context", "corpus.json")
MAX_CORPUS_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_RETAINED_CORPUS_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class FrozenTaskAdapter:
    """Task adapter reconstructed only from launch-snapshotted behavior."""

    task: str
    _draft_directive: str
    _judge_question: str

    def draft_directive(self) -> str:
        return self._draft_directive

    def judge_question(self) -> str:
        return self._judge_question


@dataclass(frozen=True)
class RunContext:
    manifest: RunContextManifest
    binding: TaskBinding
    units: list[RequestItem]
    facts: list[dict[str, str]]
    corpus_snapshot: CorpusSnapshot | None = None


def context_manifest_path(vault_root: Path | str, run_id: str) -> Path:
    return safe_vault_path(vault_root, "runs", run_id, *MANIFEST_SUBPATH)


def corpus_snapshot_path(vault_root: Path | str, run_id: str) -> Path:
    return safe_vault_path(vault_root, "runs", run_id, *CORPUS_SNAPSHOT_SUBPATH)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config_digest(config: TaskAdapterConfig) -> str:
    """The compact digest historically recorded on ``RunStarted``."""
    return _sha256(config.model_dump_json().encode("utf-8"))[:16]


def _corpus_payload(snapshot: CorpusSnapshot) -> str:
    payload = snapshot.model_dump_json(indent=2) + "\n"
    if len(payload.encode("utf-8")) > MAX_CORPUS_SNAPSHOT_BYTES:
        raise OrchestratorError(
            "corpus snapshot exceeds the 256 MiB launch limit; reduce or split the matter corpus"
        )
    return payload


def _validate_corpus_snapshot(
    manifest: RunContextManifest, snapshot: CorpusSnapshot
) -> None:
    expected = [
        (str(doc.doc_id), doc.normalized_path)
        for doc in manifest.corpus_manifest.docs
        if doc.normalized_path is not None
    ]
    actual = [(doc.doc_id, doc.locator) for doc in snapshot.documents]
    if actual != expected:
        raise OrchestratorError("corpus snapshot inventory does not match the manifest")
    sources = {
        (source.locator, source.sha256)
        for source in manifest.sources
        if source.kind == "corpus_content"
    }
    for doc in snapshot.documents:
        digest = _sha256(doc.text.encode("utf-8"))
        if digest != doc.sha256 or (doc.locator, digest) not in sources:
            raise OrchestratorError(
                f"corpus snapshot content for {doc.doc_id!r} does not match its provenance"
            )


def _source(kind: ContextSourceKind, locator: str, raw: bytes) -> ContextSource:
    return ContextSource(kind=kind, locator=locator, sha256=_sha256(raw))


def _load_request_sets(vault_root: Path | str) -> tuple[list[RequestSet], list[ContextSource]]:
    requests_dir = safe_vault_path(vault_root, "requests")
    if not requests_dir.is_dir():
        return [], []
    sets: list[RequestSet] = []
    sources: list[ContextSource] = []
    for discovered in sorted(requests_dir.glob("*.json")):
        path = safe_vault_path(vault_root, "requests", discovered.name)
        raw = path.read_bytes()
        sets.append(RequestSet.model_validate_json(raw))
        sources.append(
            ContextSource(
                kind="request_set",
                locator=f"requests/{path.name}",
                sha256=_sha256(raw),
            )
        )
    sets.sort(key=lambda request_set: (request_set.set_number, request_set.request_type.value))
    return sets, sources


def _load_facts(vault_root: Path | str) -> tuple[list[Fact], bytes]:
    path = safe_vault_path(vault_root, *FACTS_PATH)
    raw = path.read_bytes() if path.is_file() else b""
    records = [Fact.model_validate_json(line) for line in raw.splitlines() if line.strip()]
    current = [fact for fact in fold_facts(records).values() if fact.superseded_by is None]
    return current, raw


def _load_corpus(
    vault_root: Path | str,
) -> tuple[Manifest, list[CorpusTextSnapshot], list[ContextSource]]:
    manifest_path = safe_vault_path(vault_root, *MANIFEST_PATH)
    manifest_raw = manifest_path.read_bytes() if manifest_path.is_file() else b""
    manifest = Manifest.model_validate_json(manifest_raw) if manifest_raw else Manifest()
    sources = [_source("corpus_manifest", "corpus/manifest.json", manifest_raw)]
    texts: list[CorpusTextSnapshot] = []
    captured_bytes = 0
    for doc in manifest.docs:
        if doc.normalized_path is None:
            continue
        path = safe_vault_path(vault_root, *Path(doc.normalized_path).parts)
        if not path.is_file():
            raise OrchestratorError(
                f"corpus document {doc.doc_id!r} is missing normalized content at "
                f"{doc.normalized_path!r}"
            )
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise OrchestratorError(
                f"corpus document {doc.doc_id!r} cannot be sized for snapshotting"
            ) from exc
        captured_bytes += size
        if captured_bytes > MAX_CORPUS_SNAPSHOT_BYTES:
            raise OrchestratorError(
                "corpus snapshot exceeds the 256 MiB launch limit; "
                "reduce or split the matter corpus"
            )
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OrchestratorError(
                f"corpus document {doc.doc_id!r} is not valid UTF-8"
            ) from exc
        digest = _sha256(raw)
        texts.append(
            CorpusTextSnapshot(
                doc_id=str(doc.doc_id),
                locator=doc.normalized_path,
                sha256=digest,
                text=text,
            )
        )
        sources.append(
            ContextSource(
                kind="corpus_content",
                locator=doc.normalized_path,
                sha256=digest,
            )
        )
    return manifest, texts, sources


def _validate_task_spec(
    vault_root: Path | str,
    task_spec_id: str | None,
    task: str,
    matter_id: str,
) -> TaskSpec | None:
    if task_spec_id is None:
        return None
    spec = TaskSpecStore(vault_root).get(task_spec_id)
    if spec is None:
        raise OrchestratorError(f"TaskSpec {task_spec_id!r} was not found; cannot start run")
    if str(spec.matter_id) != matter_id:
        raise OrchestratorError(
            f"TaskSpec {task_spec_id!r} belongs to matter {spec.matter_id!r}, not {matter_id!r}"
        )
    if not spec.runnable:
        raise OrchestratorError(f"TaskSpec {task_spec_id!r} is not runnable")
    if spec.task != task:
        raise OrchestratorError(
            f"TaskSpec {task_spec_id!r} task {spec.task!r} does not match requested task {task!r}"
        )
    return spec


def build_run_context(
    vault_root: Path | str,
    run_id: str,
    task: str,
    binding: TaskBinding,
    matter_config: MatterConfig,
    mode: RunMode,
    max_attempts: int,
    task_spec_id: str | None,
) -> RunContext:
    task_spec = _validate_task_spec(
        vault_root, task_spec_id, task, str(matter_config.matter_id)
    )
    request_sets, request_sources = _load_request_sets(vault_root)
    facts, facts_raw = _load_facts(vault_root)
    corpus_manifest, corpus_texts, corpus_sources = _load_corpus(vault_root)
    corpus_snapshot = CorpusSnapshot(documents=corpus_texts)
    adapter_config_file = task_config_path(task)
    rubric_file = rubric_path(binding.config.rubric_id)
    matter_file = safe_vault_path(vault_root, "matter.yaml")
    rubric_lock_file = rubric_file.with_suffix(".sha256")
    matter_raw = matter_file.read_bytes()
    adapter_raw = adapter_config_file.read_bytes()
    rubric_raw = rubric_file.read_bytes()
    rubric_lock_raw = rubric_lock_file.read_bytes() if rubric_lock_file.is_file() else b""
    persona_bodies = load_persona_bodies()
    try:
        captured_matter = MatterConfig.model_validate(yaml.safe_load(matter_raw))
        captured_adapter = TaskAdapterConfig.model_validate(yaml.safe_load(adapter_raw))
        captured_rubric = Rubric.model_validate(yaml.safe_load(rubric_raw))
    except (ValidationError, yaml.YAMLError) as exc:
        raise OrchestratorError(f"launch context source changed or is invalid: {exc}") from exc
    if (
        captured_matter != matter_config
        or captured_adapter != binding.config
        or captured_rubric != binding.rubric
    ):
        raise OrchestratorError("launch context changed while the run snapshot was captured")
    if captured_rubric.locked:
        recorded = rubric_lock_raw.decode("utf-8").split()[0] if rubric_lock_raw else ""
        if recorded != sha256_hex(rubric_raw.decode("utf-8")):
            raise OrchestratorError("rubric lock changed while the run snapshot was captured")
    sources = [
        _source("matter_config", "matter.yaml", matter_raw),
        _source("task_adapter", f"config/tasks/{adapter_config_file.name}", adapter_raw),
        _source("rubric", f"rubrics/{rubric_file.name}", rubric_raw),
        _source("rubric_lock", f"rubrics/{rubric_lock_file.name}", rubric_lock_raw),
        _source("fact_repository", "facts/facts.jsonl", facts_raw),
        *[
            _source(
                "persona_body",
                f"personas/{persona.body_slug}.md",
                body.encode("utf-8"),
            )
            for persona, body in persona_bodies.items()
        ],
        *request_sources,
        *corpus_sources,
    ]
    if task_spec is not None:
        sources.append(
            ContextSource(
                kind="task_spec",
                locator=f"tasks/specs.jsonl#{task_spec.task_spec_id}",
                sha256=_sha256(task_spec.model_dump_json().encode("utf-8")),
            )
        )
    manifest = RunContextManifest(
        run_id=RunId(run_id),
        matter_id=MatterId(matter_config.matter_id),
        task=task,
        task_spec=task_spec,
        adapter_config=captured_adapter,
        adapter_behavior=AdapterBehavior(
            task=binding.adapter.task,
            draft_directive=binding.adapter.draft_directive(),
            judge_question=binding.adapter.judge_question(),
        ),
        persona_bodies=persona_bodies,
        rubric=captured_rubric,
        request_sets=request_sets,
        facts=facts,
        corpus_manifest=corpus_manifest,
        corpus_snapshot_sha256=_sha256(_corpus_payload(corpus_snapshot).encode("utf-8")),
        matter_config=captured_matter,
        effective_mode=mode,
        max_attempts=max_attempts,
        tier_models=budget.tier_models(captured_matter.budget.tier),
        sources=sources,
    )
    return _materialize(manifest, corpus_snapshot)


def _materialize(
    manifest: RunContextManifest, corpus_snapshot: CorpusSnapshot | None = None
) -> RunContext:
    if corpus_snapshot is not None:
        _validate_corpus_snapshot(manifest, corpus_snapshot)
    adapter = FrozenTaskAdapter(
        task=manifest.adapter_behavior.task,
        _draft_directive=manifest.adapter_behavior.draft_directive,
        _judge_question=manifest.adapter_behavior.judge_question,
    )
    binding = TaskBinding(
        config=manifest.adapter_config,
        adapter=adapter,
        rubric=manifest.rubric,
    )
    units = [
        item
        for request_set in manifest.request_sets
        for item in request_set.items
        if item.subpart is None
    ]
    units.sort(key=lambda item: (item.set_number, item.number))
    facts = [
        {"fact_id": str(fact.fact_id), "statement": fact.statement}
        for fact in manifest.facts
    ]
    return RunContext(
        manifest=manifest,
        binding=binding,
        units=units,
        facts=facts,
        corpus_snapshot=corpus_snapshot,
    )


def write_run_context(vault_root: Path | str, context: RunContext) -> str:
    """Publish a manifest once, or recover an identical manifest-only launch."""
    if context.corpus_snapshot is None:
        raise OrchestratorError("cannot publish a run context without its corpus snapshot")
    corpus_path = corpus_snapshot_path(vault_root, str(context.manifest.run_id))
    corpus_payload = _corpus_payload(context.corpus_snapshot)
    _check_retained_corpus_quota(vault_root, corpus_path, len(corpus_payload.encode("utf-8")))
    _publish_once(
        corpus_path,
        corpus_payload,
        f"run {context.manifest.run_id!r} already has a different corpus snapshot",
    )
    path = context_manifest_path(vault_root, str(context.manifest.run_id))
    payload = context.manifest.model_dump_json(indent=2) + "\n"
    _publish_once(
        path,
        payload,
        f"run {context.manifest.run_id!r} already has a context manifest; refusing overwrite",
    )
    return _sha256(payload.encode())


def _check_retained_corpus_quota(
    vault_root: Path | str, target: Path, incoming_bytes: int
) -> None:
    runs_dir = safe_vault_path(vault_root, "runs")
    retained = 0
    if runs_dir.is_dir():
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            candidate = safe_vault_path(
                vault_root, "runs", run_dir.name, *CORPUS_SNAPSHOT_SUBPATH
            )
            if candidate == target:
                continue
            try:
                retained += candidate.stat().st_size
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise OrchestratorError(
                    f"cannot verify retained corpus quota because {candidate} is unreadable"
                ) from exc
    if retained + incoming_bytes > MAX_RETAINED_CORPUS_SNAPSHOT_BYTES:
        raise OrchestratorError(
            "retained run corpus snapshots exceed the 2 GiB matter limit; "
            "complete an approved archival/retention cleanup before starting another run"
        )


def _publish_once(path: Path, payload: str, conflict_message: str) -> None:
    expected = payload.encode()
    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        try:
            atomic_write_once_text(path, payload)
            return
        except FileExistsError:
            try:
                existing = path.read_bytes()
            except OSError as exc:
                raise OrchestratorError(conflict_message) from exc
    except OSError as exc:
        raise OrchestratorError(conflict_message) from exc
    if existing != expected:
        raise OrchestratorError(conflict_message)
    try:
        fsync_file_and_parent(path)
    except OSError as exc:
        raise OrchestratorError(f"could not make existing context durable at {path}") from exc


def load_run_context(vault_root: Path | str, run_id: str) -> RunContext:
    from mootloop.journal import read_events

    started = [event for event in read_events(vault_root, run_id) if isinstance(event, RunStarted)]
    if len(started) != 1:
        raise OrchestratorError(
            f"run {run_id!r} must have exactly one RunStarted event; found {len(started)}"
        )
    event = started[0]
    if not event.context_manifest_sha256:
        raise OrchestratorError(
            f"run {run_id!r} has no committed context manifest digest; start a new run"
        )
    path = context_manifest_path(vault_root, run_id)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise OrchestratorError(
            f"run {run_id!r} context manifest is missing; restore it or start a new run"
        ) from None
    except OSError as exc:
        raise OrchestratorError(
            f"run {run_id!r} context manifest is unreadable; restore it or start a new run"
        ) from exc
    actual = _sha256(raw)
    if actual != event.context_manifest_sha256:
        raise OrchestratorError(
            f"run {run_id!r} context manifest was tampered with or has the wrong digest"
        )
    try:
        manifest = load_versioned_json(
            raw,
            RunContextManifest,
            current_version=RUN_CONTEXT_SCHEMA_VERSION,
        )
    except MigrationError as exc:
        raise OrchestratorError(
            f"run {run_id!r} context manifest failed validation: {exc}"
        ) from exc
    if (
        event.run_id != run_id
        or str(manifest.run_id) != run_id
        or str(manifest.matter_id) != event.matter_id
        or manifest.task != event.task
        or manifest.adapter_config.rubric_id != event.rubric_version
        or manifest.rubric.rubric_id != event.rubric_version
        or config_digest(manifest.adapter_config) != event.config_digest
        or manifest.effective_mode != event.mode
    ):
        raise OrchestratorError(
            f"run {run_id!r} context manifest identity does not match RunStarted"
        )
    manifest_task_spec_id = (
        str(manifest.task_spec.task_spec_id) if manifest.task_spec is not None else None
    )
    if manifest_task_spec_id != event.task_spec_id:
        raise OrchestratorError(
            f"run {run_id!r} context manifest TaskSpec does not match RunStarted"
        )
    corpus_path = corpus_snapshot_path(vault_root, run_id)
    try:
        corpus_digest = _sha256_path(corpus_path)
    except FileNotFoundError:
        raise OrchestratorError(
            f"run {run_id!r} corpus snapshot is missing; restore it or start a new run"
        ) from None
    except OSError as exc:
        raise OrchestratorError(
            f"run {run_id!r} corpus snapshot is unreadable; restore it or start a new run"
        ) from exc
    if corpus_digest != manifest.corpus_snapshot_sha256:
        raise OrchestratorError(
            f"run {run_id!r} corpus snapshot was tampered with or has the wrong digest"
        )
    return _materialize(manifest)


def load_run_corpus(vault_root: Path | str, context: RunContext) -> CorpusSnapshot:
    """Load and verify corpus bodies only for operations that consume evidence."""
    if context.corpus_snapshot is not None:
        return context.corpus_snapshot
    run_id = str(context.manifest.run_id)
    corpus_path = corpus_snapshot_path(vault_root, run_id)
    try:
        corpus_raw = corpus_path.read_bytes()
    except FileNotFoundError:
        raise OrchestratorError(
            f"run {run_id!r} corpus snapshot is missing; restore it or start a new run"
        ) from None
    except OSError as exc:
        raise OrchestratorError(
            f"run {run_id!r} corpus snapshot is unreadable; restore it or start a new run"
        ) from exc
    if _sha256(corpus_raw) != context.manifest.corpus_snapshot_sha256:
        raise OrchestratorError(
            f"run {run_id!r} corpus snapshot was tampered with or has the wrong digest"
        )
    try:
        corpus_snapshot = CorpusSnapshot.model_validate_json(corpus_raw)
    except ValidationError as exc:
        raise OrchestratorError(
            f"run {run_id!r} corpus snapshot failed validation: {exc}"
        ) from exc
    _validate_corpus_snapshot(context.manifest, corpus_snapshot)
    return corpus_snapshot
