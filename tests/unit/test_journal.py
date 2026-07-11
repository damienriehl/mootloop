"""Journal append/read/fold + torn-line recovery + idempotent turn bodies."""

from __future__ import annotations

from pathlib import Path

from mootloop.journal import (
    append,
    fold,
    journal_path,
    read_events,
    turn_body_path,
    write_turn_body,
)
from mootloop.models.events import (
    RunFinished,
    RunStarted,
    SpendRecorded,
    StageStarted,
    TurnCompleted,
    TurnDiscarded,
)
from mootloop.models.gates import GatePass
from mootloop.models.run import DraftOutput, PersonaName, TurnRecord, TurnSpec

RUN = "discovery-responses-20260711000000"


def _started() -> RunStarted:
    return RunStarted(
        run_id=RUN,
        matter_id="acme-v-widgets",
        task="discovery-responses",
        rubric_version="discovery-responses-v1.0",
        config_digest="abc123",
    )


def _turn_record(turn_id: str) -> TurnRecord:
    spec = TurnSpec(
        turn_id=turn_id,
        run_id=RUN,
        persona=PersonaName.ASSOCIATE,
        stage="associate_draft",
        output_schema_name="draft",
    )
    output = DraftOutput(response_text="A response.", self_assessment="ok").model_dump()
    return TurnRecord(
        spec=spec,
        output=output,
        gate_results=[GatePass(gate="degeneracy")],
        completed_at="2026-07-11T01:00:00+00:00",
    )


def test_append_read_roundtrip(tmp_path: Path) -> None:
    append(tmp_path, RUN, _started())
    append(tmp_path, RUN, StageStarted(stage="associate_draft"))
    append(tmp_path, RUN, TurnCompleted(record=_turn_record(f"{RUN}-t0000")))
    events = read_events(tmp_path, RUN)
    assert len(events) == 3
    assert isinstance(events[0], RunStarted)
    assert isinstance(events[2], TurnCompleted)


def test_fold_derives_state(tmp_path: Path) -> None:
    append(tmp_path, RUN, _started())
    append(tmp_path, RUN, StageStarted(stage="partner_loop"))
    append(tmp_path, RUN, TurnCompleted(record=_turn_record(f"{RUN}-t0000")))
    append(tmp_path, RUN, TurnDiscarded(turn_id=f"{RUN}-t0001", reason="bad json", attempt=1))
    append(
        tmp_path,
        RUN,
        SpendRecorded(
            turn_id=f"{RUN}-t0000",
            input_tokens=100,
            cache_read=0,
            cache_write=0,
            output_tokens=50,
            model="fake",
            usd_equiv=0.5,
        ),
    )
    state = fold(read_events(tmp_path, RUN))
    assert state.run_id == RUN
    assert state.matter_id == "acme-v-widgets"
    assert state.current_stage == "partner_loop"
    assert state.is_completed(f"{RUN}-t0000")
    assert state.discarded[f"{RUN}-t0001"] == 1
    assert state.total_spend_usd == 0.5
    assert not state.finished


def test_fold_finished_status(tmp_path: Path) -> None:
    append(tmp_path, RUN, _started())
    append(tmp_path, RUN, RunFinished(status="finished"))
    state = fold(read_events(tmp_path, RUN))
    assert state.status == "finished"
    assert state.finished


def test_fold_deterministic_across_serialize_roundtrip(tmp_path: Path) -> None:
    append(tmp_path, RUN, _started())
    append(tmp_path, RUN, TurnCompleted(record=_turn_record(f"{RUN}-t0000")))
    events = read_events(tmp_path, RUN)
    first = fold(events)
    second = fold(read_events(tmp_path, RUN))
    assert first.model_dump() == second.model_dump()


def test_torn_final_line_recovered(tmp_path: Path) -> None:
    append(tmp_path, RUN, _started())
    append(tmp_path, RUN, TurnCompleted(record=_turn_record(f"{RUN}-t0000")))
    # Simulate a crash mid-append: a partial JSON line with no newline.
    path = journal_path(tmp_path, RUN)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "turn_completed", "record": {"spec"')
    events = read_events(tmp_path, RUN)
    assert len(events) == 2  # torn line dropped, valid prefix kept
    # The tail was truncated, so a subsequent append lands cleanly.
    append(tmp_path, RUN, RunFinished(status="finished"))
    events2 = read_events(tmp_path, RUN)
    assert len(events2) == 3
    assert isinstance(events2[-1], RunFinished)


def test_write_turn_body_is_idempotent(tmp_path: Path) -> None:
    record = _turn_record(f"{RUN}-t0000")
    path = write_turn_body(tmp_path, RUN, record)
    assert path == turn_body_path(tmp_path, RUN, f"{RUN}-t0000")
    original = path.read_text(encoding="utf-8")
    # A second write with different content must NOT clobber the first.
    mutated = record.model_copy(
        update={"completed_at": "2099-01-01T00:00:00+00:00"}
    )
    write_turn_body(tmp_path, RUN, mutated)
    assert path.read_text(encoding="utf-8") == original


def test_read_missing_journal_is_empty(tmp_path: Path) -> None:
    assert read_events(tmp_path, "nope") == []
    assert fold([]).status == "running"
