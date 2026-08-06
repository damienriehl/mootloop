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

A shutdown request interrupts it the same way. Because a drain is a whole run, the
drain loop — not just the tick loop — has to watch for it, or ``SIGTERM`` would sit
unread for hours while systemd waited out ``TimeoutStopSec`` and then ``SIGKILL``ed
the process wherever it happened to be.

Failure routing around the provider call:
  - `SeatLimitError` -> pause the run (``capacity``), release the item to resume later.
  - `AuthError`      -> finish the run ``needs_attention`` + drop a notification file.
  - `TurnError`      -> release with backoff; after ``max_attempts`` finish + notify.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import signal
import socket
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mootloop import budget, orchestrator
from mootloop.engine.queue import Queue, WorkItem
from mootloop.errors import AuthError, SeatLimitError, TurnError
from mootloop.journal import append
from mootloop.llm import LLMProvider, RawTurnResult
from mootloop.models.events import RunFinished, TurnIntent
from mootloop.models.run import TurnSpec
from mootloop.vault import RunLock, validate_id

logger = logging.getLogger("mootloop.engine.worker")

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

_UNSAFE_STEM_RE = re.compile(r"[^A-Za-z0-9._-]")
_PERMISSION_DENIAL_PREFIX = "headless turn was denied filesystem access:"


class _LeaseLostError(Exception):
    """Internal control flow: the queue item is no longer owned by this worker."""


