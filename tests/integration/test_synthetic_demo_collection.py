from __future__ import annotations

from pathlib import Path

import pytest

from mootloop.demo_prepare import prepare_demo
from mootloop.models.demo_preparation import DemoPreparation

ROOT = Path(__file__).resolve().parents[2] / "fixtures/demos/synthetic"
DEMOS = {
    "supplier-discovery": "discovery-responses",
    "employment-retaliation": "complaint",
    "product-liability": "motion",
    "land-use-appeal": "appellate-brief",
    "civil-rights-argument": "oral-argument",
}


@pytest.mark.parametrize("demo_id,task", DEMOS.items())
def test_five_synthetic_cases_run_assigned_workflows(tmp_path, demo_id, task):
    preparation = DemoPreparation.model_validate_json(
        (ROOT / demo_id / "preparation.json").read_bytes()
    )
    assert preparation.descriptor.task == task
    snapshot = prepare_demo(
        ROOT / demo_id,
        tmp_path / "work",
        tmp_path / "projection",
        revision="test-r1",
        software_revision="test",
    )
    strategy = snapshot.strategies[0]
    stages = {stage.kind: stage for stage in strategy.stages}
    assert stages["initial"].text != stages["revised"].text
    assert strategy.gate_state.run_status == (
        "needs_decisions" if task == "discovery-responses" else "finished"
    )
    assert not strategy.gate_state.export_ready
    assert snapshot.provenance.provider_calls == 0
    if task == "oral-argument":
        assert all(f"{number}." in stages["revised"].text for number in range(1, 7))
        assert "clearly" in stages["revised"].text and "May 2023" in stages["revised"].text
    if task == "complaint":
        assert "## Claims" in stages["revised"].text
        assert "attendance" in stages["critique"].text
    if task == "discovery-responses":
        assert all(key in stages["revised"].text for key in ("ROG-1", "RFP-1", "RFA-1"))
        assert "42 U.S.C." not in stages["revised"].text
