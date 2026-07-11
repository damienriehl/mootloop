"""Cost metering + pre-run estimate (plan D5).

One dated price table is the single source of truth for every dollar figure MootLoop
shows. Metering uses the four-bucket cache-aware formula:

    $ = (input×I + cache_write×1.25I + cache_read×0.1I + output×O) / 1e6

where ``input`` **excludes** cached tokens (a meter reading only ``input_tokens``
undercounts, and the hard cap fires late). Dates are always injected — never
``datetime.now()`` in this core — so a run meters at the price in effect on its run
date, and estimates meter at a caller-supplied date.

Budget *tiers* vary the model per role (plan D5 finding: persona output is 60-75% of
every run, so tiers move the persona/judge/rubric/cite model, not just panel size).
"""

from __future__ import annotations

from datetime import date

from mootloop.errors import BudgetError
from mootloop.llm import TokenUsage
from mootloop.models.budget import (
    ROLES,
    EstimateAssumptions,
    EstimateRange,
    PriceWindow,
    StageEstimate,
)
from mootloop.models.task import TaskAdapterConfig

# Pinned model ids (plan D5). Cache multipliers: read 0.1×input, write_5m 1.25×input.
MODEL_OPUS = "claude-opus-4-8"
MODEL_SONNET = "claude-sonnet-5"
MODEL_HAIKU = "claude-haiku-4-5"
MODEL_FABLE = "claude-fable-5"

# Dated price table ($/1e6 tokens). Sonnet 5's intro pricing ($2/$10) ends
# 2026-08-31, then $3/$15 (plan D5).
_EPOCH = date(2025, 1, 1)
PRICES: dict[str, list[PriceWindow]] = {
    MODEL_OPUS: [PriceWindow(_EPOCH, None, 5.0, 25.0)],
    MODEL_SONNET: [
        PriceWindow(_EPOCH, date(2026, 8, 31), 2.0, 10.0),
        PriceWindow(date(2026, 9, 1), None, 3.0, 15.0),
    ],
    MODEL_HAIKU: [PriceWindow(_EPOCH, None, 1.0, 5.0)],
    MODEL_FABLE: [PriceWindow(_EPOCH, None, 10.0, 50.0)],
}

# Tier -> {role: model} (plan D5). Personas stay on Opus across tiers; the cheaper
# tiers move judges/rubric/cite down the model ladder.
TIERS: dict[str, dict[str, str]] = {
    "no-budget": {
        "personas": MODEL_OPUS,
        "judges": MODEL_OPUS,
        "rubric": MODEL_OPUS,
        "cite": MODEL_OPUS,
    },
    "moderate": {
        "personas": MODEL_OPUS,
        "judges": MODEL_SONNET,
        "rubric": MODEL_SONNET,
        "cite": MODEL_HAIKU,
    },
    "low": {
        "personas": MODEL_OPUS,
        "judges": MODEL_HAIKU,
        "rubric": MODEL_HAIKU,
        "cite": MODEL_HAIKU,
    },
}


def tier_models(tier: str) -> dict[str, str]:
    """Resolve a budget tier to its per-role model map (plan D5)."""
    models = TIERS.get(tier)
    if models is None:
        known = ", ".join(sorted(TIERS))
        raise BudgetError(f"unknown budget tier {tier!r}; known tiers: {known}")
    return dict(models)


# --- metering ---------------------------------------------------------------


def price_for(model: str, on: date) -> tuple[float, float] | None:
    """The (input, output) $/1e6 rate for ``model`` on ``on``, or None if unpriced."""
    for window in PRICES.get(model, ()):
        if window.covers(on):
            return (window.input_per_mtok, window.output_per_mtok)
    return None


def cost_of(usage: TokenUsage, model: str, on: date) -> float:
    """The metered dollar cost of one call under the four-bucket formula (plan D5).

    An unpriced model (e.g. the ``fake`` test provider) costs $0.
    """
    price = price_for(model, on)
    if price is None:
        return 0.0
    rate_in, rate_out = price
    return (
        usage.input_tokens * rate_in
        + usage.cache_write * 1.25 * rate_in
        + usage.cache_read * 0.1 * rate_in
        + usage.output_tokens * rate_out
    ) / 1e6


# --- pre-run estimate -------------------------------------------------------


def _call_cost(role: str, model: str, on: date, assume: EstimateAssumptions) -> float:
    usage = TokenUsage(
        input_tokens=assume.uncached_in,
        cache_read=assume.cache_read,
        cache_write=0,
        output_tokens=assume.output_for(role),
        model=model,
    )
    return cost_of(usage, model, on)


def estimate_run(
    request_count: int,
    config: TaskAdapterConfig,
    tier: str,
    on: date,
    *,
    assumptions: EstimateAssumptions | None = None,
) -> EstimateRange:
    """Pre-run cost range for a task (plan D5).

    ``min`` assumes every partner loop converges after one round; ``max`` assumes
    every cap is hit. The judge stage carries the ``requests × objections × panel``
    term, and a derail-retry factor pads the whole. Per-request call counts:

      - associate_draft: 1 draft (round 1)
      - partner_loop: critiques (1..ap) + redrafts (0..ap-1) + in-loop rubric (1..ap)
      - oc_attack / bolster: their caps
      - judge_panel: judges × objections
      - rubric_gate: the decorrelated final panel
    """
    assume = assumptions or EstimateAssumptions()
    models = tier_models(tier)
    ap = config.loop_caps.associate_partner
    oc = config.loop_caps.oc
    bolster = config.loop_caps.bolster
    judges = config.panels.judges
    rubric_panel = config.panels.rubric_judges
    obj = assume.objections_per_request

    # (stage, role, min_calls_per_request, max_calls_per_request)
    plan: list[tuple[str, str, int, int]] = [
        ("associate_draft", "personas", 1, 1),
        ("partner_loop:redraft", "personas", 0, ap - 1),
        ("partner_loop:critique", "personas", 1, ap),
        ("partner_loop:rubric", "rubric", 1, ap),
        ("oc_attack", "personas", oc, oc),
        ("bolster", "personas", bolster, bolster),
        ("judge_panel", "judges", judges * obj, judges * obj),
        # Costed restructure pass fires only when an objection is weak (plan Phase 6):
        # 0 in the converge-early floor, up to the cap in the all-caps ceiling.
        ("restructure", "personas", 0, config.loop_caps.restructure),
        ("rubric_gate", "rubric", rubric_panel, rubric_panel),
    ]

    breakdown: list[StageEstimate] = []
    min_total = 0.0
    max_total = 0.0
    for stage, role, min_per, max_per in plan:
        model = models[role]
        per_call = _call_cost(role, model, on, assume)
        min_calls = min_per * request_count
        max_calls = max_per * request_count
        min_usd = min_calls * per_call * assume.derail_factor
        max_usd = max_calls * per_call * assume.derail_factor
        breakdown.append(
            StageEstimate(
                stage=stage,
                role=role,
                model=model,
                min_calls=min_calls,
                max_calls=max_calls,
                min_usd=round(min_usd, 4),
                max_usd=round(max_usd, 4),
            )
        )
        min_total += min_usd
        max_total += max_usd

    return EstimateRange(
        tier=tier,
        requests=request_count,
        min_usd=round(min_total, 4),
        max_usd=round(max_total, 4),
        breakdown=breakdown,
    )


__all__ = [
    "ROLES",
    "PRICES",
    "TIERS",
    "tier_models",
    "price_for",
    "cost_of",
    "estimate_run",
]
