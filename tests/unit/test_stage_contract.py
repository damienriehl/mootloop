"""Frozen stage-input snapshots and write-once stage-result behavior."""

from __future__ import annotations

from mootloop.models.common import DocId
from mootloop.models.events import RunState
from mootloop.models.requests import RequestItem
from mootloop.stages import StageContext
from mootloop.tasks import get_binding


def test_stage_context_snapshots_all_nested_caller_inputs() -> None:
    request = RequestItem(
        request_id="ROG-1",  # type: ignore[arg-type]
        set_number=1,
        number=1,
        text="Original request",
        source_doc=DocId("doc-original"),
    )
    facts = [{"fact_id": "fact-1", "statement": "Original fact"}]
    binding = get_binding("discovery-responses")
    state = RunState(run_id="snapshot", status="running")
    tier_models = {"personas": "original-model"}
    context = StageContext(
        run_id="snapshot",
        req_index=0,
        request=request,
        facts=facts,
        config=binding.config,
        adapter=binding.adapter,
        rubric=binding.rubric,
        state=state,
        tier_models=tier_models,
    )

    request.text = "Mutated request"
    facts[0]["fact_id"] = "mutated-fact"
    binding.config.loop_caps.associate_partner = 99
    binding.rubric.version = "mutated-rubric"
    state.status = "finished"
    tier_models["personas"] = "mutated-model"

    assert context.request.text == "Original request"
    assert context.fact_ids() == ["fact-1"]
    assert context.config.loop_caps.associate_partner == 2
    assert context.rubric.version == "1.0"
    assert context.state.status == "running"
    assert context.tier_models["personas"] == "original-model"
