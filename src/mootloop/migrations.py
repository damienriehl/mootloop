"""Pure, explicit migrations for persisted ``VersionedModel`` payloads."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import ValidationError

from mootloop.errors import MigrationError
from mootloop.models.common import VersionedModel

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
                raise MigrationError(
                    f"{model_name} migration from {version} must return a mapping"
                )
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
