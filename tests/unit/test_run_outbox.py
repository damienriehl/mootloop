"""Crash-safe run-start outbox: journal first, queue second, restart repair."""

from __future__ import annotations

import multiprocessing as mp
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mootloop import orchestrator
from mootloop.engine.outbox import drain_pending_run_outboxes, drain_run_outbox
from mootloop.engine.queue import Queue, WorkItem
from mootloop.engine.worker import Worker
from mootloop.errors import LockHeldError, OrchestratorError, QueueError
from mootloop.journal import append, clear_cache, fold, read_events
from mootloop.llm import FakeLLMProvider, LLMProvider
from mootloop.models.events import QueueIntent, RunEnqueued, RunStarted
from mootloop.registry import MatterRegistry
from tests.conftest import make_matter

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
MATTER_ID = "acme-v-widgets"
RUN_ID = "outbox-run"


def _vault(tmp_path: Path) -> tuple[Path, Queue]:
    registry = MatterRegistry(root=tmp_path / "matters")
    vault = registry.create(make_matter(MATTER_ID))
    return vault, Queue(registry.root)


def _intent(*, item_id: str = f"run:{MATTER_ID}:{RUN_ID}") -> QueueIntent:
    return QueueIntent.create(
        item_id=item_id,
        lane="run",
        kind="run_turn",
        enqueued_at=NOW.isoformat(),
    )


def _start(vault: Path, intent: QueueIntent | None = None) -> None:
    orchestrator.start_run(
        vault,
        "discovery-responses",
        NOW.isoformat(),
        run_id=RUN_ID,
        queue_intent=intent,
    )


def _concurrent_drain(vault: str, matters_root: str, result_path: str) -> None:
    try:
        result = str(drain_run_outbox(vault, Queue(matters_root), RUN_ID))
    except LockHeldError:
        result = "locked"
    Path(result_path).write_text(result, encoding="utf-8")


def test_historical_run_started_without_intent_still_parses_and_folds(tmp_path: Path) -> None:
    event = RunStarted(
        run_id=RUN_ID,
        matter_id=MATTER_ID,
        task="discovery-responses",
        rubric_version="discovery-responses-v1.0",
        config_digest="abc123",
    )
    append(tmp_path, RUN_ID, event)

    parsed = read_events(tmp_path, RUN_ID)
    assert isinstance(parsed[0], RunStarted)
    assert parsed[0].queue_intent is None
    assert fold(parsed).run_id == RUN_ID


def test_drain_materializes_intent_and_acknowledges_only_after_queue(tmp_path: Path) -> None:
    vault, queue = _vault(tmp_path)
    intent = _intent()
    _start(vault, intent)

    assert drain_run_outbox(vault, queue, RUN_ID) is True
    assert [item.item_id for item in queue.snapshot()] == [intent.item_id]
    events = read_events(vault, RUN_ID)
    assert len([event for event in events if isinstance(event, RunEnqueued)]) == 1

    assert drain_run_outbox(vault, queue, RUN_ID) is False
    acknowledgments = [
        event for event in read_events(vault, RUN_ID) if isinstance(event, RunEnqueued)
    ]
    assert len(acknowledgments) == 1


def test_ensure_failure_leaves_pending_outbox_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, queue = _vault(tmp_path)
    _start(vault, _intent())
    original = queue.ensure_enqueued

    def fail(_item: WorkItem) -> WorkItem:
        raise QueueError("injected queue failure")

    monkeypatch.setattr(queue, "ensure_enqueued", fail)
    with pytest.raises(QueueError, match="injected"):
        drain_run_outbox(vault, queue, RUN_ID)
    assert not any(isinstance(event, RunEnqueued) for event in read_events(vault, RUN_ID))

    monkeypatch.setattr(queue, "ensure_enqueued", original)
    assert drain_run_outbox(vault, queue, RUN_ID) is True


def test_crash_after_queue_before_ack_is_idempotently_repaired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, queue = _vault(tmp_path)
    intent = _intent()
    _start(vault, intent)
    import mootloop.engine.outbox as outbox

    original = outbox.append

    def fail_ack(vault_root: Path | str, run_id: str, event: object) -> None:
        if isinstance(event, RunEnqueued):
            raise OSError("crash before ack")
        original(vault_root, run_id, event)  # type: ignore[arg-type]

    monkeypatch.setattr(outbox, "append", fail_ack)
    with pytest.raises(OSError, match="before ack"):
        drain_run_outbox(vault, queue, RUN_ID)
    assert [item.item_id for item in queue.snapshot()] == [intent.item_id]

    monkeypatch.setattr(outbox, "append", original)
    assert drain_run_outbox(vault, queue, RUN_ID) is True
    assert [item.item_id for item in queue.snapshot()] == [intent.item_id]


