"""Pure, fail-closed migrations for persisted versioned models."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from mootloop.errors import MigrationError, OrchestratorError
from mootloop.migrations import MigrationRegistry, load_versioned_json
from mootloop.models.common import VersionedModel
from mootloop.models.context import RunContextManifest
from mootloop.models.events import RunStarted
from mootloop.models.run import PersonaName
from mootloop.orchestrator import start_run
from mootloop.vault import init_vault
from tests.conftest import make_matter


class ExampleRecord(VersionedModel):
    schema_version: str = "3.0"
    label: str
    tags: list[str]
    count: int


class OtherRecord(VersionedModel):
    schema_version: str = "3.0"
    label: str


def _upgrade_1_to_2(payload: dict[str, Any]) -> dict[str, Any]:
    payload["schema_version"] = "2.0"
    payload["tags"].append("migrated")
    return payload


def _upgrade_2_to_3(payload: dict[str, Any]) -> dict[str, Any]:
    payload["schema_version"] = "3.0"
    payload["count"] = len(payload["tags"])
    return payload


def test_registry_chains_one_step_migrations_without_mutating_input() -> None:
    registry = MigrationRegistry()
    registry.register(ExampleRecord, "1.0", "2.0", _upgrade_1_to_2)
    registry.register(ExampleRecord, "2.0", "3.0", _upgrade_2_to_3)
    source: dict[str, Any] = {
        "schema_version": "1.0",
        "label": "original",
        "tags": ["source"],
    }
    untouched = deepcopy(source)

    record = registry.migrate_and_validate(ExampleRecord, source, current_version="3.0")

    assert source == untouched
    assert record == ExampleRecord(label="original", tags=["source", "migrated"], count=2)
    assert record.model_dump()["schema_version"] == "3.0"


def test_registry_keys_steps_by_model_type_and_source_version() -> None:
    registry = MigrationRegistry()
    registry.register(ExampleRecord, "1.0", "3.0", _upgrade_2_to_3)

    with pytest.raises(MigrationError, match="OtherRecord.*1.0.*3.0"):
        registry.migrate_and_validate(
            OtherRecord,
            {"schema_version": "1.0", "label": "source"},
            current_version="3.0",
        )


def test_registry_rejects_duplicate_model_and_source_registration() -> None:
    registry = MigrationRegistry()
    registry.register(ExampleRecord, "1.0", "2.0", _upgrade_1_to_2)

    with pytest.raises(MigrationError, match="duplicate.*ExampleRecord.*1.0"):
        registry.register(ExampleRecord, "1.0", "3.0", _upgrade_2_to_3)


def test_registry_fails_when_a_chain_step_is_missing() -> None:
    registry = MigrationRegistry()
    registry.register(ExampleRecord, "1.0", "2.0", _upgrade_1_to_2)

    with pytest.raises(MigrationError, match="ExampleRecord.*2.0.*3.0"):
        registry.migrate_and_validate(
            ExampleRecord,
            {"schema_version": "1.0", "label": "source", "tags": []},
            current_version="3.0",
        )


def test_registry_rejects_unknown_future_version() -> None:
    with pytest.raises(MigrationError, match="future.*4.0.*3.0"):
        MigrationRegistry().migrate_and_validate(
            ExampleRecord,
            {
                "schema_version": "4.0",
                "label": "future",
                "tags": [],
                "count": 0,
            },
            current_version="3.0",
        )


@pytest.mark.parametrize(
    ("migration", "message"),
    [
        (lambda _payload: [], "mapping"),
        (
            lambda payload: {**payload, "schema_version": "9.0"},
            "declared target.*2.0",
        ),
        (
            lambda payload: {"schema_version": "2.0", "tags": payload["tags"]},
            "failed validation",
        ),
    ],
)
def test_registry_rejects_invalid_migration_output(
    migration: Any, message: str
) -> None:
    registry = MigrationRegistry()
    registry.register(ExampleRecord, "1.0", "2.0", migration)
    registry.register(ExampleRecord, "2.0", "3.0", _upgrade_2_to_3)

    with pytest.raises(MigrationError, match=message):
        registry.migrate_and_validate(
            ExampleRecord,
            {"schema_version": "1.0", "label": "source", "tags": []},
            current_version="3.0",
        )


def test_load_versioned_json_migrates_in_memory_and_leaves_source_bytes_unchanged() -> None:
    registry = MigrationRegistry()
    registry.register(ExampleRecord, "1.0", "2.0", _upgrade_1_to_2)
    registry.register(ExampleRecord, "2.0", "3.0", _upgrade_2_to_3)
    raw = b'{"schema_version":"1.0","label":"source","tags":[]}'
    original = bytes(raw)

    record = load_versioned_json(
        raw,
        ExampleRecord,
        current_version="3.0",
        registry=registry,
    )

    assert record.schema_version == "3.0"
    assert raw == original


def test_run_context_rejects_raw_digest_before_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mootloop.context as context_module

    vault = tmp_path / "vault"
    context_dir = vault / "runs" / "digest-order" / "context"
    context_dir.mkdir(parents=True)
    raw = b'{"schema_version":"0.9"}\n'
    (context_dir / "manifest.json").write_bytes(raw)
    journal = vault / "runs" / "digest-order" / "journal.jsonl"
    event = {
        "kind": "run_started",
        "run_id": "digest-order",
        "matter_id": "matter",
        "task": "task",
        "rubric_version": "rubric",
        "config_digest": "digest",
        "context_manifest_sha256": hashlib.sha256(b"different bytes").hexdigest(),
        "mode": "autonomous",
        "task_spec_id": None,
    }
    journal.write_text(json.dumps(event) + "\n", encoding="utf-8")
    called = False

    def forbidden_migration(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("migration must not run before the raw digest is verified")

    monkeypatch.setattr(context_module, "load_versioned_json", forbidden_migration)

    with pytest.raises(OrchestratorError, match="manifest.*(tampered|digest)"):
        context_module.load_run_context(vault, "digest-order")
    assert called is False


def test_run_context_v1_0_migrates_from_captured_fields_without_rewriting(
    tmp_path: Path,
) -> None:
    from mootloop.context import load_run_context
    from mootloop.journal import clear_cache, read_events

    vault = tmp_path / "vault"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
    run_id = start_run(vault, "discovery-responses", "2026-07-11T00:00:00+00:00")
    manifest_path = vault / "runs" / run_id / "context" / "manifest.json"
    payload = json.loads(manifest_path.read_bytes())
    payload["schema_version"] = "1.0"
    payload.pop("resolved_config")
    payload.pop("pipeline")
    legacy_adapter = payload["adapter_config"]
    legacy_adapter.pop("overridable")
    legacy_adapter.pop("pipeline_strategies")
    legacy_raw = (json.dumps(payload, indent=2) + "\n").encode()
    manifest_path.write_bytes(legacy_raw)

    started = next(event for event in read_events(vault, run_id) if isinstance(event, RunStarted))
    legacy_config_digest = hashlib.sha256(
        json.dumps(legacy_adapter, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    journal_path = vault / "runs" / run_id / "journal.jsonl"
    event_payload = started.model_dump(mode="json")
    event_payload["config_digest"] = legacy_config_digest
    event_payload["context_manifest_sha256"] = hashlib.sha256(legacy_raw).hexdigest()
    journal_path.write_text(
        json.dumps(event_payload, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    clear_cache()

    migrated = load_run_context(vault, run_id)

    assert manifest_path.read_bytes() == legacy_raw
    assert migrated.manifest.schema_version == "1.4"
    assert migrated.manifest.task_spec_lock is None
    assert migrated.manifest.context_contributions == []
    assert migrated.manifest.context_exclusions == []
    assert migrated.manifest.resolved_config.task == "discovery-responses"
    assert migrated.manifest.resolved_config.run_mode == started.mode
    assert migrated.manifest.resolved_config.max_attempts == 3
    assert migrated.manifest.pipeline.strategy == "thin-full"
    assert migrated.manifest.pipeline.oc_personas == (PersonaName.OC_ASSOCIATE,)
    assert migrated.binding.config == migrated.manifest.pipeline.effective_config
    assert migrated.binding.config.loop_caps.oc == 1
    assert migrated.binding.config.pipeline_strategies == {}


def test_run_context_v1_1_adds_only_empty_context_capture_fields(tmp_path: Path) -> None:
    vault = tmp_path / "vault-v11"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries-v11.json")
    run_id = start_run(vault, "discovery-responses", "2026-07-11T00:00:00+00:00")
    manifest_path = vault / "runs" / run_id / "context" / "manifest.json"
    payload = json.loads(manifest_path.read_bytes())
    payload["schema_version"] = "1.1"
    payload.pop("context_contributions")
    payload.pop("context_exclusions")
    raw = json.dumps(payload, separators=(",", ":")).encode()
    untouched = bytes(raw)

    migrated = load_versioned_json(
        raw,
        RunContextManifest,
        current_version="1.2",
    )

    assert raw == untouched
    assert migrated.schema_version == "1.2"
    assert migrated.context_contributions == []
    assert migrated.context_exclusions == []
