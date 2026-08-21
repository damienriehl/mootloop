"""Pure, explicit migrations for persisted ``VersionedModel`` payloads."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import ValidationError

from mootloop.errors import MigrationError
from mootloop.models.common import VersionedModel, canonical_json_sha256
from mootloop.models.context import RunContextManifest
from mootloop.models.task import TaskAdapterConfig
from mootloop.pipeline import legacy_fixed_pipeline

MigrationPayload = dict[str, Any]
MigrationFunction = Callable[[MigrationPayload], object]
ModelT = TypeVar("ModelT", bound=VersionedModel)
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")


@dataclass(frozen=True)
class _MigrationStep:
    target_version: str
    migrate: MigrationFunction


def _version_key(version: str, *, model_name: str) -> tuple[int, ...]:
    if not _VERSION_RE.fullmatch(version):
        raise MigrationError(
            f"{model_name} has invalid schema version {version!r}; expected numeric components"
        )
    return tuple(int(part) for part in version.split("."))


class MigrationRegistry:
    """Registry of pure, one-step migrations keyed by model type and source version."""

    def __init__(self) -> None:
        self._steps: dict[tuple[type[VersionedModel], str], _MigrationStep] = {}

    def register(
        self,
        model_type: type[VersionedModel],
        source_version: str,
        target_version: str,
        migration: MigrationFunction,
    ) -> None:
        """Register one explicit source-to-target step, rejecting ambiguous routes."""
        model_name = model_type.__name__
        source_key = _version_key(source_version, model_name=model_name)
        target_key = _version_key(target_version, model_name=model_name)
        if target_key <= source_key:
            raise MigrationError(
                f"{model_name} migration {source_version} -> {target_version} must advance"
            )
        key = (model_type, source_version)
        if key in self._steps:
            raise MigrationError(
                f"duplicate migration for {model_name} schema version {source_version}"
            )
        self._steps[key] = _MigrationStep(target_version, migration)

    def migrate_and_validate(
        self,
        model_type: type[ModelT],
        payload: Mapping[str, Any],
        *,
        current_version: str,
    ) -> ModelT:
        """Migrate an isolated copy through every registered step, then validate it."""
        model_name = model_type.__name__
        current_key = _version_key(current_version, model_name=model_name)
        working = deepcopy(dict(payload))
        source_version = working.get("schema_version")
        if not isinstance(source_version, str):
            raise MigrationError(f"{model_name} is missing a string schema_version")
        source_key = _version_key(source_version, model_name=model_name)
        if source_key > current_key:
            raise MigrationError(
                f"{model_name} has unknown future schema version {source_version}; "
                f"current version is {current_version}"
            )

        version = source_version
        visited: set[str] = set()
        while version != current_version:
            if version in visited:
                raise MigrationError(f"{model_name} migration chain cycles at {version}")
            visited.add(version)
            step = self._steps.get((model_type, version))
            if step is None:
                raise MigrationError(
                    f"{model_name} has no migration from {version} to current version "
                    f"{current_version}"
                )
            result = step.migrate(deepcopy(working))
            if not isinstance(result, Mapping):
                raise MigrationError(f"{model_name} migration from {version} must return a mapping")
            working = deepcopy(dict(result))
            actual_target = working.get("schema_version")
            if actual_target != step.target_version:
                raise MigrationError(
                    f"{model_name} migration from {version} did not emit its declared target "
                    f"schema version {step.target_version}"
                )
            version = step.target_version
            if _version_key(version, model_name=model_name) > current_key:
                raise MigrationError(
                    f"{model_name} migration chain advanced past current version "
                    f"{current_version} to {version}"
                )

        try:
            model = model_type.model_validate(working)
        except ValidationError as exc:
            raise MigrationError(
                f"{model_name} schema version {current_version} failed validation: {exc}"
            ) from exc
        if model.schema_version != current_version:
            raise MigrationError(
                f"{model_name} validated with schema version {model.schema_version}; "
                f"expected {current_version}"
            )
        return model


DEFAULT_MIGRATIONS = MigrationRegistry()


def _legacy_context_source(
    payload: Mapping[str, Any],
    kind: str,
    *,
    fallback_locator: str,
    fallback_value: object,
) -> tuple[str, str]:
    sources = payload.get("sources")
    if isinstance(sources, list):
        for source in sources:
            if isinstance(source, Mapping) and source.get("kind") == kind:
                locator = source.get("locator")
                digest = source.get("sha256")
                if isinstance(locator, str) and isinstance(digest, str):
                    return locator, digest
    return fallback_locator, canonical_json_sha256(fallback_value)


def _migrate_run_context_1_0_to_1_1(payload: MigrationPayload) -> MigrationPayload:
    """Reconstruct the v1.0 effective config solely from its captured launch fields."""
    adapter = payload.get("adapter_config")
    matter = payload.get("matter_config")
    if not isinstance(adapter, Mapping) or not isinstance(matter, Mapping):
        return {**payload, "schema_version": "1.1"}
    budget = matter.get("budget")
    if not isinstance(budget, Mapping):
        budget = {}
    adapter_locator, adapter_digest = _legacy_context_source(
        payload,
        "task_adapter",
        fallback_locator="migration:v1.0:adapter-snapshot",
        fallback_value=adapter,
    )
    matter_locator, matter_digest = _legacy_context_source(
        payload,
        "matter_config",
        fallback_locator="migration:v1.0:matter-snapshot",
        fallback_value=matter,
    )
    unavailable_digest = hashlib.sha256(b"").hexdigest()
    effective_fields = {
        "run_mode": payload.get("effective_mode"),
        "max_attempts": payload.get("max_attempts"),
    }
    resolved_config = {
        "schema_version": "1.0",
        "task": adapter.get("task"),
        "stages": adapter.get("stages"),
        "loop_caps": adapter.get("loop_caps"),
        "panels": adapter.get("panels"),
        "convergence": adapter.get("convergence"),
        "gates": adapter.get("gates"),
        "rubric_id": adapter.get("rubric_id"),
        "rubric_threshold": adapter.get("rubric_threshold"),
        "restructure_threshold": adapter.get("restructure_threshold"),
        "deliverables": adapter.get("deliverables"),
        "run_mode": payload.get("effective_mode"),
        "max_attempts": payload.get("max_attempts"),
        "budget": {
            "tier": budget.get("tier"),
            "hard_cap_usd": budget.get("hard_cap_usd"),
        },
        "overridable_structural_paths": adapter.get("overridable", []),
        "sources": [
            {
                "layer": "defaults",
                "locator": "unavailable:migrated-v1.0-defaults",
                "sha256": unavailable_digest,
                "present": False,
            },
            {
                "layer": "task_adapter",
                "locator": adapter_locator,
                "sha256": adapter_digest,
                "present": True,
            },
            {
                "layer": "firm_preferences",
                "locator": "absent:firm_preferences",
                "sha256": unavailable_digest,
                "present": False,
            },
            {
                "layer": "matter_overlay",
                "locator": matter_locator,
                "sha256": matter_digest,
                "present": False,
            },
            {
                "layer": "invocation_flags",
                "locator": "migration:v1.0:effective-run-fields",
                "sha256": canonical_json_sha256(effective_fields),
                "present": True,
            },
        ],
    }
    migrated = deepcopy(payload)
    migrated["schema_version"] = "1.1"
    migrated["resolved_config"] = resolved_config
    return migrated


DEFAULT_MIGRATIONS.register(
    RunContextManifest,
    "1.0",
    "1.1",
    _migrate_run_context_1_0_to_1_1,
)


def _migrate_run_context_1_1_to_1_2(payload: MigrationPayload) -> MigrationPayload:
    """Represent newly supported contribution inputs as absent on historical runs."""
    migrated = deepcopy(payload)
    migrated["schema_version"] = "1.2"
    migrated["context_contributions"] = []
    migrated["context_exclusions"] = []
    return migrated


DEFAULT_MIGRATIONS.register(
    RunContextManifest,
    "1.1",
    "1.2",
    _migrate_run_context_1_1_to_1_2,
)


def _migrate_run_context_1_2_to_1_3(payload: MigrationPayload) -> MigrationPayload:
    """Historical launches predate human TaskSpec locks; never synthesize approval."""
    migrated = deepcopy(payload)
    migrated["schema_version"] = "1.3"
    migrated["task_spec_lock"] = None
    return migrated


DEFAULT_MIGRATIONS.register(
    RunContextManifest,
    "1.2",
    "1.3",
    _migrate_run_context_1_2_to_1_3,
)


def _migrate_run_context_1_3_to_1_4(payload: MigrationPayload) -> MigrationPayload:
    """Capture the exact fixed graph historical runs actually executed."""
    migrated = deepcopy(payload)
    migrated["schema_version"] = "1.4"
    adapter = migrated.get("adapter_config")
    if not isinstance(adapter, Mapping):
        return migrated
    matter_locator, matter_digest = _legacy_context_source(
        payload,
        "matter_config",
        fallback_locator="migration:v1.3:matter-snapshot",
        fallback_value=payload.get("matter_config"),
    )
    adapter_locator, adapter_digest = _legacy_context_source(
        payload,
        "task_adapter",
        fallback_locator="migration:v1.3:adapter-snapshot",
        fallback_value=adapter,
    )
    try:
        adapter_model = TaskAdapterConfig.model_validate(adapter)
    except ValidationError:
        return migrated
    migrated["pipeline"] = legacy_fixed_pipeline(
        adapter_model,
        matter_locator=matter_locator,
        matter_sha256=matter_digest,
        adapter_locator=adapter_locator,
        adapter_sha256=adapter_digest,
    ).model_dump(mode="json")
    return migrated


DEFAULT_MIGRATIONS.register(
    RunContextManifest,
    "1.3",
    "1.4",
    _migrate_run_context_1_3_to_1_4,
)


def _migrate_run_context_1_4_to_1_5(payload: MigrationPayload) -> MigrationPayload:
    """Keep historical prompt contributions matter-scoped with no wall exclusions."""
    migrated = deepcopy(payload)
    migrated["schema_version"] = "1.5"
    contributions = migrated.get("context_contributions")
    if isinstance(contributions, list):
        for contribution in contributions:
            if isinstance(contribution, dict):
                contribution.setdefault("sharing_scope", "matter")
                contribution.setdefault("excluded_matter_ids", [])
    return migrated


DEFAULT_MIGRATIONS.register(
    RunContextManifest,
    "1.4",
    "1.5",
    _migrate_run_context_1_4_to_1_5,
)


def load_versioned_json[JsonModelT: VersionedModel](
    raw: bytes,
    model_type: type[JsonModelT],
    *,
    current_version: str,
    registry: MigrationRegistry = DEFAULT_MIGRATIONS,
) -> JsonModelT:
    """Parse and migrate versioned JSON entirely in memory; never rewrite its source."""
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"{model_type.__name__} contains invalid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise MigrationError(f"{model_type.__name__} JSON root must be a mapping")
    return registry.migrate_and_validate(
        model_type,
        payload,
        current_version=current_version,
    )
