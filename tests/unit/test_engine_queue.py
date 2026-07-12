"""Light smoke tests for the file-based driver `Queue` (thorough hammer test in Unit 3)."""

from __future__ import annotations

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
