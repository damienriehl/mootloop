from __future__ import annotations

import hashlib
from datetime import date

import pytest

from mootloop.budget import estimate_run
from mootloop.errors import PipelineConfigError
from mootloop.models.matter import MatterConfig, Personas
from mootloop.models.pipeline import ResolvedPipeline
from mootloop.models.run import PersonaName
from mootloop.pipeline import ACTIVE_PIPELINE_PERSONAS, compile_pipeline
from mootloop.tasks import get_binding
from tests.conftest import make_matter

_SHA_A = hashlib.sha256(b"matter").hexdigest()
_SHA_B = hashlib.sha256(b"adapter").hexdigest()


def _matter(*, strategy: str = "thin-full", **personas: bool) -> MatterConfig:
    matter = make_matter()
    return matter.model_copy(
        update={
            "pipeline_strategy": strategy,
            "personas": Personas(**personas),
        }
    )


def _compile(matter: MatterConfig):
    return compile_pipeline(
        get_binding("discovery-responses").config,
        matter,
        matter_sha256=_SHA_A,
        adapter_sha256=_SHA_B,
    )


@pytest.mark.parametrize(
    ("strategy", "stages", "ap_cap", "max_turns"),
    [
        (
            "thin-full",
            (
                "associate_draft",
                "partner_loop",
                "oc_attack",
                "bolster",
                "judge_panel",
                "restructure",
                "rubric_gate",
                "assemble",
            ),
            2,
            16,
        ),
        (
            "deep-core",
            (
                "associate_draft",
                "partner_loop",
                "oc_attack",
                "bolster",
                "judge_panel",
                "restructure",
                "rubric_gate",
                "assemble",
            ),
            3,
            19,
        ),
        (
            "adversarial-first",
            (
                "associate_draft",
                "oc_attack",
                "bolster",
                "partner_loop",
                "judge_panel",
                "restructure",
                "rubric_gate",
                "assemble",
            ),
            2,
            16,
        ),
    ],
)
def test_strategy_matrix_is_exact_and_provenanced(
    strategy: str,
    stages: tuple[str, ...],
    ap_cap: int,
    max_turns: int,
) -> None:
    pipeline = _compile(_matter(strategy=strategy))

    assert pipeline.strategy == strategy
    assert pipeline.effective_config.stages == list(stages)
    assert pipeline.effective_config.loop_caps.associate_partner == ap_cap
    assert pipeline.effective_config.loop_caps.oc == 2
    assert pipeline.max_turns_per_request == max_turns
    assert pipeline.active_personas == ACTIVE_PIPELINE_PERSONAS
    assert pipeline.bypassed_personas == ()
    assert [(source.kind, source.sha256) for source in pipeline.sources] == [
        ("matter_config", _SHA_A),
        ("task_adapter", _SHA_B),
    ]
    estimate = estimate_run(
        1,
        pipeline.effective_config,
        "moderate",
        date(2026, 8, 21),
    )
    calls = [(row.stage, row.min_calls, row.max_calls) for row in estimate.breakdown]
    partner_calls = [
        ("partner_loop:redraft", 0, ap_cap - 1),
        ("partner_loop:critique", 1, ap_cap),
        ("partner_loop:rubric", 1, ap_cap),
    ]
    external_calls = [
        ("oc_attack", 2, 2),
        ("bolster", 1, 1),
    ]
    if strategy == "adversarial-first":
        expected_core = external_calls + partner_calls
    else:
        expected_core = partner_calls + external_calls
    assert calls == [
        ("associate_draft", 1, 1),
        *expected_core,
        ("judge_panel", 9, 9),
        ("restructure", 0, 1),
        ("rubric_gate", 3, 3),
    ]


