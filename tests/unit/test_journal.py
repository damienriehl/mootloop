"""Journal append/read/fold + torn-line recovery + idempotent turn bodies."""

from __future__ import annotations

from pathlib import Path

from mootloop.journal import (
    append,
    fold,
    journal_path,
    read_events,
    tail_events,
    turn_body_path,
    write_turn_body,
)
from mootloop.models.events import (
    RunFinished,
    RunPaused,
    RunResumed,
    RunStarted,
    SpendRecorded,
    StageStarted,
    TurnCompleted,
    TurnDiscarded,
    TurnIntent,
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


def test_fold_pause_resume(tmp_path: Path) -> None:
    append(tmp_path, RUN, _started())
    append(tmp_path, RUN, RunPaused(reason="capacity"))
    paused = fold(read_events(tmp_path, RUN))
    assert paused.status == "paused"
    assert paused.finished  # not schedulable now
    assert not paused.is_terminal  # but NOT complete for good
    append(tmp_path, RUN, RunResumed())
    resumed = fold(read_events(tmp_path, RUN))
    assert resumed.status == "running"
    assert not resumed.finished


def test_is_terminal_only_for_complete_states() -> None:
    from mootloop.models.events import RunState

    for status in ("finished", "needs_attention", "capped"):
        assert RunState(status=status).is_terminal
    for status in ("running", "paused", "checkpoint", "needs_decisions"):
        assert not RunState(status=status).is_terminal


def test_fold_turn_intent_reconciles_on_completion(tmp_path: Path) -> None:
    turn_id = f"{RUN}-t0000"
    append(tmp_path, RUN, _started())
    append(
        tmp_path,
        RUN,
        TurnIntent(
            turn_id=turn_id, model="fake", billing_mode="subscription", max_plausible_usd=1.25
        ),
    )
    pending = fold(read_events(tmp_path, RUN))
    assert pending.pending_intents == {turn_id: 1.25}
    # A TurnCompleted reconciles (clears) the intent, even with no SpendRecorded.
    append(tmp_path, RUN, TurnCompleted(record=_turn_record(turn_id)))
    reconciled = fold(read_events(tmp_path, RUN))
    assert reconciled.pending_intents == {}


def test_fold_turn_intent_reconciles_on_spend(tmp_path: Path) -> None:
    turn_id = f"{RUN}-t0000"
    append(tmp_path, RUN, _started())
    append(
        tmp_path,
        RUN,
        TurnIntent(turn_id=turn_id, model="fake", billing_mode="api", max_plausible_usd=2.0),
    )
    append(
        tmp_path,
        RUN,
        SpendRecorded(
            turn_id=turn_id,
            input_tokens=10,
            cache_read=0,
            cache_write=0,
            output_tokens=5,
            model="fake",
            usd_equiv=0.0,
        ),
    )
    assert fold(read_events(tmp_path, RUN)).pending_intents == {}


def test_tail_events_incremental(tmp_path: Path) -> None:
    path = journal_path(tmp_path, RUN)
    append(tmp_path, RUN, _started())
    events, offset = tail_events(path)
    assert len(events) == 1
    assert isinstance(events[0], RunStarted)
    assert offset == path.stat().st_size
    # A second read from the offset yields nothing until more is appended.
    assert tail_events(path, offset) == ([], offset)
    append(tmp_path, RUN, RunFinished(status="finished"))
    more, offset2 = tail_events(path, offset)
    assert len(more) == 1
    assert isinstance(more[0], RunFinished)
    assert offset2 == path.stat().st_size


def test_tail_events_leaves_torn_final_line(tmp_path: Path) -> None:
    path = journal_path(tmp_path, RUN)
    append(tmp_path, RUN, _started())
    complete_size = path.stat().st_size
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "turn_completed", "record": {"spec"')
    events, offset = tail_events(path)
    assert len(events) == 1  # only the complete line is parsed
    assert offset == complete_size  # offset does NOT advance past the torn tail
    # tail_events never truncates: the torn bytes remain on disk for the writer.
    assert path.stat().st_size > complete_size


def test_tail_events_missing_file(tmp_path: Path) -> None:
    assert tail_events(tmp_path / "runs" / "nope" / "journal.jsonl", 7) == ([], 7)