def test_retry_reestablishes_queue_parent_durability_before_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, queue = _vault(tmp_path)
    intent = _intent()
    _start(vault, intent)
    import mootloop.engine.queue as queue_module

    original_fsync = os.fsync
    calls = 0

    def fail_parent_once(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected parent fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(queue_module.os, "fsync", fail_parent_once)
    with pytest.raises(OSError, match="parent fsync"):
        drain_run_outbox(vault, queue, RUN_ID)
    assert [item.item_id for item in queue.snapshot()] == [intent.item_id]
    assert not any(isinstance(event, RunEnqueued) for event in read_events(vault, RUN_ID))

    monkeypatch.setattr(queue_module.os, "fsync", original_fsync)
    assert drain_run_outbox(vault, queue, RUN_ID) is True
    assert any(isinstance(event, RunEnqueued) for event in read_events(vault, RUN_ID))


def test_conflicting_queue_item_fails_closed_without_ack(tmp_path: Path) -> None:
    vault, queue = _vault(tmp_path)
    intent = _intent()
    _start(vault, intent)
    queue.enqueue(
        WorkItem.create(
            lane="interactive",
            matter_id=MATTER_ID,
            run_id=RUN_ID,
            kind="different",
            now=NOW,
            item_id=intent.item_id,
        )
    )

    with pytest.raises(QueueError, match="conflicting work"):
        drain_run_outbox(vault, queue, RUN_ID)
    assert not any(isinstance(event, RunEnqueued) for event in read_events(vault, RUN_ID))


def test_launch_rejects_cross_run_or_arbitrary_intent(tmp_path: Path) -> None:
    vault, _queue = _vault(tmp_path)
    with pytest.raises(OrchestratorError, match="invalid hosted run queue intent"):
        orchestrator.start_run(
            vault,
            "discovery-responses",
            NOW.isoformat(),
            run_id=RUN_ID,
            queue_intent=_intent(item_id="run:other"),
        )
    assert read_events(vault, RUN_ID) == []


def test_post_validation_payload_mutation_fails_before_commit(tmp_path: Path) -> None:
    vault, queue = _vault(tmp_path)
    intent = _intent()
    intent.payload["later"] = "mutation"

    with pytest.raises(OrchestratorError, match="invalid hosted run queue intent"):
        _start(vault, intent)
    assert queue.snapshot() == []


def test_cached_intent_payload_mutation_is_revalidated_at_delivery(tmp_path: Path) -> None:
    vault, queue = _vault(tmp_path)
    _start(vault, _intent())
    started = next(
        event for event in read_events(vault, RUN_ID) if isinstance(event, RunStarted)
    )
    assert started.queue_intent is not None
    started.queue_intent.payload["later"] = "mutation"

    with pytest.raises(OrchestratorError, match="invalid queue intent"):
        drain_run_outbox(vault, queue, RUN_ID)
    assert queue.snapshot() == []


def test_live_source_mutation_does_not_change_replayed_queue_work(tmp_path: Path) -> None:
    vault, queue = _vault(tmp_path)
    intent = _intent()
    _start(vault, intent)
    matter_path = vault / "matter.yaml"
    matter_path.write_text(matter_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert drain_run_outbox(vault, queue, RUN_ID) is True
    assert queue.snapshot()[0].item_id == intent.item_id


def test_tampered_context_never_queues(tmp_path: Path) -> None:
    vault, queue = _vault(tmp_path)
    _start(vault, _intent())
    manifest = vault / "runs" / RUN_ID / "context" / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")

    with pytest.raises(OrchestratorError):
        drain_run_outbox(vault, queue, RUN_ID)
    assert queue.snapshot() == []


def test_registry_scan_repairs_pending_outboxes(tmp_path: Path) -> None:
    vault, queue = _vault(tmp_path)
    _start(vault, _intent())

    assert drain_pending_run_outboxes(tmp_path / "matters", queue) == 1
    assert [item.item_id for item in queue.snapshot()] == [f"run:{MATTER_ID}:{RUN_ID}"]


def test_worker_restart_repairs_before_claiming(tmp_path: Path) -> None:
    vault, queue = _vault(tmp_path)
    _start(vault, _intent())

    def provider_factory(_vault: Path, _run_dir: Path, _billing: str) -> LLMProvider:
        return FakeLLMProvider()

    worker = Worker(tmp_path / "matters", "restart-worker", queue, provider_factory)
    assert worker.run_once(NOW) is True
    assert queue.snapshot() == []
    assert any(isinstance(event, RunEnqueued) for event in read_events(vault, RUN_ID))


def test_run_enqueued_is_informational_to_fold() -> None:
    started = RunStarted(
        run_id=RUN_ID,
        matter_id=MATTER_ID,
        task="discovery-responses",
        rubric_version="discovery-responses-v1.0",
        config_digest="abc123",
        queue_intent=_intent(),
    )
    before = fold([started])
    after = fold(
        [
            started,
            RunEnqueued(
                item_id=started.queue_intent.item_id,
                payload_sha256=started.queue_intent.payload_sha256,
            ),
        ]
    )
    assert after == before


def test_concurrent_drains_converge_on_one_item_and_ack(tmp_path: Path) -> None:
    vault, queue = _vault(tmp_path)
    _start(vault, _intent())
    ctx = mp.get_context("fork")
    results = [tmp_path / f"drain-{index}" for index in range(2)]
    processes = [
        ctx.Process(
            target=_concurrent_drain,
            args=(str(vault), str(tmp_path / "matters"), str(result)),
        )
        for result in results
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    # A simultaneous contender may observe the live RunLock, but retry convergence is
    # one deterministic queue item and one logical acknowledgment.
    drain_run_outbox(vault, queue, RUN_ID)
    clear_cache()
    assert [item.item_id for item in queue.snapshot()] == [f"run:{MATTER_ID}:{RUN_ID}"]
    acknowledgments = [
        event for event in read_events(vault, RUN_ID) if isinstance(event, RunEnqueued)
    ]
    assert len(acknowledgments) == 1
