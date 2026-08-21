"""Five-layer run-configuration resolution and structural override policy."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mootloop.config import ConfigLayerInput, default_config_layer, resolve_run_config
from mootloop.errors import ConfigResolutionError
from mootloop.models.config import ResolvedRunConfig, RunConfigOverlay
from mootloop.resources import task_config_path
from tests.conftest import make_matter


def _defaults(**overrides: object) -> ConfigLayerInput:
    content: dict[str, object] = {
        "schema_version": "1.0",
        "run_mode": "autonomous",
        "max_attempts": 3,
        "budget": {"tier": "moderate"},
        "loop_caps": {
            "associate_partner": 1,
            "oc": 1,
            "bolster": 1,
            "restructure": 1,
        },
        "panels": {"judges": 3, "jury": False, "rubric_judges": 3},
        "convergence": {
            "score_delta_floor": 0.02,
            "material_change_floor": 0.10,
            "coverage_floor": 0.80,
        },
        "rubric_threshold": 0.75,
        "restructure_threshold": 0.5,
    }
    content.update(overrides)
    return ConfigLayerInput.from_mapping("config/defaults.yaml", content)


def _adapter(
    *,
    associate_partner: int = 2,
    judges: int = 3,
    overridable: list[str] | None = None,
) -> ConfigLayerInput:
    return ConfigLayerInput.from_mapping(
        "config/tasks/example.yaml",
        {
            "schema_version": "1.0",
            "task": "example",
            "stages": ["draft", "judge", "assemble"],
            "loop_caps": {"associate_partner": associate_partner},
            "panels": {"judges": judges},
            "gates": ["completeness", "rubric"],
            "rubric_id": "example-v1.0",
            "deliverables": ["draft.md"],
            "overridable": overridable or [],
        },
    )


def _firm(run_config: dict[str, object]) -> ConfigLayerInput:
    return ConfigLayerInput.from_mapping(
        "/outside/firm/preferences.yaml",
        {"schema_version": "1.0", "run_config": run_config},
    )


def _overlay(layer: str, content: dict[str, object]) -> ConfigLayerInput:
    return ConfigLayerInput.from_mapping(layer, content)


@pytest.mark.parametrize(
    ("firm_value", "matter_value", "flag_value", "expected"),
    [
        (3, 4, 5, 5),
        (3, 4, None, 4),
        (3, None, None, 3),
        (None, None, None, 2),
    ],
)
def test_successive_precedence_fallbacks(
    firm_value: int | None,
    matter_value: int | None,
    flag_value: int | None,
    expected: int,
) -> None:
    resolved = resolve_run_config(
        defaults=_defaults(),
        adapter=_adapter(),
        firm_preferences=(
            _firm({"loop_caps": {"associate_partner": firm_value}})
            if firm_value is not None
            else None
        ),
        matter_overlay=(
            _overlay("matter.yaml#run_config", {"loop_caps": {"associate_partner": matter_value}})
            if matter_value is not None
            else None
        ),
        invocation_flags=(
            _overlay("invocation flags", {"loop_caps": {"associate_partner": flag_value}})
            if flag_value is not None
            else None
        ),
    )

    assert resolved.loop_caps.associate_partner == expected


def test_value_absent_from_all_higher_layers_falls_back_to_defaults() -> None:
    resolved = resolve_run_config(defaults=_defaults(), adapter=_adapter())

    assert resolved.run_mode == "autonomous"
    assert resolved.max_attempts == 3


def test_deep_merge_preserves_untouched_nested_siblings() -> None:
    resolved = resolve_run_config(
        defaults=_defaults(),
        adapter=_adapter(),
        firm_preferences=_firm({"loop_caps": {"associate_partner": 4}}),
    )

    assert resolved.loop_caps.associate_partner == 4
    assert resolved.loop_caps.oc == 1
    assert resolved.loop_caps.bolster == 1
    assert resolved.loop_caps.restructure == 1


def test_adapter_can_override_structural_defaults_without_allowlist() -> None:
    resolved = resolve_run_config(
        defaults=_defaults(panels={"judges": 1, "jury": False, "rubric_judges": 1}),
        adapter=_adapter(judges=3),
    )

    assert resolved.panels.judges == 3


def test_adapter_omissions_preserve_canonical_defaults() -> None:
    resolved = resolve_run_config(
        defaults=_defaults(
            loop_caps={
                "associate_partner": 1,
                "oc": 7,
                "bolster": 6,
                "restructure": 5,
            }
        ),
        adapter=_adapter(associate_partner=2),
    )

    assert resolved.loop_caps.associate_partner == 2
    assert resolved.loop_caps.oc == 7
    assert resolved.loop_caps.bolster == 6
    assert resolved.loop_caps.restructure == 5


def test_higher_layer_structural_change_is_denied_without_allowlist() -> None:
    with pytest.raises(ConfigResolutionError) as exc:
        resolve_run_config(
            defaults=_defaults(),
            adapter=_adapter(),
            matter_overlay=_overlay("matter.yaml#run_config", {"panels": {"judges": 5}}),
        )

    assert "matter" in str(exc.value)
    assert "panels.judges" in str(exc.value)


def test_exactly_allowlisted_structural_leaf_can_change() -> None:
    resolved = resolve_run_config(
        defaults=_defaults(),
        adapter=_adapter(overridable=["panels.judges"]),
        matter_overlay=_overlay("matter.yaml#run_config", {"panels": {"judges": 5}}),
    )

    assert resolved.panels.judges == 5


@pytest.mark.parametrize(
    "path",
    [
        "panels",
        "panels.*",
        "panels.unknown",
        "task",
        "schema_version",
        "rubric_id",
    ],
)
def test_invalid_allowlist_paths_name_adapter_source_and_path(path: str) -> None:
    with pytest.raises(ConfigResolutionError) as exc:
        resolve_run_config(defaults=_defaults(), adapter=_adapter(overridable=[path]))

    message = str(exc.value)
    assert "task_adapter" in message
    assert path in message


def test_duplicate_allowlist_path_is_rejected() -> None:
    with pytest.raises(ConfigResolutionError) as exc:
        resolve_run_config(
            defaults=_defaults(),
            adapter=_adapter(overridable=["panels.judges", "panels.judges"]),
        )
    assert "task_adapter" in str(exc.value)
    assert "panels.judges" in str(exc.value)
    assert "duplicate" in str(exc.value)


def test_unknown_overlay_path_names_source_layer_and_leaf() -> None:
    with pytest.raises(ConfigResolutionError) as exc:
        resolve_run_config(
            defaults=_defaults(),
            adapter=_adapter(),
            invocation_flags=_overlay("invocation flags", {"loop_caps": {"mystery": 2}}),
        )

    assert "invocation_flags" in str(exc.value)
    assert "loop_caps.mystery" in str(exc.value)


def test_higher_layer_cannot_change_immutable_rubric_identity() -> None:
    with pytest.raises(ConfigResolutionError) as exc:
        resolve_run_config(
            defaults=_defaults(),
            adapter=_adapter(),
            invocation_flags=_overlay("invocation flags", {"rubric_id": "other-v2"}),
        )

    assert "invocation_flags" in str(exc.value)
    assert "rubric_id" in str(exc.value)
    assert "immutable identity" in str(exc.value)


def test_absent_firm_source_is_explicit_and_deterministic() -> None:
    first = resolve_run_config(defaults=_defaults(), adapter=_adapter())
    second = resolve_run_config(defaults=_defaults(), adapter=_adapter())

    first_firm = next(source for source in first.sources if source.layer == "firm_preferences")
    second_firm = next(source for source in second.sources if source.layer == "firm_preferences")
    assert first_firm == second_firm
    assert first_firm.present is False
    assert first_firm.locator == "absent:firm_preferences"


def test_resolved_config_and_nested_values_are_frozen() -> None:
    resolved = resolve_run_config(defaults=_defaults(), adapter=_adapter())

    with pytest.raises(ValidationError):
        resolved.run_mode = "gated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        resolved.loop_caps.associate_partner = 9  # type: ignore[misc]
    with pytest.raises(TypeError):
        resolved.stages[0] = "changed"  # type: ignore[index]


def test_source_digests_are_deterministic_and_content_sensitive() -> None:
    left = _overlay("flags", {"budget": {"tier": "low"}, "run_mode": "gated"})
    reordered = _overlay("flags", {"run_mode": "gated", "budget": {"tier": "low"}})
    changed = _overlay("flags", {"run_mode": "observed", "budget": {"tier": "low"}})

    left_result = resolve_run_config(
        defaults=_defaults(), adapter=_adapter(), invocation_flags=left
    )
    reordered_result = resolve_run_config(
        defaults=_defaults(), adapter=_adapter(), invocation_flags=reordered
    )
    changed_result = resolve_run_config(
        defaults=_defaults(), adapter=_adapter(), invocation_flags=changed
    )

    def flags_digest(result: ResolvedRunConfig) -> str:
        sources = result.sources
        return next(source.sha256 for source in sources if source.layer == "invocation_flags")

    assert flags_digest(left_result) == flags_digest(reordered_result)
    assert flags_digest(left_result) != flags_digest(changed_result)


def test_repo_defaults_adapter_firm_matter_and_flags_resolve_at_boundary(tmp_path: Path) -> None:
    firm_path = tmp_path / "preferences.yaml"
    firm_path.write_text(
        "schema_version: '1.0'\nrun_config:\n  run_mode: gated\n",
        encoding="utf-8",
    )
    matter = make_matter().model_copy(
        update={"run_config": RunConfigOverlay(budget={"tier": "low"})}
    )

    resolved = resolve_run_config(
        defaults=default_config_layer(),
        adapter=ConfigLayerInput.from_path(task_config_path("discovery-responses")),
        firm_preferences=ConfigLayerInput.from_path(firm_path),
        matter_overlay=ConfigLayerInput.from_mapping(
            "matter.yaml#run_config",
            matter.run_config.model_dump(exclude_unset=True),
        ),
        invocation_flags=_overlay("invocation flags", {"run_mode": "observed"}),
    )

    assert resolved.task == "discovery-responses"
    assert resolved.run_mode == "observed"
    assert resolved.budget.tier == "low"
    assert [source.layer for source in resolved.sources] == [
        "defaults",
        "task_adapter",
        "firm_preferences",
        "matter_overlay",
        "invocation_flags",
    ]
