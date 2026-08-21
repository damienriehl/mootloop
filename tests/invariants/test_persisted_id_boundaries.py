"""Canonical identifier types at persisted run-context and journal boundaries."""

from __future__ import annotations

import json

from mootloop.models.common import (
    DocId,
    LearningImportId,
    LearningProposalId,
    MatterId,
    RubricId,
    RunId,
    TaskSpecId,
    TaskSpecLockId,
    TurnId,
)
from mootloop.models.context import CorpusTextSnapshot
from mootloop.models.events import (
    GateEvaluated,
    RunStarted,
    SpendRecorded,
    TurnDiscarded,
    TurnIntent,
)
from mootloop.models.learnings import LearningImportRecord, LearningProposal, LearningReview
from mootloop.models.rubric import Rubric
from mootloop.models.run import TurnSpec
from mootloop.models.task import TaskAdapterConfig


def test_persisted_models_expose_canonical_id_annotations() -> None:
    assert CorpusTextSnapshot.model_fields["doc_id"].annotation is DocId

    assert RunStarted.model_fields["run_id"].annotation is RunId
    assert RunStarted.model_fields["matter_id"].annotation is MatterId
    assert RunStarted.model_fields["rubric_version"].annotation is RubricId
    assert RunStarted.model_fields["task_spec_id"].annotation == TaskSpecId | None
    assert RunStarted.model_fields["task_spec_lock_id"].annotation == TaskSpecLockId | None

    assert TurnSpec.model_fields["turn_id"].annotation is TurnId
    assert TurnSpec.model_fields["run_id"].annotation is RunId
    for event_type in (TurnDiscarded, GateEvaluated, SpendRecorded, TurnIntent):
        assert event_type.model_fields["turn_id"].annotation is TurnId

    assert TaskAdapterConfig.model_fields["rubric_id"].annotation is RubricId
    assert Rubric.model_fields["rubric_id"].annotation is RubricId
    assert LearningImportRecord.model_fields["import_id"].annotation is LearningImportId
    assert LearningProposal.model_fields["proposal_id"].annotation is LearningProposalId
    assert LearningProposal.model_fields["import_id"].annotation is LearningImportId
    assert LearningReview.model_fields["proposal_id"].annotation is LearningProposalId


def test_id_newtypes_are_static_boundaries_with_string_wire_format() -> None:
    # NewType constructors are distinct callables for static checkers even though their
    # runtime/wire representation deliberately remains a plain string.
    assert TurnId is not RunId
    assert RubricId is not TaskSpecId
    assert LearningImportId is not LearningProposalId

    historical = {
        "kind": "run_started",
        "run_id": "run-001",
        "matter_id": "2026-08-20-client-matter",
        "task": "discovery-responses",
        "rubric_version": "discovery-responses-v1.0",
        "config_digest": "abc123",
        "task_spec_id": "task-001",
    }
    started = RunStarted.model_validate(historical)
    assert json.loads(started.model_dump_json()) == {
        **historical,
        "context_manifest_sha256": None,
        "mode": "autonomous",
        "task_spec_lock_id": None,
        "task_spec_lock_sha256": None,
        "queue_intent": None,
    }
