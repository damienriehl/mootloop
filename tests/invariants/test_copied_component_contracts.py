"""Copied structures retain local seams and exact upstream provenance."""

from __future__ import annotations

from pathlib import Path

from mootloop.convergence import CONVERGENCE_PROVENANCE


def test_convergence_copy_has_a_machine_readable_upstream_pin() -> None:
    assert CONVERGENCE_PROVENANCE.component == "alea-intake ConvergenceEvaluator structure"
    assert CONVERGENCE_PROVENANCE.source_path == (
        "backend/app/services/analysis/convergence.py"
    )
    assert CONVERGENCE_PROVENANCE.commit_sha == "18d8cf5"
    assert CONVERGENCE_PROVENANCE.license == "MIT"

    third_party = (Path(__file__).parents[2] / "THIRD-PARTY.md").read_text(encoding="utf-8")
    assert CONVERGENCE_PROVENANCE.commit_sha in third_party
    assert CONVERGENCE_PROVENANCE.source_path in third_party


def test_stage_layer_uses_local_score_and_convergence_protocols() -> None:
    source = (Path(__file__).parents[2] / "src/mootloop/stages.py").read_text(encoding="utf-8")
    assert "ConvergenceEvaluator(" not in source
    assert ".weighted_score(" not in source
    assert "self.score_source.score(" in source
    assert "self.convergence_source.evaluate(" in source
