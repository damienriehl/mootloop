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
