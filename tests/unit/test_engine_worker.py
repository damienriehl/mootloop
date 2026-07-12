"""Thorough coverage for the driver `Worker` loop (plan FE-1 Unit 3).

Drives a real (fake-provider) discovery run end-to-end through claimed queue items and
exercises the failure routing that matters most for the hosted tier: a seat limit
pauses the run and reschedules its queue slot for a later resume (the work is never
lost). Consolidates the earlier smoke test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from mootloop.engine.queue import Queue, WorkItem
from mootloop.engine.worker import Worker
from mootloop.errors import SeatLimitError
from mootloop.journal import load_state, read_events
from mootloop.llm import FakeLLMProvider, LLMProvider
from mootloop.models.common import DocId
from mootloop.models.events import RunPaused, SpendRecorded, TurnIntent
from mootloop.models.requests import RequestItem, RequestSet, RequestType
from mootloop.models.run import TurnSpec
from mootloop.registry import MatterRegistry

NOW = datetime(2026, 7, 12, tzinfo=UTC)
MATTER_ID = "acme-v-widgets"
_MAX_TICKS = 20


def _fake_factory(vault_root: Path, run_dir: Path, billing_mode: str) -> LLMProvider:
    return FakeLLMProvider()


class _SeatProvider:
    """A provider that always hits the subscription seat/rate limit."""

    def run_turn(self, spec: TurnSpec, prompt: str) -> object:
        raise SeatLimitError("seat limit for test")


def _seat_factory(vault_root: Path, run_dir: Path, billing_mode: str) -> LLMProvider:
    return _SeatProvider()  # type: ignore[return-value]


def _build_matters_root(tmp_path: Path) -> tuple[Path, str]:
    from mootloop.discovery_parser import save_requests
    from mootloop.facts import FactStore
    from mootloop.orchestrator import start_run
    from tests.conftest import make_matter

    root = tmp_path / "matters"
    registry = MatterRegistry(root=root)
    vault = registry.create(make_matter(MATTER_ID))
    save_requests(
        vault,
        RequestSet(
            request_type=RequestType.INTERROGATORY,
            set_number=1,
            title="Interrogatories Set 1",
            items=[
                RequestItem(
                    request_id="ROG-1",  # type: ignore[arg-type]
                    set_number=1,
                    number=1,
                    text="Identify every person with knowledge of the contract.",
                    source_doc=DocId("doc-servedservedserv"),
                )
            ],
        ),
    )
    FactStore(vault).add_fact("The contract price was $148,500.", confidence=1.0)
    run_id = start_run(vault, "discovery-responses", NOW.isoformat(), run_id="drive-0001")
    return root, run_id


def _enqueue_run_turn(queue: Queue, run_id: str, item_id: str) -> None:
    queue.enqueue(
        WorkItem.create(
            lane="run",
            matter_id=MATTER_ID,
            run_id=run_id,
            kind="run_turn",
            now=NOW,
            item_id=item_id,
        )
    )


def test_worker_drains_run_to_finished_with_intents_reconciled(tmp_path: Path) -> None:
    root, run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    _enqueue_run_turn(queue, run_id, "wi-1")
    worker = Worker(root, "w1", queue, _fake_factory)
    vault = MatterRegistry(root=root).resolve(MATTER_ID)

    # Loop run_once until the run reaches a terminal state (guarded).
    for _ in range(_MAX_TICKS):
        worker.run_once(NOW)
        if load_state(vault, run_id).is_terminal:
            break
    else:  # pragma: no cover - guard tripping is a test failure, not normal flow
        raise AssertionError("run did not reach a terminal state within the tick guard")

    state = load_state(vault, run_id)
    assert state.status == "finished"
    assert len(state.completed_turns) > 0
    # Every write-ahead TurnIntent was reconciled by its TurnCompleted/SpendRecorded.
    assert state.pending_intents == {}

    events = read_events(vault, run_id)
    assert any(isinstance(e, TurnIntent) for e in events)  # write-ahead intents emitted
    assert any(isinstance(e, SpendRecorded) for e in events)  # spend recorded on real prices
    assert queue.snapshot() == []  # the item was completed


def test_worker_seat_limit_pauses_and_reschedules_slot(tmp_path: Path) -> None:
    root, run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    _enqueue_run_turn(queue, run_id, "wi-seat")
    worker = Worker(root, "wS", queue, _seat_factory, resume_delay_s=900.0)
    vault = MatterRegistry(root=root).resolve(MATTER_ID)

    handled = worker.run_once(NOW)
    assert handled is True

    # The run paused with reason "capacity" (a non-terminal, resumable state).
    state = load_state(vault, run_id)
    assert state.status == "paused"
    assert state.is_terminal is False
    paused = [e for e in read_events(vault, run_id) if isinstance(e, RunPaused)]
    assert len(paused) == 1 and paused[0].reason == "capacity"

    # The queue slot was RELEASED (owner cleared) and scheduled for a later resume:
    # a claim right now sees nothing; a claim after the resume delay reclaims it.
    snap = queue.snapshot()
    assert len(snap) == 1 and snap[0].claimed_by is None
    assert queue.claim("wS", NOW, visibility_timeout_s=60) is None
    later = queue.claim("wS", NOW + timedelta(seconds=901), visibility_timeout_s=60)
    assert later is not None and later.item_id == "wi-seat"


def test_worker_idle_returns_false(tmp_path: Path) -> None:
    root = tmp_path / "matters"
    root.mkdir()
    worker = Worker(root, "w1", Queue(root), _fake_factory)
    assert worker.run_once(NOW) is False
