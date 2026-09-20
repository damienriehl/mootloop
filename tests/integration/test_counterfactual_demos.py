from __future__ import annotations

import os
from pathlib import Path

import pytest

from mootloop.context import load_run_context
from mootloop.demo_prepare import prepare_demo
from mootloop.models.demo import LocalInputBundle
from mootloop.models.demo_preparation import DemoPreparation
from mootloop.models.document_task import DocumentTaskInput

IDS = ("musk-openai", "dominion-fox", "epic-apple", "tesla-tornetta", "google-oracle")


@pytest.mark.parametrize("demo_id", IDS)
def test_external_counterfactuals_replay_two_isolated_strategies(tmp_path, demo_id):
    configured = os.environ.get("MOOTLOOP_REAL_DEMO_FIXTURES")
    if not configured:
        pytest.skip("Curated public-record packets must be staged outside the checkout")
    root = Path(configured).resolve()
    assert not root.is_relative_to(Path(__file__).resolve().parents[2])
    recipe = DemoPreparation.model_validate_json((root / demo_id / "preparation.json").read_bytes())
    snapshot = prepare_demo(
        root / demo_id,
        tmp_path / "work",
        tmp_path / "public",
        revision="test-r1",
        software_revision="test",
    )
    assert len(snapshot.strategies) == 2
    assert snapshot.actual_outcome and snapshot.comparison
    assert snapshot.provenance.provider_calls == 0
    bundle = LocalInputBundle.model_validate_json((tmp_path / "public/inputs.json").read_bytes())
    docs = [
        DocumentTaskInput.model_validate_json(f.text)
        for f in bundle.files
        if f.name.startswith("document-")
    ]
    assert len(docs) == 2
    assert len({d.strategy_id for d in docs}) == 2
    outcomes = set(snapshot.outcome_source_ids)
    for doc in docs:
        assert not outcomes.intersection(e.source_id for e in doc.evidence)
        assert all(e.classification != "outcome" for e in doc.evidence)
        assert doc.cutoff == snapshot.descriptor.cutoff
        assert doc.preservation_constraints
    for authored in recipe.strategies:
        vault = tmp_path / "work" / authored.strategy_id / "vault"
        for run_id in ("author-" + authored.strategy_id, "replay-" + authored.strategy_id):
            frozen = load_run_context(vault, run_id)
            assert {d.strategy_id for d in frozen.manifest.document_inputs} == {
                authored.strategy_id
            }
            assert all(
                d.cutoff == snapshot.descriptor.cutoff for d in frozen.manifest.document_inputs
            )
            assert all(
                e.classification != "outcome"
                for d in frozen.manifest.document_inputs
                for e in d.evidence
            )
            assert all(d.preservation_constraints for d in frozen.manifest.document_inputs)
    for strategy in snapshot.strategies:
        stages = {stage.kind: stage.text for stage in strategy.stages}
        assert len(stages["revised"].split()) > 280
        assert stages["revised"] != stages["initial"]
        assert "limits:" in stages["assessment"].lower()
        assert not strategy.gate_state.export_ready
        assert strategy.gate_state.run_status == "finished"
        assert not outcomes.intersection(strategy.source_ids)
    if demo_id == "tesla-tornetta":
        assert any("hypothetical" in a.lower() for d in docs for a in d.assumptions)
        assert "abandon" in snapshot.comparison.lower()
