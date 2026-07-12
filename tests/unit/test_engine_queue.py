"""File-based driver `Queue`: lane priority, visibility timeouts, and the FE-1/FD-6
two-process concurrency gate (advisory-locked appends never corrupt or double-claim)."""

from __future__ import annotations

import multiprocessing as mp
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mootloop.engine.queue import Queue, WorkItem

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _item(lane: str, item_id: str) -> WorkItem:
    return WorkItem.create(
        lane=lane,  # type: ignore[arg-type]
        matter_id="acme-v-widgets",
        run_id="r1",
        kind="run_turn",
        now=NOW,
        item_id=item_id,
    )


def test_interactive_lane_drains_before_run_lane(tmp_path: Path) -> None:
    queue = Queue(tmp_path)
    queue.enqueue(_item("run", "run-1"))
    queue.enqueue(_item("interactive", "int-1"))
    claimed = queue.claim("w1", NOW, visibility_timeout_s=60)
    assert claimed is not None and claimed.item_id == "int-1"


def test_claim_hides_until_visibility_lapses(tmp_path: Path) -> None:
    queue = Queue(tmp_path)
    queue.enqueue(_item("run", "run-1"))
    first = queue.claim("w1", NOW, visibility_timeout_s=60)
    assert first is not None
    # A second claim before the lease lapses sees nothing.
    assert queue.claim("w2", NOW + timedelta(seconds=10), visibility_timeout_s=60) is None
    # After the lease lapses the item is reclaimable (self-healing).
    again = queue.claim("w2", NOW + timedelta(seconds=61), visibility_timeout_s=60)
    assert again is not None and again.item_id == "run-1"


def test_complete_removes_item(tmp_path: Path) -> None:
    queue = Queue(tmp_path)
    queue.enqueue(_item("run", "run-1"))
    claimed = queue.claim("w1", NOW, visibility_timeout_s=60)
    assert claimed is not None
    assert queue.complete("run-1", "w1") is True
    assert queue.snapshot() == []


def test_release_makes_item_immediately_claimable(tmp_path: Path) -> None:
    queue = Queue(tmp_path)
    queue.enqueue(_item("run", "run-1"))
    queue.claim("w1", NOW, visibility_timeout_s=600)
    assert queue.release("run-1", "w1") is True
    reclaimed = queue.claim("w2", NOW, visibility_timeout_s=60)
    assert reclaimed is not None and reclaimed.claimed_by == "w2"


def test_release_all_claimed_by_frees_stale_worker(tmp_path: Path) -> None:
    queue = Queue(tmp_path)
    queue.enqueue(_item("run", "run-1"))
    queue.claim("stale", NOW, visibility_timeout_s=600)
    assert queue.release_all_claimed_by("stale") == 1
    assert queue.claim("w2", NOW, visibility_timeout_s=60) is not None


# --- two-process concurrency gate (plan FD-6) -------------------------------

_PROCS = 4
_PER_PROC = 12
_NOW_ISO = NOW.isoformat()


def _enqueue_worker(root: str, prefix: str, count: int) -> None:
    queue = Queue(root)
    for i in range(count):
        queue.enqueue(
            WorkItem.create(
                lane="run",
                matter_id="acme-v-widgets",
                run_id="r1",
                kind="run_turn",
                now=NOW,
                item_id=f"{prefix}-{i}",
            )
        )


def _claim_worker(root: str, out_path: str) -> None:
    queue = Queue(root)
    now = datetime.fromisoformat(_NOW_ISO)
    claimed: list[str] = []
    # A long visibility timeout keeps claimed items invisible so every process drains a
    # disjoint set; the loop ends when nothing visible remains.
    while True:
        item = queue.claim(f"w-{out_path[-1]}", now, visibility_timeout_s=100_000)
        if item is None:
            break
        claimed.append(item.item_id)
    Path(out_path).write_text("\n".join(claimed), encoding="utf-8")


def test_concurrent_enqueue_no_corruption(tmp_path: Path) -> None:
    ctx = mp.get_context("fork")
    procs = [
        ctx.Process(target=_enqueue_worker, args=(str(tmp_path), f"p{p}", _PER_PROC))
        for p in range(_PROCS)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=30)
        assert proc.exitcode == 0

    # snapshot() parses every JSONL line; a torn/interleaved write would raise here.
    items = Queue(tmp_path).snapshot()
    ids = [it.item_id for it in items]
    assert len(ids) == _PROCS * _PER_PROC  # every enqueue landed
    assert len(set(ids)) == len(ids)  # no line was lost or duplicated


def test_concurrent_claim_never_double_claims(tmp_path: Path) -> None:
    queue = Queue(tmp_path)
    for p in range(_PROCS):
        for i in range(_PER_PROC):
            queue.enqueue(_item("run", f"p{p}-{i}"))
    total = _PROCS * _PER_PROC

    ctx = mp.get_context("fork")
    out_dir = tmp_path / "claims"
    out_dir.mkdir()
    procs = []
    for c in range(_PROCS):
        out = out_dir / f"claimer-{c}"
        procs.append(ctx.Process(target=_claim_worker, args=(str(tmp_path), str(out))))
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=30)
        assert proc.exitcode == 0

    claimed: list[str] = []
    for out in out_dir.iterdir():
        claimed.extend(line for line in out.read_text(encoding="utf-8").splitlines() if line)

    # No item claimed twice, and claimed + still-unclaimed == everything enqueued.
    assert len(claimed) == len(set(claimed))  # exactly-once claim under flock
    remaining = [it for it in queue.snapshot() if it.claimed_by is None]
    assert len(claimed) + len(remaining) == total
    assert len(claimed) == total  # all items were drained