def test_each_persona_toggle_has_owned_or_removed_work() -> None:
    delegated = _compile(_matter(associate=False))
    assert delegated.drafting_persona == PersonaName.PARTNER
    assert PersonaName.ASSOCIATE in delegated.bypassed_personas

    no_partner = _compile(_matter(partner=False))
    assert "partner_loop" not in no_partner.effective_config.stages
    assert PersonaName.PARTNER in no_partner.bypassed_personas

    one_opponent = _compile(_matter(oc_partner=False))
    assert one_opponent.oc_personas == (PersonaName.OC_ASSOCIATE,)
    assert one_opponent.effective_config.loop_caps.oc == 1

    no_opponents = _compile(_matter(oc_associate=False, oc_partner=False))
    assert "oc_attack" not in no_opponents.effective_config.stages
    assert "bolster" not in no_opponents.effective_config.stages
    assert no_opponents.effective_config.loop_caps.oc == 0
    assert no_opponents.effective_config.loop_caps.bolster == 0

    no_judge = _compile(_matter(judge=False))
    assert "judge_panel" not in no_judge.effective_config.stages
    assert "restructure" not in no_judge.effective_config.stages
    assert no_judge.effective_config.loop_caps.restructure == 0

    no_rubric = _compile(_matter(rubric_judge=False))
    assert "rubric_gate" not in no_rubric.effective_config.stages
    assert "rubric" not in no_rubric.effective_config.gates


def test_impossible_persona_and_strategy_combinations_fail_before_launch() -> None:
    with pytest.raises(PipelineConfigError, match="drafting owner"):
        _compile(_matter(associate=False, partner=False))
    with pytest.raises(PipelineConfigError, match="requires the associate and partner"):
        _compile(_matter(strategy="deep-core", partner=False))
    with pytest.raises(PipelineConfigError, match="requires the associate and partner"):
        _compile(_matter(strategy="deep-core", associate=False))
    with pytest.raises(PipelineConfigError, match="requires opposing counsel"):
        _compile(
            _matter(
                strategy="adversarial-first",
                oc_associate=False,
                oc_partner=False,
            )
        )


def test_cost_estimate_uses_the_compiled_stage_graph() -> None:
    full = _compile(_matter())
    minimal = _compile(
        _matter(
            partner=False,
            oc_associate=False,
            oc_partner=False,
            judge=False,
            rubric_judge=False,
        )
    )

    full_estimate = estimate_run(1, full.effective_config, "moderate", date(2026, 8, 21))
    minimal_estimate = estimate_run(
        1,
        minimal.effective_config,
        "moderate",
        date(2026, 8, 21),
    )

    assert full_estimate.max_usd > minimal_estimate.max_usd
    assert [row.stage for row in minimal_estimate.breakdown] == ["associate_draft"]


def test_legacy_persona_keys_are_import_only_not_selectable_vocabulary() -> None:
    personas = Personas.model_validate({"opposing_counsel": False, "cite_checker": True})

    assert personas.oc_associate is False
    assert personas.oc_partner is False
    assert "opposing_counsel" not in Personas.model_fields
    assert "cite_checker" not in Personas.model_fields
    assert PersonaName.JUROR not in ACTIVE_PIPELINE_PERSONAS
    assert PersonaName.CITE_CHECKER not in ACTIVE_PIPELINE_PERSONAS


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("max_turns_per_request", 999, "does not match"),
        (
            "active_personas",
            [
                "partner",
                "associate",
                "oc_associate",
                "oc_partner",
                "judge",
                "rubric_judge",
            ],
            "canonical order",
        ),
        (
            "sources",
            [
                {"kind": "task_adapter", "locator": "adapter", "sha256": "1" * 64},
                {"kind": "matter_config", "locator": "matter", "sha256": "0" * 64},
            ],
            "sources must name matter",
        ),
    ],
)
def test_persisted_pipeline_rejects_tampered_derived_contracts(
    field: str,
    replacement: object,
    message: str,
) -> None:
    payload = _compile(_matter()).model_dump(mode="json")
    payload[field] = replacement

    with pytest.raises(ValueError, match=message):
        ResolvedPipeline.model_validate(payload)
