"""Deterministic five-layer run-configuration resolution."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ValidationError

from mootloop.errors import ConfigResolutionError
from mootloop.models.config import (
    IMMUTABLE_IDENTITY_PATHS,
    STRUCTURAL_LEAF_PATHS,
    ConfigLayer,
    ConfigSource,
    DefaultRunConfig,
    FirmPreferences,
    ResolvedRunConfig,
    RunConfigOverlay,
)
from mootloop.models.task import TaskAdapterConfig
from mootloop.resources import DEFAULTS_CONFIG

_ABSENT_DIGEST = hashlib.sha256(b"").hexdigest()
_MISSING = object()


@dataclass(frozen=True)
class ConfigLayerInput:
    """Immutable source bytes plus the locator those bytes came from."""

    locator: str
    content: bytes

    @classmethod
    def from_path(cls, path: Path | str) -> ConfigLayerInput:
        source = Path(path)
        try:
            return cls(locator=str(source), content=source.read_bytes())
        except OSError as exc:
            raise ConfigResolutionError(
                f"config source {source!s} could not be read: {exc}"
            ) from exc

    @classmethod
    def from_mapping(cls, locator: str, content: Mapping[str, object]) -> ConfigLayerInput:
        canonical = json.dumps(
            content,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return cls(locator=locator, content=canonical)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def default_config_layer() -> ConfigLayerInput:
    """Load the packaged lowest-precedence defaults artifact."""
    return ConfigLayerInput.from_path(DEFAULTS_CONFIG)


def _parse_mapping(source: ConfigLayerInput, layer: ConfigLayer) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(source.content)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigResolutionError(
            f"{layer} source {source.locator!r} is not valid YAML: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigResolutionError(
            f"{layer} source {source.locator!r} at <root> must be a mapping"
        )
    return {str(key): value for key, value in raw.items()}


def _validation_path(error: Mapping[str, object]) -> str:
    location = cast(tuple[str | int, ...], error["loc"])
    return ".".join(str(part) for part in location) or "<root>"


def _validate_model[ModelT: BaseModel](
    model_type: type[ModelT],
    raw: Mapping[str, Any],
    source: ConfigLayerInput,
    layer: ConfigLayer,
) -> ModelT:
    try:
        return model_type.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        path = _validation_path(first)
        raise ConfigResolutionError(
            f"{layer} source {source.locator!r} invalid at {path}: {first['msg']}"
        ) from exc


def _deep_merge(base: dict[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in incoming.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _leaf_paths(content: Mapping[str, Any], prefix: str = "") -> list[str]:
    leaves: list[str] = []
    for key, value in content.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            leaves.extend(_leaf_paths(value, path))
        else:
            leaves.append(path)
    return leaves


def _value_at(content: Mapping[str, Any], path: str) -> object:
    current: object = content
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _same_value(left: object, right: object) -> bool:
    if left is _MISSING or right is _MISSING:
        return left is right
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def _enforce_structural_policy(
    current: Mapping[str, Any],
    incoming: Mapping[str, Any],
    allowed: frozenset[str],
    layer: ConfigLayer,
) -> None:
    for path in _leaf_paths(incoming):
        if path in IMMUTABLE_IDENTITY_PATHS:
            raise ConfigResolutionError(
                f"{layer} source attempted immutable identity path {path!r}"
            )
        if path not in STRUCTURAL_LEAF_PATHS:
            continue
        if _same_value(_value_at(current, path), _value_at(incoming, path)):
            continue
        if path not in allowed:
            raise ConfigResolutionError(
                f"{layer} source cannot change structural path {path!r}; "
                "the task adapter did not allowlist that exact leaf"
            )


def _source(layer: ConfigLayer, value: ConfigLayerInput | None) -> ConfigSource:
    if value is None:
        return ConfigSource(
            layer=layer,
            locator=f"absent:{layer}",
            sha256=_ABSENT_DIGEST,
            present=False,
        )
    return ConfigSource(
        layer=layer,
        locator=value.locator,
        sha256=value.sha256,
        present=True,
    )


def _model_content(model: BaseModel, *excluded: str) -> dict[str, Any]:
    return model.model_dump(exclude=set(excluded), exclude_unset=False)


def _overlay_content(model: RunConfigOverlay) -> dict[str, Any]:
    return model.model_dump(exclude_unset=True)


def resolve_run_config(
    *,
    defaults: ConfigLayerInput,
    adapter: ConfigLayerInput,
    legacy_fallback: RunConfigOverlay | None = None,
    firm_preferences: ConfigLayerInput | None = None,
    matter_overlay: ConfigLayerInput | None = None,
    matter_provenance: ConfigLayerInput | None = None,
    invocation_flags: ConfigLayerInput | None = None,
) -> ResolvedRunConfig:
    """Resolve defaults → adapter → firm → matter → flags into one frozen value."""

    defaults_raw = _parse_mapping(defaults, "defaults")
    defaults_model = _validate_model(DefaultRunConfig, defaults_raw, defaults, "defaults")
    merged = _model_content(defaults_model, "schema_version")
    # Compatibility-only values from the legacy top-level MatterConfig live below
    # every authored layer. This preserves old matter behavior without allowing
    # Pydantic-populated run_mode/budget defaults to shadow firm preferences.
    if legacy_fallback is not None:
        merged = _deep_merge(merged, _overlay_content(legacy_fallback))

    adapter_raw = _parse_mapping(adapter, "task_adapter")
    adapter_model = _validate_model(TaskAdapterConfig, adapter_raw, adapter, "task_adapter")
    adapter_content = adapter_model.model_dump(
        exclude={"schema_version", "overridable"}, exclude_unset=True
    )
    # Layer two owns the task shape and may always replace defaults.
    merged = _deep_merge(merged, adapter_content)
    allowed = frozenset(adapter_model.overridable)

    if firm_preferences is not None:
        firm_raw = _parse_mapping(firm_preferences, "firm_preferences")
        firm_model = _validate_model(
            FirmPreferences, firm_raw, firm_preferences, "firm_preferences"
        )
        firm_content = _overlay_content(firm_model.run_config)
        _enforce_structural_policy(merged, firm_content, allowed, "firm_preferences")
        merged = _deep_merge(merged, firm_content)

    higher_layers: tuple[tuple[ConfigLayer, ConfigLayerInput | None], ...] = (
        ("matter_overlay", matter_overlay),
        ("invocation_flags", invocation_flags),
    )
    for layer, source in higher_layers:
        if source is None:
            continue
        raw = _parse_mapping(source, layer)
        overlay_model = _validate_model(RunConfigOverlay, raw, source, layer)
        content = _overlay_content(overlay_model)
        _enforce_structural_policy(merged, content, allowed, layer)
        merged = _deep_merge(merged, content)

    merged["schema_version"] = "1.0"
    merged["overridable_structural_paths"] = tuple(adapter_model.overridable)
    merged["sources"] = (
        _source("defaults", defaults),
        _source("task_adapter", adapter),
        _source("firm_preferences", firm_preferences),
        _source("matter_overlay", matter_provenance or matter_overlay),
        _source("invocation_flags", invocation_flags),
    )
    try:
        return ResolvedRunConfig.model_validate(merged)
    except ValidationError as exc:
        first = exc.errors()[0]
        path = _validation_path(first)
        raise ConfigResolutionError(
            f"resolved_config source invalid at {path}: {first['msg']}"
        ) from exc
