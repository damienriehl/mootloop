"""Budget metering + estimate (plan D5): dated prices, the four-bucket formula, and
the pre-run range with its judge-multiplier term."""

from __future__ import annotations

from datetime import date

import pytest

from mootloop import budget
from mootloop.errors import BudgetError
from mootloop.llm import TokenUsage
from mootloop.models.budget import EstimateAssumptions
from mootloop.tasks import get_binding

ON = date(2026, 7, 1)


def test_sonnet_price_switches_on_the_boundary() -> None:
    assert budget.price_for("claude-sonnet-5", date(2026, 8, 31)) == (2.0, 10.0)
    assert budget.price_for("claude-sonnet-5", date(2026, 9, 1)) == (3.0, 15.0)
    # Opus / Haiku / Fable are flat; an unknown model is unpriced.
    assert budget.price_for("claude-opus-4-8", date(2026, 7, 1)) == (5.0, 25.0)
    assert budget.price_for("claude-fable-5", date(2026, 7, 1)) == (10.0, 50.0)
    assert budget.price_for("fake", date(2026, 7, 1)) is None


def test_four_bucket_formula_is_exact() -> None:
    usage = TokenUsage(
        input_tokens=1_000_000,
        cache_read=1_000_000,
        cache_write=1_000_000,
        output_tokens=1_000_000,
        model="claude-opus-4-8",
    )
    # 5 (in) + 1.25*5 (write) + 0.1*5 (read) + 25 (out) = 36.75
    assert budget.cost_of(usage, "claude-opus-4-8", date(2026, 7, 1)) == pytest.approx(36.75)
    # The same usage is cheaper on Sonnet's intro pricing, and cheaper still after.
    intro = budget.cost_of(usage, "claude-sonnet-5", date(2026, 8, 31))
    later = budget.cost_of(usage, "claude-sonnet-5", date(2026, 9, 1))
    assert later > intro
    # An unpriced (fake) model is free — the fake provider never accrues real spend.
    assert budget.cost_of(usage, "fake", date(2026, 7, 1)) == 0.0


def test_cache_read_excluded_from_input_is_metered_separately() -> None:
    # A meter that only read input_tokens would undercount the 15k cached read.
    only_input = TokenUsage(8_000, 0, 0, 0, "claude-opus-4-8")
    with_cache = TokenUsage(8_000, 15_000, 0, 0, "claude-opus-4-8")
    assert budget.cost_of(with_cache, "claude-opus-4-8", date(2026, 7, 1)) > budget.cost_of(
        only_input, "claude-opus-4-8", date(2026, 7, 1)
    )


def test_tiers_move_persona_and_judge_models() -> None:
    assert budget.tier_models("no-budget")["judges"] == "claude-opus-4-8"
    assert budget.tier_models("moderate")["judges"] == "claude-sonnet-5"
    assert budget.tier_models("low")["judges"] == "claude-haiku-4-5"
    # Personas stay on Opus across every tier (plan D5 finding).
    assert all(budget.tier_models(t)["personas"] == "claude-opus-4-8" for t in budget.TIERS)
    with pytest.raises(BudgetError):
        budget.tier_models("nonexistent")


def test_estimate_range_and_judge_multiplier_term() -> None:
    config = get_binding("discovery-responses").config
    assume = EstimateAssumptions()
    requests = 5
    estimate = budget.estimate_run(
        requests, config, "moderate", date(2026, 7, 1), assumptions=assume
    )

    assert estimate.requests == requests
    assert estimate.min_usd < estimate.max_usd  # convergence-early is cheaper than all-caps
    assert estimate.min_usd > 0

    # The judge stage carries the requests × objections × panel term (plan D5).
    judge_row = next(r for r in estimate.breakdown if r.stage == "judge_panel")
    expected = config.panels.judges * assume.objections_per_request * requests
    assert judge_row.min_calls == expected
    assert judge_row.max_calls == expected
    assert judge_row.model == "claude-sonnet-5"  # judges role under the moderate tier

    # The final rubric gate is priced at the rubric-role model, panel-sized.
    gate_row = next(r for r in estimate.breakdown if r.stage == "rubric_gate")
    assert gate_row.min_calls == config.panels.rubric_judges * requests


# --- an unpriced model must never silently meter at $0 -----------------------


@pytest.mark.parametrize(
    "reported",
    [
        "claude-opus-4-8-20260101",  # dated variant of a table entry
        "claude-sonnet-5-20260401",
    ],
)
def test_dated_model_ids_resolve_to_the_table_rate(reported: str) -> None:
    """`claude -p --output-format json` reports a DATED id; the table keys the bare
    name. Unresolved, the recorded spend was $0.00 for every real turn."""
    assert budget.price_for(reported, ON) == budget.price_for(
        budget.normalize_model(reported), ON
    )
    assert budget.price_for(reported, ON) is not None


@pytest.mark.parametrize("unknown", ["claude", "claude-opus-9-9", "some-new-model"])
def test_unknown_model_meters_at_the_priciest_rate_not_zero(unknown: str) -> None:
    """The hosted provider records whatever id the CLI reports. Metering an unknown one
    at $0 meant `total_spend_usd` never moved and the hard cap could not fire —
    unbounded spend. Charge the priciest known rate instead (fail closed)."""
    usage = TokenUsage(
        input_tokens=200_000, cache_read=0, cache_write=0, output_tokens=64_000,
        model=unknown,
    )
    cost = budget.cost_of(usage, unknown, ON)
    assert cost > 0.0
    assert cost == pytest.approx(budget.max_plausible_cost(unknown, ON))
    # And it is at least as expensive as the priciest priced model.
    assert cost >= budget.cost_of(usage, budget.MODEL_OPUS, ON)


def test_explicitly_free_models_still_cost_nothing() -> None:
    usage = TokenUsage(
        input_tokens=1000, cache_read=0, cache_write=0, output_tokens=1000, model="fake"
    )
    for free in ("fake", "seat", ""):
        assert budget.cost_of(usage, free, ON) == 0.0
        assert budget.max_plausible_cost(free, ON) == 0.0


def test_every_tier_model_is_priced() -> None:
    """A tier whose model is unpriced would reserve and settle at the fallback rate."""
    for tier in budget.TIERS:
        for role, model in budget.tier_models(tier).items():
            assert budget.price_for(model, ON) is not None, f"{tier}/{role}={model}"
