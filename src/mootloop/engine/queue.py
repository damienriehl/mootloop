"""File-based priority work queue for the hosted driver (plan FE-1). No Redis.

A single JSONL state file (``<matters_root>/.queue/queue.jsonl``) holds every live work
item. Every read-modify-write (claim / complete / release / heartbeat / enqueue) runs
under an exclusive advisory ``flock`` on a sibling lock file, then rewrites the state
file atomically (same-dir temp + ``os.replace`` + ``fsync``). The lock serializes
concurrent workers and the atomic replace survives a crash mid-write, so a two-process
hammer never corrupts the file and never double-claims an item.

Two priority lanes: the ``interactive`` lane (human-facing turns) is always drained
before the ``run`` lane (batch runs). Visibility timeouts make claims self-healing: a
claimed item becomes claimable again once its ``visible_at`` lapses, so a crashed
worker's item is picked up by the next claimer without any external reaper.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from mootloop.models.common import StrictModel

Lane = Literal["interactive", "run"]

# Lane priority: lower drains first. Interactive beats batch runs.
_LANE_PRIORITY: dict[str, int] = {"interactive": 0, "run": 1}

_EPOCH_ISO = "1970-01-01T00:00:00+00:00"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class WorkItem(StrictModel):
    """One unit of driver work. ``visible_at`` gates claimability (a claim pushes it
    into the future); ``claimed_by`` records the current owner (``None`` when free)."""

    item_id: str
    lane: Lane
    matter_id: str
    run_id: str
    kind: str
    enqueued_at: str
    visible_at: str
    attempts: int = 0
    claimed_by: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        lane: Lane,
        matter_id: str,
        run_id: str,
        kind: str,
        now: datetime,
        payload: dict[str, Any] | None = None,
        item_id: str | None = None,
    ) -> WorkItem:
        stamp = _iso(now)
        return cls(
            item_id=item_id or uuid.uuid4().hex,
            lane=lane,
            matter_id=matter_id,
            run_id=run_id,
            kind=kind,
            enqueued_at=stamp,
            visible_at=stamp,
            payload=payload or {},
        )

    def _sort_key(self) -> tuple[int, str]:
        return (_LANE_PRIORITY.get(self.lane, 99), self.enqueued_at)


class Queue:
    """Priority work queue rooted at ``<matters_root>/.queue`` (created lazily)."""

    def __init__(self, matters_root: Path | str) -> None:
        self.root = Path(matters_root) / ".queue"
        self._state = self.root / "queue.jsonl"
        self._lock = self.root / ".lock"

    # -- locking + persistence --

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(self) -> list[WorkItem]:
        if not self._state.is_file():
            return []
        items: list[WorkItem] = []
        for line in self._state.read_text(encoding="utf-8").splitlines():
            if line.strip():
                items.append(WorkItem.model_validate_json(line))
        return items

    def _write(self, items: list[WorkItem]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        body = "".join(item.model_dump_json() + "\n" for item in items)
        fd, tmp = tempfile.mkstemp(dir=str(self.root), prefix=".tmp-queue-", suffix=".jsonl")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._state)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    # -- operations --

    def enqueue(self, item: WorkItem) -> WorkItem:
        """Append a work item to the queue (under lock)."""
        with self._locked():
            items = self._read()
            items.append(item)
            self._write(items)
        return item

    def claim(
        self, worker_id: str, now: datetime, *, visibility_timeout_s: float
    ) -> WorkItem | None:
        """Claim the highest-priority currently-visible item, or ``None`` if none.

        An item is visible when ``visible_at <= now`` (free, or a lapsed prior claim).
        The claim marks it owned and pushes ``visible_at`` a ``visibility_timeout_s``
        into the future so no other worker can take it until the lease lapses."""
        with self._locked():
            items = self._read()
            visible = [it for it in items if _parse(it.visible_at) <= now]
            if not visible:
                return None
            chosen = min(visible, key=lambda it: it._sort_key())
            chosen.claimed_by = worker_id
            chosen.attempts += 1
            chosen.visible_at = _iso(now + timedelta(seconds=visibility_timeout_s))
            self._write(items)
            return chosen.model_copy(deep=True)

    def heartbeat(
        self, item_id: str, worker_id: str, now: datetime, *, visibility_timeout_s: float
    ) -> bool:
        """Extend the visibility lease on an item this worker still owns."""
        with self._locked():
            items = self._read()
            for it in items:
                if it.item_id == item_id and it.claimed_by == worker_id:
                    it.visible_at = _iso(now + timedelta(seconds=visibility_timeout_s))
                    self._write(items)
                    return True
        return False

    def complete(self, item_id: str, worker_id: str) -> bool:
        """Remove an item this worker owns (its slot is released for good)."""
        with self._locked():
            items = self._read()
            kept = [
                it for it in items if not (it.item_id == item_id and it.claimed_by == worker_id)
            ]
            if len(kept) == len(items):
                return False
            self._write(kept)
            return True

    def release(
        self, item_id: str, worker_id: str, *, visible_at: datetime | None = None
    ) -> bool:
        """Return an item this worker owns to the queue (slot released on pause).

        With ``visible_at`` it schedules a delayed resume (the item stays invisible
        until then); without it the item becomes claimable immediately."""
        with self._locked():
            items = self._read()
            for it in items:
                if it.item_id == item_id and it.claimed_by == worker_id:
                    it.claimed_by = None
                    it.visible_at = _iso(visible_at) if visible_at is not None else _EPOCH_ISO
                    self._write(items)
                    return True
        return False

    def release_all_claimed_by(self, worker_id: str) -> int:
        """Free every item currently claimed by ``worker_id`` (stale-worker reclaim)."""
        with self._locked():
            items = self._read()
            freed = 0
            for it in items:
                if it.claimed_by == worker_id:
                    it.claimed_by = None
                    it.visible_at = _EPOCH_ISO
                    freed += 1
            if freed:
                self._write(items)
            return freed

    def snapshot(self) -> list[WorkItem]:
        """A read-only copy of the current queue contents (tests/introspection)."""
        with self._locked():
            return self._read()


def now_utc() -> datetime:
    return datetime.now(UTC)