def default_now() -> datetime:
    return datetime.now(UTC)


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
        now_fn: NowFn = default_now,
    ) -> None:
        self.now_fn = now_fn
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
        self._stop: Stop | None = None

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

    # -- shutdown --

    def should_stop(self) -> bool:
        """True once shutdown has been requested — by SIGTERM or by `serve`'s ``stop``.

        One predicate for both, so everything that has to notice a shutdown notices
        the same thing whether the signal is real or injected by a test.
        """
        return self._stop_requested or (self._stop is not None and self._stop())

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
        """Drain the item, and never let an unexpected failure escape the tick.

        Only the three provider errors were caught before, so anything else — an
        `OrchestratorError` from a run with no `RunStarted` event, a corrupt journal
        line, a `VaultBoundaryError`, an `OSError` — propagated out through `run_once`
        and `serve` and killed the process. The item stayed claimable, so under
        ``Restart=always`` one poison item crash-looped the driver forever and starved
        every other matter's queue. Route it down the same backoff/dead-letter ladder a
        `TurnError` takes.
        """
        try:
            return self._drain(item, now)
        except Exception:  # noqa: BLE001 — a poison item must never kill the driver loop
            logger.exception(
                "worker %s: unexpected failure on item %s (matter=%s run=%s)",
                self.worker_id,
                item.item_id,
                item.matter_id,
                item.run_id,
            )
            self._on_poison(item, now)
            return True

    def _on_poison(self, item: WorkItem, now: datetime) -> None:
        """Back off, then dead-letter at ``max_attempts`` — best-effort throughout.

        The run may not be journalable at all (that can be *why* the item is poison), so
        every vault touch is suppressed; getting the item off the queue is what matters.
        """
        if item.attempts < self.max_attempts:
            self.queue.release(
                item.item_id, self.worker_id, visible_at=now + timedelta(seconds=self.backoff_s)
            )
            return
        with contextlib.suppress(Exception):
            vault = self._resolve_vault(item.matter_id)
            with RunLock(vault, item.run_id):
                append(vault, item.run_id, RunFinished(status="needs_attention"))
        with contextlib.suppress(OSError):
            self._write_notification(item.matter_id, item.run_id, reason="poison_item", now=now)
        self.queue.complete(item.item_id, self.worker_id)

    def _drain(self, item: WorkItem, now: datetime) -> bool:
        vault = self._resolve_vault(item.matter_id)
        run_id = validate_id(item.run_id, kind="run_id")
        run_dir = vault / "runs" / run_id
        provider = self.provider_factory(vault, run_dir, self.billing_mode)
        now_iso = now.isoformat()
        while True:
            if self.should_stop():
                # The safe boundary: the previous turn is journaled, no provider call
                # is in flight, and nothing is half-written. Hand the item back
                # unclaimed so the run RESUMES here (the journal fold makes that
                # exact) instead of waiting out the visibility lease.
                logger.info(
                    "worker %s: shutdown requested; releasing item %s at a turn boundary",
                    self.worker_id,
                    item.item_id,
                )
                self.queue.release(item.item_id, self.worker_id)
                return True
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
                result = self._run_turn_with_lease(item, provider, spec, prompt)
            except _LeaseLostError:
                logger.warning(
                    "worker %s: lost the lease on item %s during provider call; "
                    "discarding its result",
                    self.worker_id,
                    item.item_id,
                )
                return True
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
            except TurnError as exc:
                if str(exc).startswith(_PERMISSION_DENIAL_PREFIX):
                    self._finish_needs_attention(
                        vault, run_id, item, reason="permission_denied", now=now
                    )
                    return True
                self._on_turn_error(vault, run_id, item, now=now)
                return True
            # record_turn takes the RunLock itself — never held across the call above.
            orchestrator.record_turn(
                vault, run_id, spec.turn_id, result.text, result.usage, now_iso
            )
            # WALL time, not the tick's frozen `now`: `Queue.heartbeat` writes
            # `visible_at = <the time passed> + timeout`, so heartbeating with the
            # claim-time stamp rewrote the lease to its original expiry and could never
            # extend it. A drain is many provider calls (each up to `timeout_s`), so the
            # lease lapsed mid-drain and a second worker claimed the same run — two
            # workers paying for the same turns, the loser dying on `LockHeldError`.
            # And the result was discarded, so the worker never learned it lost the item.
            if not self.queue.heartbeat(
                item.item_id,
                self.worker_id,
                self.now_fn(),
                visibility_timeout_s=self.visibility_timeout_s,
            ):
                logger.warning(
                    "worker %s: lost the lease on item %s mid-drain; stopping",
                    self.worker_id,
                    item.item_id,
                )
                return True

    def _run_turn_with_lease(
        self,
        item: WorkItem,
        provider: LLMProvider,
        spec: TurnSpec,
        prompt: str,
    ) -> RawTurnResult:
        """Renew the file-queue lease while the blocking provider process runs.

        The queue opens and locks its own files for every operation, so the helper
        thread shares no database connection or mutable transaction with the worker.
        A final ownership check closes the race at provider return; if ownership was
        lost, the caller discards the paid result and performs no journal mutation.
        """
        stop = threading.Event()
        lost = threading.Event()
        interval = max(0.01, min(self.visibility_timeout_s / 3.0, 30.0))

        def renew() -> None:
            while not stop.wait(interval):
                if not self.queue.heartbeat(
                    item.item_id,
                    self.worker_id,
                    self.now_fn(),
                    visibility_timeout_s=self.visibility_timeout_s,
                ):
                    lost.set()
                    return

        keeper = threading.Thread(
            target=renew,
            name=f"lease-{self.worker_id}-{item.item_id}",
            daemon=True,
        )
        keeper.start()
        result: RawTurnResult | None = None
        error: BaseException | None = None
        try:
            result = provider.run_turn(spec, prompt)
        except BaseException as exc:  # preserve provider exception after ownership check
            error = exc
        finally:
            stop.set()
            keeper.join()

        still_owned = not lost.is_set() and self.queue.heartbeat(
            item.item_id,
            self.worker_id,
            self.now_fn(),
            visibility_timeout_s=self.visibility_timeout_s,
        )
        if not still_owned:
            raise _LeaseLostError
        if error is not None:
            raise error
        assert result is not None
        return result

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
        # The run_id reaches a filename here. `_drain` validates it, but the poison
        # handler runs precisely when that validation may have failed, so slugify.
        path = notif_dir / f"{_UNSAFE_STEM_RE.sub('_', run_id)[:64]}-{stamp}.json"
        payload = {
            "run_id": run_id,
            "matter_id": matter_id,
            "reason": reason,
            "ts": now.isoformat(),
        }
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    # -- supervised loop --

    def _on_sigterm(self, _signum: int, _frame: object) -> None:
        # Finish the turn in flight, stop at the next turn boundary, release the item.
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
        test can drive a bounded number of ticks without signals. Both reach the
        drain itself through `should_stop`, so shutdown is observed WITHIN a tick and
        not only between ticks — ``run_once`` drains a whole run, which is hours."""
        self._stop_requested = False
        self._stop = stop
        with contextlib.suppress(ValueError):  # signal only installs on the main thread
            signal.signal(signal.SIGTERM, self._on_sigterm)
        try:
            while not self.should_stop():
                did_work = self.run_once(now_fn())
                if self.should_stop():
                    break
                if not did_work:
                    sleep_fn(interval)
        finally:
            self._stop = None
