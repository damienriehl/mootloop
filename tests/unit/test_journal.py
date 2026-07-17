"""Journal append/read/fold + torn-line recovery + idempotent turn bodies.

Also covers the FE-1 read-only `tail_events` writer-safety contract and the FD-6
two-process append gate (concurrent ``append`` never tears a line)."""

from __future__ import annotations

import multiprocessing as mp
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
    mutated = record.model_copy(update={"completed_at": "2099-01-01T00:00:00+00:00"})
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


def test_tail_events_preserves_torn_tail_then_advances(tmp_path: Path) -> None:
    """N complete lines + a torn (newline-less) final line: tail returns the N complete
    events, leaves the torn bytes ON DISK for the writer (never truncates), and — once
    the writer completes the line — advances from the saved offset to the new event."""
    path = journal_path(tmp_path, RUN)
    append(tmp_path, RUN, _started())
    append(tmp_path, RUN, StageStarted(stage="associate_draft"))
    complete_size = path.stat().st_size

    # A crash mid-append: the first half of a valid RunFinished line, no newline.
    half = '{"kind": "run_finished", "st'
    rest = 'atus": "finished"}\n'
    with path.open("a", encoding="utf-8") as handle:
        handle.write(half)

    events, offset = tail_events(path, 0)
    assert len(events) == 2  # only the two complete lines parse
    assert offset == complete_size  # offset stops before the torn bytes
    # Writer-safety: unlike read_events, tail_events does NOT truncate the torn tail.
    assert path.stat().st_size == complete_size + len(half.encode("utf-8"))

    # The writer finishes the interrupted line; tailing from the saved offset advances.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(rest)
    more, offset2 = tail_events(path, offset)
    assert len(more) == 1
    assert isinstance(more[0], RunFinished)
    assert offset2 == path.stat().st_size


def test_tail_events_missing_file(tmp_path: Path) -> None:
    assert tail_events(tmp_path / "runs" / "nope" / "journal.jsonl", 7) == ([], 7)


# --- two-process append gate (plan FD-6) ------------------------------------

_APPEND_PROCS = 4
_APPEND_PER_PROC = 15


def _append_worker(vault: str, run_id: str, prefix: str, count: int) -> None:
    for i in range(count):
        append(
            vault,
            run_id,
            TurnDiscarded(turn_id=f"{prefix}-{i}", reason="hammer", attempt=1),
        )


def test_concurrent_appends_never_tear_a_line(tmp_path: Path) -> None:
    ctx = mp.get_context("fork")
    procs = [
        ctx.Process(target=_append_worker, args=(str(tmp_path), RUN, f"p{p}", _APPEND_PER_PROC))
        for p in range(_APPEND_PROCS)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=30)
        assert proc.exitcode == 0

    # read_events validates every line; an interleaved/torn append would raise here.
    events = read_events(tmp_path, RUN)
    discarded = [e for e in events if isinstance(e, TurnDiscarded)]
    assert len(discarded) == _APPEND_PROCS * _APPEND_PER_PROC
    turn_ids = {e.turn_id for e in discarded}
    assert len(turn_ids) == _APPEND_PROCS * _APPEND_PER_PROC  # none lost or duplicated


def test_fold_carries_discard_detail_for_retry_feedback(tmp_path: Path) -> None:
    """A discard's detail replays into state so the retry prompt can self-correct;
    detail-less events (the pre-field journal shape) fold without an entry."""
    append(tmp_path, RUN, _started())
    append(
        tmp_path,
        RUN,
        TurnDiscarded(
            turn_id=f"{RUN}-t0001",
            reason="schema-invalid: 1 error(s)",
            attempt=1,
            detail="rulings.0.verdict: Extra inputs are not permitted",
        ),
    )
    append(tmp_path, RUN, TurnDiscarded(turn_id=f"{RUN}-t0002", reason="degenerate: x", attempt=1))
    state = fold(read_events(tmp_path, RUN))
    assert state.discard_details[f"{RUN}-t0001"] == (
        "rulings.0.verdict: Extra inputs are not permitted"
    )
    assert f"{RUN}-t0002" not in state.discard_details
