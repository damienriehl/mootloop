from __future__ import annotations

import json
from pathlib import Path

import pytest

from mootloop.demo_inputs import validate_bundle
from mootloop.demo_prepare import prepare_demo
from mootloop.models.demo import LocalInputBundle
from mootloop.models.demo_preparation import DemoPreparation

ROOT = Path(__file__).resolve().parents[2] / "fixtures/demos/business"
IDS = (
    "supplier-termination",
    "liability-cap",
    "ai-customer-data",
    "worker-classification",
    "competitor-recruitment",
    "comparative-advertising",
    "open-source-release",
    "distributor-restrictions",
    "customer-data-incident",
    "acquisition-diligence",
)


def test_ten_business_examples_are_materially_distinct():
    preparations = [
        DemoPreparation.model_validate_json((ROOT / key / "preparation.json").read_bytes())
        for key in IDS
    ]
    assert {p.descriptor.demo_id for p in preparations} == set(IDS)
    assert len({p.strategies[0].input_summary for p in preparations}) == 10
    for preparation in preparations:
        assert preparation.descriptor.collection == "business"
        assert preparation.descriptor.task == "business-advice"
        unit = preparation.strategies[0].outputs["advice"]
        assert unit.initial.response_text != unit.revised.response_text
        assert len(unit.critique.critiques) >= 3
        assert "## Secondary Deliverable" in unit.revised.response_text
        assert preparation.sources and preparation.claims


@pytest.mark.parametrize("demo_id", IDS)
def test_business_workflow_replays_complete_advice_and_action(tmp_path, demo_id):
    output = tmp_path / "projection"
    snapshot = prepare_demo(
        ROOT / demo_id, tmp_path / "work", output, revision="test-r1", software_revision="test"
    )
    strategy = snapshot.strategies[0]
    assert strategy.gate_state.run_status == "finished"
    assert not strategy.gate_state.export_ready
    assert "attestation" in strategy.gate_state.blockers
    assert snapshot.provenance.provider_calls == 0
    assert not snapshot.provenance.attorney_approval
    assert {stage.kind for stage in strategy.stages} == {
        "initial",
        "critique",
        "revised",
        "assessment",
    }
    revised = next(stage for stage in strategy.stages if stage.kind == "revised")
    assert "## Secondary Deliverable" in revised.text
    assert all(stage.turn_ids for stage in strategy.stages)
    bundle = LocalInputBundle.model_validate_json((output / "inputs.json").read_bytes())
    validate_bundle(bundle)
    assert any(file.name.startswith("replay-") for file in bundle.files)
    assert not (output / "review.json").exists(), "Preparation cannot approve publication itself"
    if demo_id == "supplier-termination":
        assert "Draft notice" in revised.text and "15-calendar-day" in revised.text
    if demo_id == "liability-cap":
        assert "$360,000" in revised.text and "Negotiation draft" in revised.text
    original = json.loads((ROOT / demo_id / "document-advice.json").read_text())
    bundled = next(file for file in bundle.files if file.name == "document-advice.json")
    assert json.loads(bundled.text) == original
