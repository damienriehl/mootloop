"""Thorough coverage for the driver `Worker` loop (plan FE-1 Unit 3).

Drives a real (fake-provider) discovery run end-to-end through claimed queue items and
exercises the failure routing that matters most for the hosted tier: a seat limit
pauses the run and reschedules its queue slot for a later resume (the work is never
lost). Consolidates the earlier smoke test.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mootloop.engine.queue import Queue, WorkItem
from mootloop.engine.worker import Worker
from mootloop.errors import SeatLimitError, TurnError
from mootloop.journal import load_state, read_events
from mootloop.llm import FakeLLMProvider, LLMProvider, RawTurnResult, TokenUsage
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


def _build_matters_root(tmp_path: Path, *, hard_cap_usd: float | None = None) -> tuple[Path, str]:
    from mootloop.discovery_parser import save_requests
    from mootloop.facts import FactStore
    from mootloop.orchestrator import start_run
    from tests.conftest import make_matter

    root = tmp_path / "matters"
    registry = MatterRegistry(root=root)
    matter = make_matter(MATTER_ID)
    if hard_cap_usd is not None:
        matter = matter.model_copy(
            update={"budget": matter.budget.model_copy(update={"hard_cap_usd": hard_cap_usd})}
        )
    vault = registry.create(matter)
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


# --- lease extension uses WALL time, not the tick's frozen timestamp ----------


class _ClockAdvancingProvider:
    """A fake provider that burns simulated wall time on every turn and records the
    queue lease as it stood at that moment."""

    def __init__(self, clock: list[datetime], queue: Queue, seen: list[str]) -> None:
        self._clock = clock
        self._queue = queue
        self._seen = seen
        self._inner = FakeLLMProvider()

    def run_turn(self, spec: TurnSpec, prompt: str) -> object:
        self._clock[0] = self._clock[0] + timedelta(seconds=40)
        snap = self._queue.snapshot()
        if snap:
            self._seen.append(snap[0].visible_at)
        return self._inner.run_turn(spec, prompt)


def test_lease_is_extended_with_wall_time_not_the_tick_stamp(tmp_path: Path) -> None:
    """`Queue.heartbeat` writes ``visible_at = <the stamp passed> + timeout``. The worker
    passed the tick's FROZEN `now`, so every heartbeat rewrote the lease to its original
    expiry and it could never move forward. A single-request drain is ~11 provider calls
    (each up to `timeout_s`), so the lease lapsed mid-drain on essentially every real run
    and a second worker claimed the same run — both paying for the same turns."""
    root, run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    _enqueue_run_turn(queue, run_id, "wi-lease")
    clock = [NOW]
    leases: list[str] = []
    worker = Worker(
        root,
        "wA",
        queue,
        lambda v, r, b: _ClockAdvancingProvider(clock, queue, leases),  # type: ignore[arg-type,return-value]
        visibility_timeout_s=60.0,
        now_fn=lambda: clock[0],
    )
    worker.run_once(NOW)

    assert clock[0] > NOW + timedelta(seconds=60), "the drain should outlast one lease"
    assert len(leases) > 2, "expected a multi-turn drain"
    # The lease strictly advances; it never sits at claim-time + timeout while the
    # worker keeps working (which is what made the item stealable mid-drain).
    assert leases == sorted(leases) and leases[-1] > leases[0], leases
    stamps = [datetime.fromisoformat(v) for v in leases]
    assert stamps[-1] > NOW + timedelta(seconds=60)


def test_worker_stops_draining_when_it_loses_the_lease(tmp_path: Path) -> None:
    """A lost heartbeat means someone else owns the run; keep draining and both workers
    execute the same turns. The return value used to be discarded entirely."""
    root, run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    _enqueue_run_turn(queue, run_id, "wi-stolen")
    worker = Worker(root, "wA", queue, _fake_factory)
    item = queue.claim("wA", NOW, visibility_timeout_s=300.0)
    assert item is not None
    # Simulate the steal: another worker now owns the item, so wA's heartbeat fails.
    queue.release(item.item_id, "wA")
    stolen = queue.claim("wB", NOW, visibility_timeout_s=300.0)
    assert stolen is not None

    handled = worker._drain(item, NOW)
    assert handled is True
    vault = MatterRegistry(root=root).resolve(MATTER_ID)
    # wA bailed after its first turn instead of driving the whole run behind wB's back.
    assert not load_state(vault, run_id).is_terminal
    assert queue.snapshot()[0].claimed_by == "wB"


class _BlockingProvider:
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release
        self._inner = FakeLLMProvider()

    def run_turn(self, spec: TurnSpec, prompt: str) -> RawTurnResult:
        self.started.set()
        assert self.release.wait(timeout=2.0)
        return self._inner.run_turn(spec, prompt)


def test_provider_call_keeps_queue_lease_alive(tmp_path: Path) -> None:
    root, run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    _enqueue_run_turn(queue, run_id, "wi-blocking")
    started = threading.Event()
    release = threading.Event()
    provider = _BlockingProvider(started, release)
    worker = Worker(
        root,
        "wA",
        queue,
        lambda v, r, b: provider,
        visibility_timeout_s=0.15,
    )

    thread = threading.Thread(target=worker.run_once, args=(NOW,))
    thread.start()
    assert started.wait(timeout=1.0)
    time.sleep(0.25)
    assert queue.claim("wB", datetime.now(UTC), visibility_timeout_s=1.0) is None
    release.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_provider_result_is_discarded_when_lease_heartbeat_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    root, run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    _enqueue_run_turn(queue, run_id, "wi-heartbeat-error")
    started = threading.Event()
    release = threading.Event()
    provider = _BlockingProvider(started, release)
    worker = Worker(
        root,
        "wA",
        queue,
        lambda v, r, b: provider,
        visibility_timeout_s=0.15,
    )
    original_heartbeat = queue.heartbeat
    calls = 0

    def failing_heartbeat(*args: object, **kwargs: object) -> bool:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("simulated queue heartbeat failure")
        return original_heartbeat(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(queue, "heartbeat", failing_heartbeat)
    caplog.set_level(logging.WARNING, logger="mootloop.engine.worker")
    thread = threading.Thread(target=worker.run_once, args=(NOW,))
    thread.start()
    assert started.wait(timeout=1.0)
    time.sleep(0.1)
    release.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    vault = MatterRegistry(root=root).resolve(MATTER_ID)
    assert load_state(vault, run_id).completed_turns == {}
    assert "queue heartbeat failed" in caplog.text


class _PermissionDeniedProvider:
    def run_turn(self, spec: TurnSpec, prompt: str) -> RawTurnResult:
        raise TurnError(
            "headless turn was denied filesystem access: permission settings refused Read"
        )


class _GenericTurnErrorProvider:
    def run_turn(self, spec: TurnSpec, prompt: str) -> RawTurnResult:
        raise TurnError("headless turn timed out after 300s")


def test_permission_denial_is_immediately_operator_visible(tmp_path: Path) -> None:
    root, run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    _enqueue_run_turn(queue, run_id, "wi-denied")
    worker = Worker(
        root,
        "wA",
        queue,
        lambda v, r, b: _PermissionDeniedProvider(),
    )

    assert worker.run_once(NOW) is True
    vault = MatterRegistry(root=root).resolve(MATTER_ID)
    assert load_state(vault, run_id).status == "needs_attention"
    assert queue.snapshot() == []
    [notification] = list((root / ".queue" / "notifications").glob("*.json"))
    body = notification.read_text(encoding="utf-8")
    assert '"category":"permission_denied"' in body
    assert "permission settings refused Read" not in body
    assert "matter_id" not in body and "run_id" not in body


def test_blocked_notification_still_completes_terminal_queue_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, run_id = _build_matters_root(tmp_path)
    registry = tmp_path / "malformed-canaries.json"
    registry.write_text("{", encoding="utf-8")
    monkeypatch.setenv("MOOTLOOP_RUNTIME_MODE", "hosted")
    monkeypatch.setenv("MOOTLOOP_CANARY_REGISTRY", str(registry))
    queue = Queue(root)
    _enqueue_run_turn(queue, run_id, "wi-blocked-notification")
    worker = Worker(root, "wA", queue, lambda v, r, b: _PermissionDeniedProvider())

    assert worker.run_once(NOW) is True
    vault = MatterRegistry(root=root).resolve(MATTER_ID)
    assert load_state(vault, run_id).status == "needs_attention"
    assert queue.snapshot() == []
    assert not (root / ".queue" / "notifications").exists()


def test_generic_turn_error_still_retries_with_backoff(tmp_path: Path) -> None:
    root, run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    _enqueue_run_turn(queue, run_id, "wi-retry")
    worker = Worker(
        root,
        "wA",
        queue,
        lambda v, r, b: _GenericTurnErrorProvider(),
        backoff_s=30.0,
    )

    assert worker.run_once(NOW) is True
    vault = MatterRegistry(root=root).resolve(MATTER_ID)
    assert load_state(vault, run_id).status == "running"
    [pending] = queue.snapshot()
    assert pending.claimed_by is None
    assert pending.visible_at == (NOW + timedelta(seconds=30)).isoformat()
    assert not (root / ".queue" / "notifications").exists()


# --- a poison item must never kill the driver --------------------------------


def _poison_item(queue: Queue, item_id: str, attempts: int = 0) -> None:
    queue.enqueue(
        WorkItem.create(
            lane="run",
            matter_id=MATTER_ID,
            run_id="ghost-run",  # started nowhere: plan_next raises OrchestratorError
            kind="run_turn",
            now=NOW,
            item_id=item_id,
        ).model_copy(update={"attempts": attempts})
    )


def test_poison_item_does_not_escape_the_tick(tmp_path: Path) -> None:
    root, _run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    _poison_item(queue, "wi-poison")
    worker = Worker(root, "wP", queue, _fake_factory, backoff_s=30.0)

    assert worker.run_once(NOW) is True  # no exception escapes
    snap = queue.snapshot()
    assert len(snap) == 1 and snap[0].claimed_by is None  # released with backoff
    assert queue.claim("wP", NOW, visibility_timeout_s=60) is None


def test_poison_item_is_dead_lettered_at_max_attempts(tmp_path: Path) -> None:
    root, _run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    worker = Worker(root, "wP", queue, _fake_factory, max_attempts=3)
    _poison_item(queue, "wi-poison", attempts=3)

    assert worker.run_once(NOW) is True
    assert queue.snapshot() == []  # off the queue, not crash-looping the driver
    notifications = list((root / ".queue" / "notifications").glob("*.json"))
    assert notifications, "a dead-lettered poison item must leave an operator notification"


def test_serve_survives_a_poison_item(tmp_path: Path) -> None:
    """`serve` had no handler either, so under Restart=always this crash-looped."""
    root, _run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    _poison_item(queue, "wi-poison")
    worker = Worker(root, "wP", queue, _fake_factory)
    ticks = [0]

    def stop() -> bool:
        ticks[0] += 1
        return ticks[0] > 4

    worker.serve(now_fn=lambda: NOW, sleep_fn=lambda _s: None, stop=stop, interval=0.0)


# --- shutdown must interrupt a drain ----------------------------------------


class _SigtermAfterFirstTurn:
    """Raises the worker's real SIGTERM flag once a turn has been journaled."""

    def __init__(self, worker: Worker) -> None:
        self._inner = FakeLLMProvider()
        self._worker = worker
        self.armed = True
        self.calls = 0

    def run_turn(self, spec: TurnSpec, prompt: str) -> RawTurnResult:
        self.calls += 1
        result = self._inner.run_turn(spec, prompt)
        if self.armed:
            self._worker._on_sigterm(signal.SIGTERM, None)  # systemd stopping us mid-drain
        return result


