"""The partner loop skips remaining redraft slots once the rubric-driven convergence
signals say the draft stopped improving AND stopped changing AND is complete (D6).

Uses a synthetic StageContext with the cap raised to 3 so genuine convergence can
settle the loop at round 2 — *before* the iteration cap."""

from __future__ import annotations

from typing import Any

from mootloop.models.common import DocId
from mootloop.models.events import RunState
from mootloop.models.requests import RequestItem
from mootloop.models.run import (
    SCHEMA_CRITIQUE,
    SCHEMA_DRAFT,
    SCHEMA_RUBRIC,
    PersonaName,
    TurnRecord,
    TurnSpec,
)
from mootloop.stages import PartnerLoopStage, SlotLayout, StageContext
from mootloop.tasks import get_binding

_COMPLIANT = (
    "Interrogatory No. 1 is restated. Responding party identifies Jane Roe and "
    "John Doe as persons with knowledge of the contract."
)


def _draft_output(text: str) -> dict[str, Any]:
    return {
        "response_text": text,
        "objections": [],
        "candidate_citations": [],
        "fact_ids_used": ["fact-1"],
        "attorney_gate_items": [],
        "self_assessment": "grounded",
    }


def _rubric_output(ids: list[str], score: int) -> dict[str, Any]:
    return {
        "scores": [{"criterion_id": cid, "score": score, "evidence": "meets"} for cid in ids],
        "overall_notes": "steady",
        "self_assessment": "scored each",
    }


def _critique_output(verdict: str) -> dict[str, Any]:
    return {"verdict": verdict, "critiques": ["x"], "instructions": ["y"], "self_assessment": "z"}


def _ctx(round2_text: str) -> StageContext:
    binding = get_binding("discovery-responses")
    caps = binding.config.loop_caps.model_copy(update={"associate_partner": 3})
    config = binding.config.model_copy(update={"loop_caps": caps})
    request = RequestItem(
        request_id="ROG-1",  # type: ignore[arg-type]
        set_number=1,
        number=1,
        text="Identify every person with knowledge of the contract.",
        source_doc=DocId("doc-servedservedserv"),
    )
    layout = SlotLayout(
        run_id="conv", req_index=0, ap=3, oc=1, bolster=1, judges=3, rubric_panel=3
    )
    ids = [c.id for c in binding.rubric.correctness_criteria("rog")]

    def rec(seq: int, persona: PersonaName, schema: str, output: dict[str, Any]) -> TurnRecord:
        tid = layout.turn_id(seq)
        spec = TurnSpec(
            turn_id=tid,
            run_id="conv",
            persona=persona,
            request_id="ROG-1",  # type: ignore[arg-type]
            stage="partner_loop",
            output_schema_name=schema,
        )
        return TurnRecord(spec=spec, output=output, completed_at="t")

    completed: dict[str, TurnRecord] = {}
    # Round 1 and round 2: same high rubric score, request-compliant drafts.
    for r, text in ((1, _COMPLIANT), (2, round2_text)):
        completed[layout.turn_id(layout.draft(r))] = rec(
            layout.draft(r), PersonaName.ASSOCIATE, SCHEMA_DRAFT, _draft_output(text)
        )
        completed[layout.turn_id(layout.critique(r))] = rec(
            layout.critique(r), PersonaName.PARTNER, SCHEMA_CRITIQUE, _critique_output("revise")
        )
        completed[layout.turn_id(layout.rubric_loop(r))] = rec(
            layout.rubric_loop(r), PersonaName.RUBRIC_JUDGE, SCHEMA_RUBRIC, _rubric_output(ids, 4)
        )
    state = RunState(run_id="conv", status="running", completed_turns=completed)
    return StageContext(
        run_id="conv",
        req_index=0,
        request=request,
        facts=[{"fact_id": "fact-1", "statement": "The contract price was $148,500."}],
        config=config,
        adapter=binding.adapter,
        rubric=binding.rubric,
        state=state,
    )


def test_converged_loop_skips_the_third_round() -> None:
    # Round 2 draft is identical to round 1 -> stopped changing + stopped improving.
    ctx = _ctx(_COMPLIANT)
    assert ctx.converged_at(2) is True
    stage = PartnerLoopStage()
    assert stage.is_complete(ctx) is True
    assert stage.plan(ctx) == []  # the round-3 redraft slot is never scheduled


def test_churning_loop_runs_to_the_next_round() -> None:
    # Round 2 draft churns heavily -> high material change -> NOT converged yet.
    ctx = _ctx("Wholly different verbiage bearing no resemblance to the first pass.")
    assert ctx.converged_at(2) is False
    stage = PartnerLoopStage()
    assert stage.is_complete(ctx) is False
    specs = stage.plan(ctx)
    assert len(specs) == 1
    assert specs[0].stage == "partner_loop"
    assert specs[0].persona == PersonaName.ASSOCIATE  # the round-3 redraft
