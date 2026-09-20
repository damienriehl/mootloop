from __future__ import annotations

import pytest
from pydantic import ValidationError

from mootloop.errors import TaskConfigError
from mootloop.gates.completeness import evaluate, validate_presence_criteria
from mootloop.gates.turn import TurnGateContext, evaluate_turn_gates
from mootloop.models.run import DraftOutput, NarrativeAssessment, RubricScoreOutput
from mootloop.tasks import get_binding
from tests.unit.test_document_task_context import _input


def test_unknown_presence_checker_fails_closed() -> None:
    rubric = get_binding("motion").rubric.model_copy(deep=True)
    rubric.criteria[0].id = "unknown"
    with pytest.raises(TaskConfigError, match="unknown"):
        validate_presence_criteria(rubric)


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown"])
def test_document_rubric_requires_exact_score_coverage(change: str) -> None:
    rubric = get_binding("motion").rubric
    scores = [
        {"criterion_id": c.id, "score": 5, "evidence": "Record support."}
        for c in rubric.correctness_criteria()
    ]
    if change == "missing":
        scores.pop()
    elif change == "duplicate":
        scores.append(scores[0])
    else:
        scores[0]["criterion_id"] = "unknown"
    output = RubricScoreOutput.model_validate(
        {"scores": scores, "overall_notes": "Assessment", "self_assessment": "Reviewed"}
    )
    context = TurnGateContext(output, rubric, "document", "", (), "", document=True)
    result = evaluate_turn_gates(("degeneracy",), context)
    assert result.halted_by == "degeneracy"
    assert result.results[0].findings[0].code == "rubric_coverage"
    # Historic discovery rubric scoring retains its existing partial-score semantics.
    legacy = TurnGateContext(output, rubric, "rog", "", (), "")
    assert evaluate_turn_gates(("degeneracy",), legacy).halted_by is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("objections", [{"basis": "relevance", "text": "A specific objection"}]),
        ("rfa_disposition", "deny"),
    ],
)
def test_document_rejects_discovery_fields(field: str, value: object) -> None:
    output = DraftOutput.model_validate(
        dict(
            response_text="Substantive response",
            fact_ids_used=["record"],
            self_assessment="Reviewed",
            **{field: value},
        )
    )
    context = TurnGateContext(
        output, get_binding("motion").rubric, "document", "", (), "", document=True
    )
    assert evaluate_turn_gates(("degeneracy",), context).halted_by == "degeneracy"


def test_document_grounding_uses_only_frozen_evidence() -> None:
    draft = DraftOutput(
        response_text="A record fact.", fact_ids_used=["record"], self_assessment="Reviewed"
    )
    context = TurnGateContext(
        draft,
        get_binding("motion").rubric,
        "document",
        "",
        (),
        "",
        document=True,
        evidence=_input().evidence,
    )
    result = evaluate_turn_gates(("degeneracy", "fabrication"), context)
    assert all(r.status == "pass" for r in result.results)
    draft.fact_ids_used = ["unknown"]
    result = evaluate_turn_gates(("degeneracy", "fabrication"), context)
    assert result.results[-1].status == "fail"


@pytest.mark.parametrize(
    "payload",
    [
        {"disposition": "not_supported", "reasons": [], "limitations": ["Incomplete record"]},
        {"disposition": "not_supported", "reasons": ["Adverse record"], "limitations": [" "]},
        {
            "disposition": "supported",
            "reasons": ["Record"],
            "limitations": ["Incomplete record"],
            "probability": 0.9,
        },
    ],
)
def test_assessment_requires_reasons_limitations_and_no_probability(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        NarrativeAssessment.model_validate(dict(**payload, self_assessment="Reviewed"))


@pytest.mark.parametrize(
    "task", ["complaint", "motion", "appellate-brief", "oral-argument", "business-advice"]
)
def test_task_sections_require_substantive_body(task: str) -> None:
    rubric = get_binding(task).rubric
    draft = DraftOutput(
        response_text="\n".join(
            f"## {c.name}\nA substantive section body." for c in rubric.presence_criteria()
        ),
        self_assessment="Reviewed",
    )
    assert evaluate(draft, rubric, "document", "").status == "pass"
    draft.response_text = "\n".join(f"## {c.name}" for c in rubric.presence_criteria())
    assert evaluate(draft, rubric, "document", "").status == "fail"


def test_document_discovery_stage_graph_is_rejected() -> None:
    from mootloop.errors import PipelineConfigError
    from mootloop.pipeline import compile_pipeline
    from tests.conftest import make_matter

    config = get_binding("motion").config.model_copy(deep=True)
    config.stages.insert(-1, "judge_panel")
    with pytest.raises(PipelineConfigError, match="discovery"):
        compile_pipeline(config, make_matter(), matter_sha256="a" * 64, adapter_sha256="b" * 64)