def test_sigterm_stops_a_drain_at_a_turn_boundary(tmp_path: Path) -> None:
    """A drain is a whole run, so SIGTERM has to be observed INSIDE `run_once`.

    The flag was only read between ticks, so a stop request sat unread for the rest
    of the run: systemd waits out TimeoutStopSec and SIGKILLs the process wherever it
    is, which for this loop is somewhere in a journal append.
    """
    root, run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    _enqueue_run_turn(queue, run_id, "wi-1")
    vault = MatterRegistry(root=root).resolve(MATTER_ID)

    provider: _SigtermAfterFirstTurn | None = None

    def factory(vault_root: Path, run_dir: Path, billing_mode: str) -> LLMProvider:
        assert provider is not None
        return provider  # type: ignore[return-value]

    worker = Worker(root, "w1", queue, factory)
    provider = _SigtermAfterFirstTurn(worker)

    assert worker.run_once(NOW) is True

    # Stopped after the turn in flight, not part-way through one and not at the end.
    assert provider.calls == 1
    state = load_state(vault, run_id)
    assert len(state.completed_turns) == 1
    assert state.is_terminal is False  # the run is unfinished, and says so

    # The item is back on the queue, unclaimed and visible: the work is rescheduled,
    # never lost, and no other worker had to wait out our visibility lease.
    [pending] = queue.snapshot()
    assert pending.item_id == "wi-1"
    assert pending.claimed_by is None

    # A restarted worker resumes from the journal and carries the run to completion.
    provider.armed = False
    worker._stop_requested = False
    for _ in range(_MAX_TICKS):
        worker.run_once(NOW)
        if load_state(vault, run_id).is_terminal:
            break
    assert load_state(vault, run_id).status == "finished"
    assert queue.snapshot() == []


