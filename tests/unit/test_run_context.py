"""Run-start input binding and fail-closed replay."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from mootloop.context import config_digest, load_run_context
from mootloop.discovery_parser import save_requests
from mootloop.errors import OrchestratorError
from mootloop.facts import FactStore
from mootloop.journal import clear_cache, read_events
from mootloop.llm import FakeLLMProvider
from mootloop.models.common import DocId, MatterId, TaskSpecId
from mootloop.models.corpus import CorpusDoc, Manifest
from mootloop.models.events import JournalEvent, RunStarted
from mootloop.models.evidence import RunStatusSidecar
from mootloop.models.requests import RequestItem, RequestSet, RequestType
from mootloop.models.run import PersonaName
from mootloop.models.taskspec import TaskSpec
from mootloop.orchestrator import (
    assemble_prompt,
    plan_next,
    record_turn,
    resume_run,
    run_with_provider,
    start_run,
)
from mootloop.panels import build_panel_report
from mootloop.tasks import DiscoveryResponsesAdapter
from mootloop.taskspec import TaskSpecStore, create_freeform, lock_task_spec
from mootloop.vault import init_vault
from tests.conftest import make_matter

NOW = "2026-07-11T00:00:00+00:00"
TASK = "discovery-responses"


def _request_set(text: str = "Identify every person with contract knowledge.") -> RequestSet:
    return RequestSet(
        request_type=RequestType.INTERROGATORY,
        set_number=1,
        title="Interrogatories Set 1",
        items=[
            RequestItem(
                request_id="ROG-1",  # type: ignore[arg-type]
                set_number=1,
                number=1,
                text=text,
                source_doc=DocId("doc-servedservedserv"),
            )
        ],
    )


def _replacement_request_set() -> RequestSet:
    return RequestSet(
        request_type=RequestType.INTERROGATORY,
        set_number=1,
        title="Changed Interrogatories",
        items=[
            RequestItem(
                request_id="ROG-2",  # type: ignore[arg-type]
                set_number=1,
                number=2,
                text="A changed request that belongs to a new run.",
                source_doc=DocId("doc-changedchangedch"),
            )
        ],
    )


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
    save_requests(vault, _request_set())
    FactStore(vault).add_fact("The original fact.", confidence=1.0)
    return vault


def _manifest_path(vault: Path, run_id: str) -> Path:
    return vault / "runs" / run_id / "context" / "manifest.json"


def _add_corpus_doc(vault: Path, text: str, *, privileged: bool = True) -> Path:
    doc_id = DocId("doc-contextsnapshot")
    relative = f"corpus/normalized/{doc_id}.md"
    path = vault / relative
    path.write_text(text, encoding="utf-8")
    Manifest(
        docs=[
            CorpusDoc(
                doc_id=doc_id,
                original_name="contract.txt",
                media_type="text/plain",
                role="client-doc",
                privileged=privileged,
                ingest_status="ok",
                normalized_path=relative,
                ingested_at=NOW,
            )
        ]
    ).save(vault)
    return path


def test_start_rejects_missing_task_spec(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(OrchestratorError, match="TaskSpec.*not found"):
        start_run(vault, TASK, NOW, run_id="ctx-missing-spec", task_spec_id="missing")
    assert not (vault / "runs" / "ctx-missing-spec" / "journal.jsonl").exists()


@pytest.mark.parametrize(
    ("spec_task", "matter_id", "message"),
    [
        (None, "acme-v-widgets", "not runnable"),
        ("another-task", "acme-v-widgets", "does not match"),
        (TASK, "other-matter", "belongs to matter"),
    ],
)
def test_start_rejects_invalid_task_spec_binding(
    tmp_path: Path, spec_task: str | None, matter_id: str, message: str
) -> None:
    vault = _vault(tmp_path)
    spec = TaskSpec(
        task_spec_id=TaskSpecId("taskspec-invalid"),
        matter_id=MatterId(matter_id),
        task=spec_task,
        source_lane="freeform",
        intent_text="test intent",
        created_at=NOW,
    )
    TaskSpecStore(vault).append(spec)

    with pytest.raises(OrchestratorError, match=message):
        start_run(vault, TASK, NOW, run_id="ctx-invalid-spec", task_spec_id=str(spec.task_spec_id))


def test_start_commits_versioned_manifest_and_task_spec(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    spec = create_freeform(vault, "acme-v-widgets", "answer the discovery", NOW)
    lock_task_spec(
        vault, "acme-v-widgets", str(spec.task_spec_id), "test-attorney", NOW
    )
    run_id = start_run(vault, TASK, NOW, run_id="ctx-start", task_spec_id=str(spec.task_spec_id))

    started = next(event for event in read_events(vault, run_id) if isinstance(event, RunStarted))
    context = load_run_context(vault, run_id)
    assert started.context_manifest_sha256
    assert context.manifest.schema_version == "1.5"
    assert context.manifest.pipeline.strategy == "thin-full"
    assert context.manifest.task_spec == spec
    assert context.manifest.task_spec_lock is not None
    assert context.manifest.adapter_behavior.draft_directive
    assert context.manifest.adapter_behavior.judge_question


def test_start_resolves_five_layers_and_binds_effective_config_digest(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    matter = yaml.safe_load((vault / "matter.yaml").read_text(encoding="utf-8"))
    matter["run_mode"] = "gated"
    matter["budget"] = {"tier": "low", "hard_cap_usd": 9.0}
    matter["run_config"] = {"loop_caps": {"associate_partner": 4}}
    (vault / "matter.yaml").write_text(yaml.safe_dump(matter), encoding="utf-8")
    firm = tmp_path / "firm-preferences.yaml"
    firm.write_text(
        "schema_version: '1.0'\nrun_config:\n  rubric_threshold: 0.66\n",
        encoding="utf-8",
    )

    run_id = start_run(
        vault,
        TASK,
        NOW,
        run_id="ctx-five-layers",
        mode="observed",
        max_attempts=5,
        firm_preferences_path=firm,
    )

    context = load_run_context(vault, run_id)
    resolved = context.manifest.resolved_config
    started = next(event for event in read_events(vault, run_id) if isinstance(event, RunStarted))
    assert [source.layer for source in resolved.sources] == [
        "defaults",
        "task_adapter",
        "firm_preferences",
        "matter_overlay",
        "invocation_flags",
    ]
    assert all(source.present for source in resolved.sources)
    assert resolved.run_mode == "observed"
    assert resolved.max_attempts == 5
    assert resolved.loop_caps.associate_partner == 4
    assert resolved.rubric_threshold == 0.66
    assert resolved.budget.tier == "low"
    assert resolved.budget.hard_cap_usd == 9.0
    assert context.binding.config.loop_caps.associate_partner == 4
    assert started.config_digest == config_digest(resolved)


def test_legacy_matter_runtime_is_fallback_below_firm_and_explicit_overlay(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    matter = yaml.safe_load((vault / "matter.yaml").read_text(encoding="utf-8"))
    matter["run_mode"] = "autonomous"
    matter["budget"] = {"tier": "moderate", "hard_cap_usd": None}
    matter["run_config"] = {"budget": {"hard_cap_usd": 7.0}}
    (vault / "matter.yaml").write_text(yaml.safe_dump(matter), encoding="utf-8")
    firm = tmp_path / "firm-preferences.yaml"
    firm.write_text(
        "schema_version: '1.0'\n"
        "run_config:\n  run_mode: gated\n  max_attempts: 6\n"
        "  budget:\n    tier: low\n    hard_cap_usd: 3.0\n",
        encoding="utf-8",
    )

    run_id = start_run(
        vault,
        TASK,
        NOW,
        run_id="ctx-legacy-fallback",
        firm_preferences_path=firm,
    )

    resolved = load_run_context(vault, run_id).manifest.resolved_config
    assert resolved.run_mode == "gated"
    assert resolved.max_attempts == 6
    assert resolved.budget.tier == "low"
    assert resolved.budget.hard_cap_usd == 7.0
    invocation_source = next(
        source for source in resolved.sources if source.layer == "invocation_flags"
    )
    matter_source = next(
        source for source in resolved.sources if source.layer == "matter_overlay"
    )
    assert invocation_source.present is False
    assert matter_source.present is True
    assert matter_source.locator == "matter.yaml#runtime"


def test_legacy_matter_jury_settings_are_snapshotted_as_allowlisted_overlay(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    matter = yaml.safe_load((vault / "matter.yaml").read_text(encoding="utf-8"))
    matter["panels"] = {"jury_enabled": True, "jurors": 2}
    (vault / "matter.yaml").write_text(yaml.safe_dump(matter), encoding="utf-8")

    run_id = start_run(vault, TASK, NOW, run_id="ctx-jury-overlay")
    launched = load_run_context(vault, run_id)
    assert launched.manifest.resolved_config.panels.jury is True
    assert launched.manifest.resolved_config.panels.jurors == 2
    assert launched.binding.config.panels.jury is True
    assert launched.binding.config.panels.jurors == 2
    assert PersonaName.JUROR in launched.manifest.persona_bodies

    matter["panels"] = {"jury_enabled": False, "jurors": 0}
    (vault / "matter.yaml").write_text(yaml.safe_dump(matter), encoding="utf-8")
    replayed = load_run_context(vault, run_id)
    assert replayed.manifest.resolved_config.panels.jury is True
    assert replayed.manifest.resolved_config.panels.jurors == 2


def test_idempotent_run_reuse_compares_current_effective_config(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    firm = tmp_path / "firm-preferences.yaml"
    firm.write_text(
        "schema_version: '1.0'\nrun_config:\n  rubric_threshold: 0.66\n",
        encoding="utf-8",
    )
    run_id = start_run(
        vault,
        TASK,
        NOW,
        run_id="ctx-idempotent-config",
        firm_preferences_path=firm,
        idempotent=True,
    )
    original = load_run_context(vault, run_id).manifest.resolved_config
    firm.write_text(
        "schema_version: '1.0'\nrun_config:\n  rubric_threshold: 0.67\n",
        encoding="utf-8",
    )

    with pytest.raises(OrchestratorError, match="different launch context"):
        start_run(
            vault,
            TASK,
            NOW,
            run_id=run_id,
            firm_preferences_path=firm,
            idempotent=True,
        )

    assert load_run_context(vault, run_id).manifest.resolved_config == original
    new_run = start_run(
        vault,
        TASK,
        NOW,
        run_id="ctx-idempotent-config-new",
        firm_preferences_path=firm,
    )
    assert load_run_context(vault, new_run).manifest.resolved_config.rubric_threshold == 0.67


def test_idempotent_run_reuse_compares_persona_and_strategy_selection(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    run_id = start_run(
        vault,
        TASK,
        NOW,
        run_id="ctx-idempotent-pipeline",
        idempotent=True,
    )
    original = load_run_context(vault, run_id).manifest.pipeline
    matter = yaml.safe_load((vault / "matter.yaml").read_text(encoding="utf-8"))
    matter["pipeline_strategy"] = "adversarial-first"
    matter["personas"] = {"oc_associate": False}
    (vault / "matter.yaml").write_text(yaml.safe_dump(matter), encoding="utf-8")

    with pytest.raises(OrchestratorError, match="different launch context"):
        start_run(vault, TASK, NOW, run_id=run_id, idempotent=True)

    assert load_run_context(vault, run_id).manifest.pipeline == original
    next_id = start_run(vault, TASK, NOW, run_id="ctx-idempotent-pipeline-new")
    selected = load_run_context(vault, next_id).manifest.pipeline
    assert selected.strategy == "adversarial-first"
    assert selected.oc_personas == (PersonaName.OC_PARTNER,)


def test_pipeline_must_reproduce_from_captured_matter_and_adapter(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id="ctx-pipeline-derived")
    manifest_path = _manifest_path(vault, run_id)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["pipeline"]["strategy"] = "adversarial-first"
    payload["pipeline"]["effective_config"]["stages"] = [
        "associate_draft",
        "oc_attack",
        "bolster",
        "partner_loop",
        "judge_panel",
        "restructure",
        "rubric_gate",
        "assemble",
    ]
    manifest_raw = (json.dumps(payload, indent=2) + "\n").encode()
    manifest_path.write_bytes(manifest_raw)

    started = next(event for event in read_events(vault, run_id) if isinstance(event, RunStarted))
    started_payload = started.model_dump(mode="json")
    started_payload["context_manifest_sha256"] = hashlib.sha256(manifest_raw).hexdigest()
    (vault / "runs" / run_id / "journal.jsonl").write_text(
        json.dumps(started_payload, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    clear_cache()

    with pytest.raises(OrchestratorError, match="pipeline does not match captured inputs"):
        load_run_context(vault, run_id)


def test_replay_ignores_all_live_config_mutations_and_new_run_uses_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mootloop.config as config_module
    import mootloop.context as context_module
    import mootloop.tasks as tasks_module
    from mootloop.resources import DEFAULTS_CONFIG, task_config_path

    defaults = tmp_path / "defaults.yaml"
    adapter = tmp_path / "discovery-responses.yaml"
    shutil.copyfile(DEFAULTS_CONFIG, defaults)
    shutil.copyfile(task_config_path(TASK), adapter)
    defaults_raw = yaml.safe_load(defaults.read_text(encoding="utf-8"))
    defaults_raw["convergence"]["coverage_floor"] = 0.71
    defaults.write_text(yaml.safe_dump(defaults_raw), encoding="utf-8")
    adapter_raw = yaml.safe_load(adapter.read_text(encoding="utf-8"))
    adapter_raw["convergence"].pop("coverage_floor")
    adapter.write_text(yaml.safe_dump(adapter_raw), encoding="utf-8")
    monkeypatch.setattr(config_module, "DEFAULTS_CONFIG", defaults)
    monkeypatch.setattr(context_module, "task_config_path", lambda _task: adapter)
    monkeypatch.setattr(tasks_module, "task_config_path", lambda _task: adapter)

    vault = _vault(tmp_path)
    matter_raw = yaml.safe_load((vault / "matter.yaml").read_text(encoding="utf-8"))
    matter_raw["run_config"] = {"restructure_threshold": 0.45}
    (vault / "matter.yaml").write_text(yaml.safe_dump(matter_raw), encoding="utf-8")
    firm = tmp_path / "firm-preferences.yaml"
    firm.write_text(
        "schema_version: '1.0'\nrun_config:\n  rubric_threshold: 0.66\n",
        encoding="utf-8",
    )
    run_id = start_run(
        vault,
        TASK,
        NOW,
        run_id="ctx-all-config-sources",
        mode="observed",
        max_attempts=5,
        firm_preferences_path=firm,
        idempotent=True,
    )
    original = load_run_context(vault, run_id).manifest.resolved_config

    defaults_raw["convergence"]["coverage_floor"] = 0.72
    defaults.write_text(yaml.safe_dump(defaults_raw), encoding="utf-8")
    adapter_raw["loop_caps"]["associate_partner"] = 3
    adapter.write_text(yaml.safe_dump(adapter_raw), encoding="utf-8")
    firm.write_text(
        "schema_version: '1.0'\nrun_config:\n  rubric_threshold: 0.67\n",
        encoding="utf-8",
    )
    matter_raw["run_config"] = {"restructure_threshold": 0.46}
    (vault / "matter.yaml").write_text(yaml.safe_dump(matter_raw), encoding="utf-8")

    assert load_run_context(vault, run_id).manifest.resolved_config == original
    with pytest.raises(OrchestratorError, match="different launch context"):
        start_run(
            vault,
            TASK,
            NOW,
            run_id=run_id,
            mode="gated",
            max_attempts=6,
            firm_preferences_path=firm,
            idempotent=True,
        )
    new_id = start_run(
        vault,
        TASK,
        NOW,
        run_id="ctx-all-config-sources-new",
        mode="gated",
        max_attempts=6,
        firm_preferences_path=firm,
    )
    changed = load_run_context(vault, new_id).manifest.resolved_config
    assert changed.convergence.coverage_floor == 0.72
    assert changed.loop_caps.associate_partner == 3
    assert changed.rubric_threshold == 0.67
    assert changed.restructure_threshold == 0.46
    assert changed.run_mode == "gated"
    assert changed.max_attempts == 6


@pytest.mark.parametrize("location", ["repo", "vault"])
def test_start_rejects_firm_preferences_inside_protected_trees(
    tmp_path: Path, location: str
) -> None:
    from mootloop.resources import REPO_ROOT

    vault = _vault(tmp_path)
    if location == "repo":
        firm = REPO_ROOT / "config" / "defaults.yaml"
    else:
        firm = vault / "firm-preferences.yaml"
        firm.write_text("schema_version: '1.0'\n", encoding="utf-8")
    with pytest.raises(OrchestratorError, match="firm preferences.*(repo|vault)"):
        start_run(
            vault,
            TASK,
            NOW,
            run_id=f"ctx-firm-boundary-{location}",
            firm_preferences_path=firm,
        )


def test_start_recovers_identical_manifest_when_first_journal_append_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mootloop.orchestrator as orchestrator

    vault = _vault(tmp_path)
    real_append = orchestrator.append
    calls = 0

    def fail_first_append(
        vault_root: Path | str, run_id: str, event: JournalEvent
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected journal failure")
        real_append(vault_root, run_id, event)

    monkeypatch.setattr(orchestrator, "append", fail_first_append)
    with pytest.raises(OSError, match="injected journal failure"):
        start_run(vault, TASK, NOW, run_id="ctx-recover-start")

    assert _manifest_path(vault, "ctx-recover-start").is_file()
    assert read_events(vault, "ctx-recover-start") == []

    assert start_run(vault, TASK, NOW, run_id="ctx-recover-start") == "ctx-recover-start"
    started = [
        event
        for event in read_events(vault, "ctx-recover-start")
        if isinstance(event, RunStarted)
    ]
    assert len(started) == 1


def test_start_retry_requires_existing_context_to_resync_durably(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mootloop.context as context_module
    import mootloop.orchestrator as orchestrator

    vault = _vault(tmp_path)
    monkeypatch.setattr(orchestrator, "append", lambda *_args: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        start_run(vault, TASK, NOW, run_id="ctx-resync")
    monkeypatch.undo()
    monkeypatch.setattr(
        context_module,
        "fsync_file_and_parent",
        lambda _path: (_ for _ in ()).throw(OSError("sync failed")),
    )

    with pytest.raises(OrchestratorError, match="could not make existing context durable"):
        start_run(vault, TASK, NOW, run_id="ctx-resync")

    assert read_events(vault, "ctx-resync") == []


def test_start_rejects_duplicate_started_run(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    start_run(vault, TASK, NOW, run_id="ctx-duplicate")

    with pytest.raises(OrchestratorError, match="already started"):
        start_run(vault, TASK, NOW, run_id="ctx-duplicate")


def test_start_rejects_corpus_snapshot_over_launch_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mootloop.context as context_module

    vault = _vault(tmp_path)
    _add_corpus_doc(vault, "corpus body")
    monkeypatch.setattr(context_module, "MAX_CORPUS_SNAPSHOT_BYTES", 8)

    with pytest.raises(OrchestratorError, match="launch limit"):
        start_run(vault, TASK, NOW, run_id="ctx-corpus-quota")

    assert read_events(vault, "ctx-corpus-quota") == []


def test_start_rejects_retained_corpus_snapshot_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mootloop.context as context_module

    vault = _vault(tmp_path)
    first = start_run(vault, TASK, NOW, run_id="ctx-retained-first")
    first_size = (
        vault / "runs" / first / "context" / "corpus.json"
    ).stat().st_size
    monkeypatch.setattr(
        context_module,
        "MAX_RETAINED_CORPUS_SNAPSHOT_BYTES",
        first_size * 2 - 1,
    )

    with pytest.raises(OrchestratorError, match="retained run corpus snapshots"):
        start_run(vault, TASK, NOW, run_id="ctx-retained-second")

    assert read_events(vault, "ctx-retained-second") == []


def test_post_start_retry_override_must_match_launch_context(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id="ctx-retry-policy", max_attempts=5)

    assert plan_next(vault, run_id)
    assert plan_next(vault, run_id, max_attempts=5)
    with pytest.raises(OrchestratorError, match="launch-bound at 5; requested 3"):
        plan_next(vault, run_id, max_attempts=3)
    with pytest.raises(OrchestratorError, match="launch-bound at 5; requested 3"):
        run_with_provider(vault, run_id, FakeLLMProvider(), NOW, max_attempts=3)


def test_plan_replays_snapshotted_requests_and_facts_after_sources_change(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id="ctx-replay")

    save_requests(vault, _request_set("A changed request that belongs to a new run."))
    FactStore(vault).add_fact("A later fact that belongs to a new run.", confidence=1.0)

    spec = plan_next(vault, run_id)[0]
    assert spec.prompt_context["request_text"] == "Identify every person with contract knowledge."
    approved_context = spec.prompt_context["approved_context"]
    assert isinstance(approved_context, list)
    facts = [item for item in approved_context if item["kind"] == "fact"]
    assert [fact["text"] for fact in facts] == ["The original fact."]
    assert all(fact["provenance_locator"] for fact in facts)


def test_manifest_preserves_semantic_request_set_order(tmp_path: Path) -> None:
    from mootloop.context import load_run_context

    vault = _vault(tmp_path)
    save_requests(
        vault,
        RequestSet(
            request_type=RequestType.RFA,
            set_number=2,
            title="Admissions Set 2",
            items=[
                RequestItem(
                    request_id="RFA-1",  # type: ignore[arg-type]
                    set_number=2,
                    number=1,
                    text="Admit the agreement was signed.",
                    source_doc=DocId("doc-admissionsset02"),
                )
            ],
        ),
    )

    run_id = start_run(vault, TASK, NOW, run_id="ctx-request-order")
    request_sets = load_run_context(vault, run_id).manifest.request_sets

    assert [(item.set_number, item.request_type.value) for item in request_sets] == [
        (1, "interrogatory"),
        (2, "rfa"),
    ]


def test_plan_replays_snapshotted_python_adapter_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id="ctx-adapter")
    original = plan_next(vault, run_id)[0].prompt_context["directive"]
    monkeypatch.setattr(
        DiscoveryResponsesAdapter,
        "draft_directive",
        lambda self: "MUTATED DEPLOYMENT BEHAVIOR",
    )

    replayed = plan_next(vault, run_id)[0]
    assert replayed.prompt_context["directive"] == original
    assert "MUTATED" not in replayed.prompt_context["directive"]


def test_prompt_replays_launch_persona_body_after_deployment_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mootloop.resources as resources

    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id="ctx-persona")
    spec = plan_next(vault, run_id)[0]
    original = assemble_prompt(vault, run_id, spec.turn_id)
    replacement_dir = tmp_path / "replacement-personas"
    replacement_dir.mkdir()
    (replacement_dir / f"{spec.persona.body_slug}.md").write_text(
        "MUTATED DEPLOYMENT PERSONA BODY", encoding="utf-8"
    )
    monkeypatch.setattr(resources, "PERSONAS_DIR", replacement_dir)

    replayed = assemble_prompt(vault, run_id, spec.turn_id)

    assert replayed == original
    assert "MUTATED DEPLOYMENT" not in replayed


def test_fabrication_gate_replays_launch_corpus_after_content_changes(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    corpus_path = _add_corpus_doc(vault, "The agreed contract amount was $42,000.")
    run_id = start_run(vault, TASK, NOW, run_id="ctx-corpus")
    spec = plan_next(vault, run_id)[0]
    fact_id = FactStore(vault).get_current()[0].fact_id
    corpus_path.write_text("The later contract amount was $99,000.", encoding="utf-8")

    record = record_turn(
        vault,
        run_id,
        spec.turn_id,
        json.dumps(
            {
                "response_text": "The agreed contract amount was $42,000.",
                "objections": [],
                "candidate_citations": [],
                "fact_ids_used": [fact_id],
                "attorney_gate_items": [],
                "rfa_disposition": None,
                "self_assessment": "Grounded in the launch corpus.",
            }
        ),
        None,
        NOW,
    )

    fabrication_result = next(
        result for result in record.gate_results if result.gate == "fabrication"
    )
    assert fabrication_result.status == "pass"


def test_provider_result_is_rejected_if_context_changes_during_call(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id="ctx-provider-tamper")
    provider = FakeLLMProvider()
    real_run_turn = provider.run_turn

    def tamper_then_return(spec: object, prompt: str) -> object:
        _manifest_path(vault, run_id).write_text("{}\n", encoding="utf-8")
        return real_run_turn(spec, prompt)  # type: ignore[arg-type]

    provider.run_turn = tamper_then_return  # type: ignore[method-assign]

    with pytest.raises(OrchestratorError, match="context manifest.*(tampered|digest)"):
        run_with_provider(vault, run_id, provider, NOW)

    assert len(read_events(vault, run_id)) == 1


def test_manifest_source_digests_match_captured_bytes(tmp_path: Path) -> None:
    from mootloop.context import load_run_context, load_run_corpus
    from mootloop.resources import PERSONAS_DIR

    vault = _vault(tmp_path)
    corpus_path = _add_corpus_doc(vault, "Exact launch corpus bytes.\n")
    run_id = start_run(vault, TASK, NOW, run_id="ctx-source-digests")
    context = load_run_context(vault, run_id)
    sources = {(source.kind, source.locator): source.sha256 for source in context.manifest.sources}

    expected = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    corpus_locator = f"corpus/normalized/{DocId('doc-contextsnapshot')}.md"
    assert sources[("corpus_content", corpus_locator)] == expected
    snapshot = load_run_corpus(vault, context).documents[0]
    assert snapshot.sha256 == expected
    assert hashlib.sha256(snapshot.text.encode("utf-8")).hexdigest() == expected
    standard_raw = (PERSONAS_DIR / "_standard.md").read_bytes()
    associate_raw = (PERSONAS_DIR / "associate.md").read_bytes()
    assert context.manifest.persona_bodies[PersonaName.ASSOCIATE] == (
        standard_raw.decode().rstrip() + "\n\n" + associate_raw.decode().lstrip()
    )
    assert sources[("persona_body", "personas/_standard.md")] == hashlib.sha256(
        standard_raw
    ).hexdigest()
    assert sources[("persona_body", "personas/associate.md")] == hashlib.sha256(
        associate_raw
    ).hexdigest()


def test_lifecycle_fails_closed_when_corpus_snapshot_is_tampered(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _add_corpus_doc(vault, "Exact launch corpus bytes.\n")
    run_id = start_run(vault, TASK, NOW, run_id="ctx-corpus-tamper")
    snapshot = vault / "runs" / run_id / "context" / "corpus.json"
    snapshot.write_text("{}\n", encoding="utf-8")

    with pytest.raises(OrchestratorError, match="corpus snapshot.*(tampered|digest)"):
        plan_next(vault, run_id)

    assert len(read_events(vault, run_id)) == 1


def test_status_reports_unreadable_corpus_as_context_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mootloop.context as context_module
    from mootloop.web.api import readers

    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id="ctx-corpus-unreadable")
    monkeypatch.setattr(
        context_module,
        "_sha256_path",
        lambda _path: (_ for _ in ()).throw(OSError("media error")),
    )

    status = readers.run_status_summary(vault, run_id)

    assert status.replayable is False
    assert "corpus snapshot is unreadable" in (status.context_blocker or "")


@pytest.mark.parametrize("action", ["plan", "record", "resume"])
def test_lifecycle_fails_closed_when_manifest_is_tampered(
    tmp_path: Path, action: str
) -> None:
    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id=f"ctx-tamper-{action}")
    spec = plan_next(vault, run_id)[0]
    if action == "resume":
        from mootloop.orchestrator import pause_run

        pause_run(vault, run_id)
    _manifest_path(vault, run_id).write_text("{}\n", encoding="utf-8")

    with pytest.raises(OrchestratorError, match="context manifest.*(tampered|digest)"):
        if action == "plan":
            plan_next(vault, run_id)
        elif action == "record":
            record_turn(vault, run_id, spec.turn_id, "{}", None, NOW)
        else:
            resume_run(vault, run_id)


def test_plan_fails_closed_when_manifest_is_missing(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id="ctx-deleted")
    _manifest_path(vault, run_id).unlink()

    with pytest.raises(OrchestratorError, match="context manifest.*missing"):
        plan_next(vault, run_id)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "different-run"),
        ("matter_id", "different-matter"),
        ("task", "different-task"),
        ("rubric_version", "different-rubric"),
        ("config_digest", "0" * 16),
        ("mode", "gated"),
    ],
)
def test_loader_rejects_run_started_identity_drift(
    tmp_path: Path, field: str, value: str
) -> None:
    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id=f"ctx-event-{field}")
    journal = vault / "runs" / run_id / "journal.jsonl"
    lines = journal.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event[field] = value
    lines[0] = json.dumps(event, separators=(",", ":"))
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    clear_cache()

    with pytest.raises(OrchestratorError, match="identity does not match"):
        plan_next(vault, run_id)


def test_loader_rejects_run_started_task_spec_drift(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    spec = create_freeform(vault, "acme-v-widgets", "answer the discovery", NOW)
    lock_task_spec(
        vault, "acme-v-widgets", str(spec.task_spec_id), "test-attorney", NOW
    )
    run_id = start_run(
        vault, TASK, NOW, run_id="ctx-event-task-spec", task_spec_id=str(spec.task_spec_id)
    )
    journal = vault / "runs" / run_id / "journal.jsonl"
    event = json.loads(journal.read_text(encoding="utf-8"))
    event["task_spec_id"] = "taskspec-different"
    journal.write_text(json.dumps(event, separators=(",", ":")) + "\n", encoding="utf-8")
    clear_cache()

    with pytest.raises(OrchestratorError, match="TaskSpec does not match"):
        plan_next(vault, run_id)


@pytest.mark.parametrize("target", ["corpus.json", "manifest.json"])
def test_start_rejects_conflicting_preexisting_context_bytes(
    tmp_path: Path, target: str
) -> None:
    vault = _vault(tmp_path)
    context_dir = vault / "runs" / f"ctx-conflict-{target.removesuffix('.json')}" / "context"
    context_dir.mkdir(parents=True)
    existing = context_dir / target
    existing.write_text("conflicting bytes\n", encoding="utf-8")
    before = existing.read_bytes()

    with pytest.raises(OrchestratorError, match="already has a different|refusing overwrite"):
        start_run(
            vault,
            TASK,
            NOW,
            run_id=f"ctx-conflict-{target.removesuffix('.json')}",
        )

    assert existing.read_bytes() == before
    assert not (context_dir.parent / "journal.jsonl").exists()


def test_derived_api_and_demo_views_replay_launch_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mootloop import gate_ledger
    from mootloop.web import app as demo
    from mootloop.web.api import readers

    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id="ctx-derived")
    save_requests(vault, _replacement_request_set())
    matter = yaml.safe_load((vault / "matter.yaml").read_text(encoding="utf-8"))
    matter["budget"]["hard_cap_usd"] = 1.0
    (vault / "matter.yaml").write_text(yaml.safe_dump(matter), encoding="utf-8")

    assert [str(unit.request_id) for unit in readers.requests_response(vault, run_id).requests] == [
        "ROG-1"
    ]
    assert readers.run_status_summary(vault, run_id).hard_cap_usd is None
    assert set(gate_ledger.build_ledger(vault, run_id).gates) == {"ROG-1"}

    monkeypatch.setenv(demo.VAULT_ENV, str(vault))
    assert [row["request_id"] for row in demo.api_requests()] == ["ROG-1"]
    assert demo.api_sets() == [
        {
            "request_type": "interrogatory",
            "set_number": 1,
            "title": "Interrogatories Set 1",
            "requests": 1,
        }
    ]
    assert demo.api_run()["stages"]
    with pytest.raises(HTTPException) as exc:
        demo.api_request_turns("ROG-2")
    assert exc.value.status_code == 404


def test_observed_status_and_panel_report_replay_launch_inputs(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id="ctx-derived-artifacts", mode="observed")
    save_requests(vault, _replacement_request_set())
    FactStore(vault).add_fact("A later fact that belongs to a new run.", confidence=1.0)

    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)

    status = (vault / "runs" / run_id / "STATUS.md").read_text(encoding="utf-8")
    assert "`ROG-1`" in status
    assert "`ROG-2`" not in status
    status_sidecar = RunStatusSidecar.model_validate_json(
        (vault / "runs" / run_id / "STATUS.json").read_text(encoding="utf-8")
    )
    assert status_sidecar.run_id == run_id
    assert status_sidecar.human_view_sha256 == hashlib.sha256(status.encode()).hexdigest()
    report = build_panel_report(vault, run_id)
    assert {str(result.request_id) for result in report.results} == {"ROG-1"}


def test_export_replays_launch_requests_and_matter(tmp_path: Path) -> None:
    from mootloop.export.service import export_run

    vault = _vault(tmp_path)
    _add_corpus_doc(vault, "Privileged launch document.", privileged=True)
    original_case_number = make_matter().caption.case_number
    run_id = start_run(vault, TASK, NOW, run_id="ctx-export")

    save_requests(vault, _replacement_request_set())
    matter = yaml.safe_load((vault / "matter.yaml").read_text(encoding="utf-8"))
    matter["caption"]["case_number"] = "66-CV-26-9999"
    (vault / "matter.yaml").write_text(yaml.safe_dump(matter), encoding="utf-8")
    Manifest.load(vault).model_copy(
        update={
            "docs": [
                Manifest.load(vault).docs[0].model_copy(update={"privileged": False})
            ]
        }
    ).save(vault)

    result = export_run(vault, run_id, NOW)
    master = result.master.read_text(encoding="utf-8")
    set_master = result.set_masters[0].read_text(encoding="utf-8")
    for exported in (master, set_master):
        assert "Identify every person with contract knowledge." in exported
        assert "A changed request that belongs to a new run." not in exported
        assert original_case_number in exported
        assert "66-CV-26-9999" not in exported
    assert "`doc-contextsnapshot`" in result.privilege_log.read_text(encoding="utf-8")
