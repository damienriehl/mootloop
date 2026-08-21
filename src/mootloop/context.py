"""Write-once run-context capture and exact replay."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from mootloop import budget
from mootloop.config import ConfigLayerInput, default_config_layer, resolve_run_config
from mootloop.context_assembly import assemble_context, select_launch_contributions
from mootloop.errors import MigrationError, OrchestratorError, PipelineConfigError, TaskSpecError
from mootloop.facts import FACTS_PATH
from mootloop.facts import fold as fold_facts
from mootloop.migrations import load_versioned_json
from mootloop.models.common import MatterId, RunId
from mootloop.models.config import BudgetOverlay, ResolvedRunConfig, RunConfigOverlay
from mootloop.models.context import (
    SCHEMA_VERSION as RUN_CONTEXT_SCHEMA_VERSION,
)
from mootloop.models.context import (
    AdapterBehavior,
    ContextContribution,
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
from mootloop.models.pipeline import ResolvedPipeline
from mootloop.models.requests import RequestItem, RequestSet
from mootloop.models.rubric import Rubric, sha256_hex
from mootloop.models.task import TaskAdapterConfig
from mootloop.models.taskspec import TaskSpec, TaskSpecLock
from mootloop.persistence import sha256_file as _sha256_path
from mootloop.pipeline import compile_pipeline
from mootloop.resources import (
    REPO_ROOT,
    compose_persona_bodies,
    load_persona_sources,
    rubric_path,
    task_config_path,
)
from mootloop.tasks import TaskBinding
from mootloop.taskspec import TaskSpecStore, require_current_lock
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


def resolve_launch_pipeline(
    vault_root: Path | str,
    binding: TaskBinding,
    matter_config: MatterConfig,
    resolved_config: ResolvedRunConfig,
) -> ResolvedPipeline:
    """Compile an exact graph from the two launch sources that own it."""
    matter_raw = safe_vault_path(vault_root, "matter.yaml").read_bytes()
    adapter_raw = task_config_path(binding.config.task).read_bytes()
    try:
        captured_matter = MatterConfig.model_validate(yaml.safe_load(matter_raw))
        captured_adapter = TaskAdapterConfig.model_validate(yaml.safe_load(adapter_raw))
    except (ValidationError, yaml.YAMLError) as exc:
        raise OrchestratorError(f"pipeline source changed or is invalid: {exc}") from exc
    if captured_matter != matter_config or captured_adapter != binding.config:
        raise OrchestratorError("pipeline source changed while selection was resolved")
    return compile_pipeline(
        captured_adapter,
        captured_matter,
        matter_sha256=_sha256(matter_raw),
        adapter_sha256=_sha256(adapter_raw),
        resolved_config=resolved_config,
    )


def context_manifest_path(vault_root: Path | str, run_id: str) -> Path:
    return safe_vault_path(vault_root, "runs", run_id, *MANIFEST_SUBPATH)


def corpus_snapshot_path(vault_root: Path | str, run_id: str) -> Path:
    return safe_vault_path(vault_root, "runs", run_id, *CORPUS_SNAPSHOT_SUBPATH)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_model_bytes(model: ResolvedRunConfig) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def config_digest(config: ResolvedRunConfig) -> str:
    """Compact deterministic digest of the complete effective launch config."""
    return _sha256(_canonical_model_bytes(config))[:16]


def _legacy_config_digest(config: TaskAdapterConfig) -> str:
    """Digest written by v1.0 RunStarted events; retained only for migration replay."""
    return _sha256(
        config.model_dump_json(exclude={"overridable", "pipeline_strategies"}).encode("utf-8")
    )[:16]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _firm_preferences_layer(
    vault_root: Path | str, path: Path | str | None
) -> ConfigLayerInput | None:
    if path is None:
        return None
    source = Path(path)
    try:
        real = source.resolve(strict=True)
    except OSError as exc:
        raise OrchestratorError(
            f"firm preferences {source!s} could not be resolved: {exc}"
        ) from exc
    repo_real = REPO_ROOT.resolve()
    vault_real = Path(vault_root).resolve()
    if _is_within(real, repo_real):
        raise OrchestratorError(
            f"firm preferences {real!s} are inside the repo; inject an external path"
        )
    if _is_within(real, vault_real):
        raise OrchestratorError(
            f"firm preferences {real!s} are inside the active matter vault; "
            "inject a separate firm-config path"
        )
    return ConfigLayerInput.from_path(real)


def resolve_launch_config(
    vault_root: Path | str,
    task: str,
    matter_config: MatterConfig,
    *,
    mode: RunMode | None,
    max_attempts: int | None,
    firm_preferences_path: Path | str | None,
) -> ResolvedRunConfig:
    """Resolve all launch layers without reading any run-history artifacts."""
    legacy_fallback = RunConfigOverlay(
        run_mode=matter_config.run_mode,
        budget=BudgetOverlay(
            tier=matter_config.budget.tier,
            hard_cap_usd=matter_config.budget.hard_cap_usd,
        ),
    )
    matter_content: dict[str, object] = {}
    if "panels" in matter_config.model_fields_set:
        legacy_panels: dict[str, object] = {}
        if "jury_enabled" in matter_config.panels.model_fields_set:
            legacy_panels["jury"] = matter_config.panels.jury_enabled
        if "jurors" in matter_config.panels.model_fields_set:
            legacy_panels["jurors"] = matter_config.panels.jurors
        if legacy_panels:
            matter_content["panels"] = legacy_panels
    if matter_config.run_config is not None:
        authored = matter_config.run_config.model_dump(exclude_unset=True)
        authored_panels = authored.pop("panels", None)
        matter_content.update(authored)
        if authored_panels is not None:
            current_panels = matter_content.get("panels")
            combined_panels: dict[str, object] = (
                dict(current_panels) if isinstance(current_panels, dict) else {}
            )
            combined_panels.update(authored_panels)
            matter_content["panels"] = combined_panels
    matter_overlay = (
        ConfigLayerInput.from_mapping("matter.yaml#run_config", matter_content)
        if matter_content
        else None
    )
    matter_provenance = ConfigLayerInput.from_mapping(
        "matter.yaml#runtime",
        {
            "legacy_fallback": legacy_fallback.model_dump(exclude_unset=True),
            "run_config": matter_content,
        },
    )
    invocation: dict[str, object] = {}
    if mode is not None:
        invocation["run_mode"] = mode
    if max_attempts is not None:
        invocation["max_attempts"] = max_attempts
    invocation_flags = (
        ConfigLayerInput.from_mapping("invocation:start_run", invocation)
        if invocation
        else None
    )
    return resolve_run_config(
        defaults=default_config_layer(),
        adapter=ConfigLayerInput.from_path(task_config_path(task)),
        legacy_fallback=legacy_fallback,
        firm_preferences=_firm_preferences_layer(vault_root, firm_preferences_path),
        matter_overlay=matter_overlay,
        matter_provenance=matter_provenance,
        invocation_flags=invocation_flags,
    )


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
        if doc.run_visible
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
    records: list[Fact] = []
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        if line.strip():
            records.append(Fact.model_validate_json(line))
    current = [
        fact
        for fact in fold_facts(records).values()
        if fact.superseded_by is None and fact.review_status == "accepted"
    ]
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
    visible_docs = [doc for doc in manifest.docs if doc.run_visible]
    if manifest_raw and not visible_docs:
        raise OrchestratorError(
            "no reviewed corpus documents are run-visible; confirm role and privilege first"
        )
    for doc in visible_docs:
        assert doc.normalized_path is not None
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
                doc_id=doc.doc_id,
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
    try:
        spec = TaskSpecStore(vault_root).get(task_spec_id)
    except TaskSpecError as exc:
        raise OrchestratorError(str(exc)) from exc
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
    mode: RunMode | None,
    max_attempts: int | None,
    task_spec_id: str | None,
    firm_preferences_path: Path | str | None = None,
    context_contributions: Sequence[ContextContribution] = (),
) -> RunContext:
    task_spec = _validate_task_spec(
        vault_root, task_spec_id, task, str(matter_config.matter_id)
    )
    request_sets, request_sources = _load_request_sets(vault_root)
    facts, facts_raw = _load_facts(vault_root)
    corpus_manifest, corpus_texts, corpus_sources = _load_corpus(vault_root)
    corpus_snapshot = CorpusSnapshot(documents=corpus_texts)
    resolved_config = resolve_launch_config(
        vault_root,
        task,
        matter_config,
        mode=mode,
        max_attempts=max_attempts,
        firm_preferences_path=firm_preferences_path,
    )
    adapter_config_file = task_config_path(task)
    rubric_file = rubric_path(binding.config.rubric_id)
    matter_file = safe_vault_path(vault_root, "matter.yaml")
    rubric_lock_file = rubric_file.with_suffix(".sha256")
    matter_raw = matter_file.read_bytes()
    adapter_raw = adapter_config_file.read_bytes()
    rubric_raw = rubric_file.read_bytes()
    rubric_lock_raw = rubric_lock_file.read_bytes() if rubric_lock_file.is_file() else b""
    persona_sources = load_persona_sources()
    persona_bodies = compose_persona_bodies(persona_sources)
    accepted_contributions, context_exclusions = select_launch_contributions(
        context_contributions,
        matter_id=MatterId(matter_config.matter_id),
        task=task,
    )
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
    pipeline = compile_pipeline(
        captured_adapter,
        captured_matter,
        matter_sha256=_sha256(matter_raw),
        adapter_sha256=_sha256(adapter_raw),
        resolved_config=resolved_config,
    )
    if captured_rubric.locked:
        recorded = rubric_lock_raw.decode("utf-8").split()[0] if rubric_lock_raw else ""
        if recorded != sha256_hex(rubric_raw.decode("utf-8")):
            raise OrchestratorError("rubric lock changed while the run snapshot was captured")
    task_spec_lock: TaskSpecLock | None = None
    if task_spec is not None:
        try:
            task_spec_lock = require_current_lock(
                vault_root,
                str(matter_config.matter_id),
                task_spec,
                adapter_raw=adapter_raw,
                rubric_raw=rubric_raw,
                rubric_lock_raw=rubric_lock_raw,
            )
        except TaskSpecError as exc:
            raise OrchestratorError(str(exc)) from exc
    sources = [
        _source("matter_config", "matter.yaml", matter_raw),
        _source("task_adapter", f"config/tasks/{adapter_config_file.name}", adapter_raw),
        _source("rubric", f"rubrics/{rubric_file.name}", rubric_raw),
        _source("rubric_lock", f"rubrics/{rubric_lock_file.name}", rubric_lock_raw),
        _source("fact_repository", "facts/facts.jsonl", facts_raw),
        *[
            _source("persona_body", f"personas/{filename}", raw)
            for filename, raw in persona_sources.items()
        ],
        *request_sources,
        *corpus_sources,
        *[
            ContextSource(
                kind="context_contribution",
                locator=contribution.provenance_locator,
                sha256=contribution.sha256,
            )
            for contribution in accepted_contributions
        ],
    ]
    if task_spec is not None:
        sources.append(
            ContextSource(
                kind="task_spec",
                locator=f"tasks/specs.jsonl#{task_spec.task_spec_id}",
                sha256=_sha256(task_spec.model_dump_json().encode("utf-8")),
            )
        )
        assert task_spec_lock is not None
        sources.append(
            ContextSource(
                kind="task_spec_lock",
                locator=f"tasks/locks.jsonl#{task_spec_lock.task_spec_lock_id}",
                sha256=task_spec_lock.record_sha256,
            )
        )
    manifest = RunContextManifest(
        run_id=RunId(run_id),
        matter_id=MatterId(matter_config.matter_id),
        task=task,
        task_spec=task_spec,
        task_spec_lock=task_spec_lock,
        adapter_config=captured_adapter,
        resolved_config=resolved_config,
        pipeline=pipeline,
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
        context_contributions=list(accepted_contributions),
        context_exclusions=list(context_exclusions),
        matter_config=captured_matter,
        effective_mode=resolved_config.run_mode,
        max_attempts=resolved_config.max_attempts,
        tier_models=budget.tier_models(resolved_config.budget.tier),
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
        config=manifest.pipeline.effective_config.model_copy(deep=True),
        adapter=adapter,
        rubric=manifest.rubric,
        pipeline=manifest.pipeline,
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
    if corpus_snapshot is not None:
        assemble_context(manifest, corpus_snapshot)
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
    raw_payload = json.loads(raw)
    raw_schema_version = raw_payload.get("schema_version")
    legacy_manifest = raw_schema_version == "1.0"
    if raw_schema_version == RUN_CONTEXT_SCHEMA_VERSION:
        matter_sources = [source for source in manifest.sources if source.kind == "matter_config"]
        adapter_sources = [source for source in manifest.sources if source.kind == "task_adapter"]
        if len(matter_sources) != 1 or len(adapter_sources) != 1:
            raise OrchestratorError(
                f"run {run_id!r} context manifest has ambiguous pipeline provenance"
            )
        try:
            expected_pipeline = compile_pipeline(
                manifest.adapter_config,
                manifest.matter_config,
                matter_sha256=matter_sources[0].sha256,
                adapter_sha256=adapter_sources[0].sha256,
                resolved_config=manifest.resolved_config,
            )
        except (PipelineConfigError, ValidationError) as exc:
            raise OrchestratorError(
                f"run {run_id!r} context manifest pipeline cannot be reproduced"
            ) from exc
        if manifest.pipeline != expected_pipeline:
            raise OrchestratorError(
                f"run {run_id!r} context manifest pipeline does not match captured inputs"
            )
    expected_config_digest = (
        _legacy_config_digest(manifest.adapter_config)
        if legacy_manifest
        else config_digest(manifest.resolved_config)
    )
    if (
        event.run_id != run_id
        or str(manifest.run_id) != run_id
        or str(manifest.matter_id) != event.matter_id
        or manifest.task != event.task
        or manifest.resolved_config.rubric_id != event.rubric_version
        or manifest.rubric.rubric_id != event.rubric_version
        or expected_config_digest != event.config_digest
        or manifest.resolved_config.run_mode != event.mode
        or manifest.effective_mode != manifest.resolved_config.run_mode
        or manifest.max_attempts != manifest.resolved_config.max_attempts
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
    manifest_lock_id = (
        manifest.task_spec_lock.task_spec_lock_id
        if manifest.task_spec_lock is not None
        else None
    )
    manifest_lock_sha256 = (
        manifest.task_spec_lock.record_sha256
        if manifest.task_spec_lock is not None
        else None
    )
    current_lock_contract = raw_payload.get("schema_version") == RUN_CONTEXT_SCHEMA_VERSION
    if (
        manifest_lock_id != event.task_spec_lock_id
        or manifest_lock_sha256 != event.task_spec_lock_sha256
        or (
            current_lock_contract
            and (manifest.task_spec is None) != (manifest.task_spec_lock is None)
        )
    ):
        raise OrchestratorError(
            f"run {run_id!r} context manifest TaskSpec lock does not match RunStarted"
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