# --- the hard cap must fire on spend a REAL provider reports -----------------


class _DatedIdProvider:
    """A provider that reports a dated model id, as `claude -p --output-format json`
    does — the shape the price table never resolved."""

    def __init__(self) -> None:
        self._inner = FakeLLMProvider()

    def run_turn(self, spec: TurnSpec, prompt: str) -> RawTurnResult:
        result = self._inner.run_turn(spec, prompt)
        return RawTurnResult(
            text=result.text,
            usage=TokenUsage(
                input_tokens=150_000,
                cache_read=0,
                cache_write=0,
                output_tokens=30_000,
                model=f"{spec.model or 'claude'}-20251101",
            ),
        )


def test_hard_cap_fires_on_spend_reported_with_a_dated_model_id(tmp_path: Path) -> None:
    """Every id a real provider reports was unpriced, so `usd_equiv` was $0.00 for
    every turn, `total_spend_usd` never moved, and the hard cap never fired."""
    root, run_id = _build_matters_root(tmp_path, hard_cap_usd=6.0)
    vault = MatterRegistry(root=root).resolve(MATTER_ID)

    queue = Queue(root)
    _enqueue_run_turn(queue, run_id, "wi-cap")
    worker = Worker(root, "wC", queue, lambda v, r, b: _DatedIdProvider())  # type: ignore[arg-type,return-value]
    for _ in range(_MAX_TICKS):
        worker.run_once(NOW)
        if load_state(vault, run_id).is_terminal:
            break

    # Each turn is 150k in + 30k out at Opus rates = $1.50 settled, so the cap is
    # reached after a few turns. Priced at $0.00 the run simply completes, unbounded.
    state = load_state(vault, run_id)
    assert state.total_spend_usd > 0.0, "a dated model id must still meter real dollars"
    assert state.status == "capped", state.status
