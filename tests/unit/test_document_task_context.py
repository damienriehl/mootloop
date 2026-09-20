from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from mootloop.context import load_document_inputs
from mootloop.errors import OrchestratorError, TaskSpecError
from mootloop.models.document_task import DocumentEvidence, DocumentTaskInput, DocumentUnit
from mootloop.models.matter import MatterConfig
from mootloop.models.taskspec import TaskSpec
from mootloop.taskspec import validate_document_refs
from tests.conftest import make_matter


def _input() -> DocumentTaskInput:
    return DocumentTaskInput(
        input_id="motion-a",
        task="motion",
        represented_side="defendant",
        jurisdiction="federal",
        court_level="trial",
        cutoff=date(2025, 1, 1),
        strategy_id="alternative-a",
        units=(
            DocumentUnit(
                unit_id="motion-a",
                title="Motion",
                instructions="Address causation",
            ),
        ),
        evidence=(
            DocumentEvidence(
                source_id="record",
                text="A record fact.",
                sha256=hashlib.sha256(b"A record fact.").hexdigest(),
                available_on=date(2024, 12, 1),
                classification="record",
                public=True,
            ),
        ),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"available_on": "2025-01-02"},
        {"available_on": None},
        {"classification": "outcome"},
        {"strategy_id": "alternative-b"},
        {"sha256": "0" * 64},
    ],
)
def test_strategy_context_rejects_ineligible_evidence(change: dict[str, object]) -> None:
    payload = _input().model_dump(mode="json")
    payload["evidence"][0].update(change)
    with pytest.raises(ValidationError):
        DocumentTaskInput.model_validate(payload)


def test_explicit_hypothetical_is_distinct() -> None:
    payload = _input().model_dump(mode="json")
    payload["evidence"][0].update(classification="hypothetical", available_on=None)
    assert DocumentTaskInput.model_validate(payload).evidence[0].classification == "hypothetical"


def test_exact_inputs_are_detached_and_digest_bound(tmp_path: Path) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    path = root / "motion-a.json"
    path.write_text(_input().model_dump_json())
    captured, sources = load_document_inputs(tmp_path, "motion")
    spec = TaskSpec.model_validate(
        {
            "schema_version": "1.0",
            "task_spec_id": "test",
            "matter_id": "test",
            "task": "motion",
            "source_lane": "freeform",
            "intent_text": "Motion",
            "created_at": "today",
            "document_input_refs": ["motion-a"],
            "document_input_sha256": {"motion-a": sources[0].sha256},
        }
    )
    validate_document_refs(tmp_path, spec)
    path.write_text(path.read_text() + "\n")
    assert captured[0] == _input()
    with pytest.raises(TaskSpecError, match="changed"):
        validate_document_refs(tmp_path, spec)
    with pytest.raises(OrchestratorError, match="identity/task"):
        load_document_inputs(tmp_path, "complaint")


def test_missing_and_duplicate_inputs_fail(tmp_path: Path) -> None:
    with pytest.raises(OrchestratorError, match="nonempty"):
        load_document_inputs(tmp_path, "motion")
    root = tmp_path / "documents"
    root.mkdir()
    (root / "motion-a.json").write_text(_input().model_dump_json())
    with pytest.raises(OrchestratorError, match="unique"):
        load_document_inputs(tmp_path, "motion", ["motion-a", "motion-a"])


def test_advisory_requires_own_version_and_no_fake_caption() -> None:
    payload = make_matter().model_dump(mode="json")
    payload.update(
        schema_version="1.1",
        matter_kind="advisory",
        client="Business",
        objective="Evaluate a contract",
        caption=None,
        our_side=None,
        parties=[],
    )
    assert MatterConfig.model_validate(payload).caption is None
    payload["schema_version"] = "1.0"
    with pytest.raises(ValidationError, match="advisory requires"):
        MatterConfig.model_validate(payload)
    payload["matter_kind"] = "litigation"
    with pytest.raises(ValidationError, match="litigation requires"):
        MatterConfig.model_validate(payload)


def test_document_prompt_context_excludes_legacy_facts_and_scopes_public_evidence(
    tmp_path: Path,
) -> None:
    from mootloop.context import _materialize, load_run_context
    from mootloop.context_assembly import assemble_context, items_for_turn
    from mootloop.models.context import CorpusSnapshot
    from mootloop.models.run import PersonaName
    from mootloop.orchestrator import start_run
    from tests.unit.test_run_context import NOW, TASK, _vault

    vault = _vault(tmp_path)
    run_id = start_run(vault, TASK, NOW, run_id="doc-context")
    manifest = load_run_context(vault, run_id).manifest
    manifest.adapter_config.input_family = "document"
    manifest.task = "motion"
    manifest.document_inputs = [_input()]
    manifest.request_sets = []
    items = assemble_context(manifest, CorpusSnapshot())
    assert len(items) == 2
    assert "A record fact." in items[0].text
    assert "original fact" not in items[0].text
    assert items_for_turn(items, task="motion", persona=PersonaName.OC_PARTNER) == items
    manifest.document_inputs[0].evidence[0].public = False
    private = assemble_context(manifest, CorpusSnapshot())
    assert all(
        "A record fact." not in item.text
        for item in items_for_turn(private, task="motion", persona=PersonaName.OC_PARTNER)
    )
    assert _materialize(manifest).facts == []
    assert _materialize(manifest).task_units == list(_input().units)
