"""The hosted driver loop (plan FE-1): a sync, supervised worker that drains runs.

One `Worker` polls the file `Queue`, resolves each claimed item's matter vault under the
matters-root, and drives that run's turns through a provider it builds per matter. It
never holds the `RunLock` across a provider call — `orchestrator.record_turn` takes the
lock itself, so the model call happens outside any lock (the discipline that lets a
crashed turn be re-planned cleanly).

Design choice (documented): ``run_once`` DRAINS a claimed run — it loops
``plan_next -> record_turn`` until the planner yields nothing, then completes the queue
item. Repeated ``run_once`` calls therefore always make progress and a run finishes
within a single tick; the Unit-3 tests that loop ``run_once`` until completion still
pass. A provider seat limit interrupts the drain: the run pauses and the item is
released with a scheduled resume, so the work is rescheduled, never lost.

Failure routing around the provider call:
  - `SeatLimitError` -> pause the run (``capacity``), release the item to resume later.
  - `AuthError`      -> finish the run ``needs_attention`` + drop a notification file.
  - `TurnError`      -> release with backoff; after ``max_attempts`` finish + notify.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mootloop import budget, orchestrator
from mootloop.engine.queue import Queue, WorkItem
from mootloop.errors import AuthError, SeatLimitError, TurnError
from mootloop.journal import append
from mootloop.llm import LLMProvider
from mootloop.models.events import RunFinished, TurnIntent
from mootloop.vault import RunLock, validate_id

# Provider factory seam: (vault_root, run_dir, billing_mode) -> an LLMProvider.
ProviderFactory = Callable[[Path, Path, str], LLMProvider]
NowFn = Callable[[], datetime]
SleepFn = Callable[[float], None]
Stop = Callable[[], bool]

_DEFAULT_VISIBILITY_S = 300.0
_DEFAULT_RESUME_DELAY_S = 900.0
_DEFAULT_BACKOFF_S = 30.0
_DEFAULT_STALE_S = 900.0
_DEFAULT_MAX_ATTEMPTS = 5


class Worker:
    """A single driver worker draining the shared file queue (plan FE-1)."""

    def __init__(
        self,
        matters_root: Path | str,
        worker_id: str,
        queue: Queue,
        provider_factory: ProviderFactory,
        *,
        billing_mode: str = "subscription",
        visibility_timeout_s: float = _DEFAULT_VISIBILITY_S,
        resume_delay_s: float = _DEFAULT_RESUME_DELAY_S,
        backoff_s: float = _DEFAULT_BACKOFF_S,
        stale_threshold_s: float = _DEFAULT_STALE_S,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.matters_root = Path(matters_root)
        self.worker_id = worker_id
        self.queue = queue
        self.provider_factory = provider_factory
        self.billing_mode = billing_mode
        self.visibility_timeout_s = visibility_timeout_s
        self.resume_delay_s = resume_delay_s
        self.backoff_s = backoff_s
        self.stale_threshold_s = stale_threshold_s
        self.max_attempts = max_attempts
        self._reclaimed = False
        self._stop_requested = False

    # -- heartbeat + stale reclaim --

    def _workers_dir(self) -> Path:
        path = self.matters_root / ".queue" / "workers"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _heartbeat_file(self, worker_id: str) -> Path:
        return self._workers_dir() / f"{worker_id}.heartbeat"

    def _write_heartbeat(self, now: datetime) -> None:
        payload = {"pid": os.getpid(), "hostname": socket.gethostname(), "ts": now.isoformat()}
        self._heartbeat_file(self.worker_id).write_text(
            json.dumps(payload) + "\n", encoding="utf-8"
        )

    def _reclaim_stale(self, now: datetime) -> None:
        """Free queue items held by workers whose heartbeat has gone stale. RunLock
        takeover is handled by RunLock's own stale-takeover on the next record_turn."""
        for hb in self._workers_dir().glob("*.heartbeat"):
            other_id = hb.stem
            if other_id == self.worker_id:
                continue
            try:
                data = json.loads(hb.read_text(encoding="utf-8"))
                ts = datetime.fromisoformat(data["ts"])
            except (json.JSONDecodeError, OSError, KeyError, ValueError):
                continue
            if now - ts > timedelta(seconds=self.stale_threshold_s):
                self.queue.release_all_claimed_by(other_id)

    @staticmethod
    def _staging_gc() -> None:
        """Placeholder for staging-dir garbage collection (Unit-3 fills this in)."""
        return None

    # -- one tick --

    def run_once(self, now: datetime) -> bool:
        """Drive one tick. Returns True if an item was claimed and processed, else False
        (idle). On the first call, reclaim stale workers' items before claiming."""
        self._write_heartbeat(now)
        if not self._reclaimed:
            self._reclaim_stale(now)
            self._reclaimed = True
        self._staging_gc()
        item = self.queue.claim(
            self.worker_id, now, visibility_timeout_s=self.visibility_timeout_s
        )
        if item is None:
            return False
        return self._process(item, now)

    def _resolve_vault(self, matter_id: str) -> Path:
        validate_id(matter_id, kind="matter_id")
        return self.matters_root / matter_id

    def _process(self, item: WorkItem, now: datetime) -> bool:
        vault = self._resolve_vault(item.matter_id)
        run_id = item.run_id
        run_dir = vault / "runs" / run_id
        provider = self.provider_factory(vault, run_dir, self.billing_mode)
        now_iso = now.isoformat()
        while True:
            specs = orchestrator.plan_next(vault, run_id)
            if not specs:
                # Nothing schedulable: the run is finished / paused / blocked.
                self.queue.complete(item.item_id, self.worker_id)
                return True
            spec = specs[0]
            model = spec.model or "claude"
            append(
                vault,
                run_id,
                TurnIntent(
                    turn_id=spec.turn_id,
                    model=model,
                    billing_mode=self.billing_mode,  # type: ignore[arg-type]
                    max_plausible_usd=budget.max_plausible_cost(model, now.date()),
                ),
            )
            prompt = orchestrator.assemble_prompt(vault, run_id, spec.turn_id)
            try:
                result = provider.run_turn(spec, prompt)
            except SeatLimitError:
                orchestrator.pause_run(vault, run_id, reason="capacity")
                self.queue.release(
                    item.item_id,
                    self.worker_id,
                    visible_at=now + timedelta(seconds=self.resume_delay_s),
                )
                return True
            except AuthError:
                self._finish_needs_attention(vault, run_id, item, reason="auth", now=now)
                return True
            except TurnError:
                self._on_turn_error(vault, run_id, item, now=now)
                return True
            # record_turn takes the RunLock itself — never held across the call above.
            orchestrator.record_turn(
                vault, run_id, spec.turn_id, result.text, result.usage, now_iso
            )
            self.queue.heartbeat(
                item.item_id, self.worker_id, now, visibility_timeout_s=self.visibility_timeout_s
            )

    # -- failure routing --

    def _finish_needs_attention(
        self, vault: Path, run_id: str, item: WorkItem, *, reason: str, now: datetime
    ) -> None:
        with RunLock(vault, run_id):
            append(vault, run_id, RunFinished(status="needs_attention"))
        self._write_notification(item.matter_id, run_id, reason=reason, now=now)
        self.queue.complete(item.item_id, self.worker_id)

    def _on_turn_error(self, vault: Path, run_id: str, item: WorkItem, *, now: datetime) -> None:
        if item.attempts >= self.max_attempts:
            self._finish_needs_attention(vault, run_id, item, reason="turn_error", now=now)
            return
        self.queue.release(
            item.item_id,
            self.worker_id,
            visible_at=now + timedelta(seconds=self.backoff_s),
        )

    def _write_notification(
        self, matter_id: str, run_id: str, *, reason: str, now: datetime
    ) -> None:
        notif_dir = self.matters_root / ".queue" / "notifications"
        notif_dir.mkdir(parents=True, exist_ok=True)
        stamp = "".join(ch for ch in now.isoformat() if ch.isdigit())
        path = notif_dir / f"{run_id}-{stamp}.json"
        payload = {
            "run_id": run_id,
            "matter_id": matter_id,
            "reason": reason,
            "ts": now.isoformat(),
        }
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    # -- supervised loop --

    def _on_sigterm(self, _signum: int, _frame: object) -> None:
        # Drain: finish the current turn (run_once completes its drain), then exit.
        self._stop_requested = True

    def serve(
        self,
        *,
        now_fn: NowFn,
        sleep_fn: SleepFn,
        stop: Stop,
        interval: float = 1.0,
    ) -> None:
        """Loop ``run_once`` until ``stop()`` (or SIGTERM) is set, sleeping when idle.

        A real ``SIGTERM`` sets the same stop flag the injected ``stop`` uses, so a
        test can drive a bounded number of ticks without signals."""
        self._stop_requested = False
        with contextlib.suppress(ValueError):  # signal only installs on the main thread
            signal.signal(signal.SIGTERM, self._on_sigterm)
        while not (stop() or self._stop_requested):
            did_work = self.run_once(now_fn())
            if stop() or self._stop_requested:
                break
            if not did_work:
                sleep_fn(interval)


def default_now() -> datetime:
    return datetime.now(UTC)
