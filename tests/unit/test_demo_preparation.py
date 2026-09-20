from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mootloop.demo_prepare import AuthoredProvider, prepare_demo
from mootloop.models.demo_preparation import DemoPreparation
from mootloop.models.run import PersonaName, TurnSpec
from mootloop.web.catalog import PublicationError

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/demos/business/supplier-termination"


def test_authored_provider_has_no_generic_fallback():
    preparation = DemoPreparation.model_validate_json((FIXTURE / "preparation.json").read_bytes())
    provider = AuthoredProvider(preparation.strategies[0])
    spec = TurnSpec(
        turn_id="one",
        run_id="one",
        persona=PersonaName.ASSOCIATE,
        request_id="unknown",
        stage="associate_draft",
        output_schema_name="draft",
    )
    with pytest.raises(PublicationError, match="no authored output"):
        provider.run_turn(spec, "")
    spec = spec.model_copy(update={"request_id": "advice", "output_schema_name": "rubric_score"})
    with pytest.raises(PublicationError, match="locked criteria"):
        provider.run_turn(spec, "")


def test_strategy_mismatch_is_rejected_before_creating_vault(tmp_path):
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE, fixture)
    path = fixture / "document-advice.json"
    document = json.loads(path.read_text())
    document["strategy_id"] = "different-alternative"
    path.write_text(json.dumps(document))
    with pytest.raises(PublicationError, match="document strategy"):
        prepare_demo(
            fixture, tmp_path / "work", tmp_path / "output", revision="r1", software_revision="test"
        )
    assert not (tmp_path / "work").exists()
    assert not (tmp_path / "output").exists()


def test_preparation_rejects_unlisted_operational_file(tmp_path):
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE, fixture)
    (fixture / "journal.jsonl").write_text("sentinel-private-state")
    with pytest.raises(PublicationError, match="allowlist"):
        prepare_demo(
            fixture, tmp_path / "work", tmp_path / "output", revision="r1", software_revision="test"
        )
    assert not (tmp_path / "work").exists()
