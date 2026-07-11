"""Structural invariants for the Phase 3 rubric/convergence/budget layer."""

from __future__ import annotations

import pytest

from mootloop.budget import PRICES, TIERS, price_for
from mootloop.models.budget import ROLES
from mootloop.models.rubric import load_rubric, sha256_hex
from mootloop.resources import rubric_path
from mootloop.tasks import get_binding

pytestmark = pytest.mark.invariant

RUBRIC_ID = "discovery-responses-v1.0"


def test_shipped_rubric_lock_is_intact() -> None:
    # The repo copy must load (the lock hash matches) — no drifted lock ever ships.
    yaml_path = rubric_path(RUBRIC_ID)
    rubric = load_rubric(yaml_path)
    assert rubric.locked is True
    recorded = yaml_path.with_suffix(".sha256").read_text(encoding="utf-8").split()[0]
    assert recorded == sha256_hex(yaml_path.read_text(encoding="utf-8"))


def test_rubric_criteria_kinds_are_disjoint_and_weighted() -> None:
    rubric = load_rubric(rubric_path(RUBRIC_ID))
    kinds = {c.kind for c in rubric.criteria}
    assert kinds == {"present", "correct"}
    # Every correctness criterion carries a positive weight (it drives the composite).
    assert all(c.weight > 0 for c in rubric.correctness_criteria("all"))


def test_run_started_pins_the_rubric_version() -> None:
    # D12: the rubric version rides on RunStarted so a run is reproducible.
    binding = get_binding("discovery-responses")
    assert binding.config.rubric_id == RUBRIC_ID


def test_every_tier_covers_every_role_with_a_priced_model() -> None:
    from datetime import date

    on = date(2026, 7, 1)
    for tier, mix in TIERS.items():
        assert set(mix) == set(ROLES), tier
        for role, model in mix.items():
            assert model in PRICES, (tier, role, model)
            assert price_for(model, on) is not None
