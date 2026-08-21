"""Journal-backed run-start outbox with at-least-once queue delivery.

Hosted launch commits a ``QueueIntent`` inside the first ``RunStarted`` record. The
queue remains a derived scheduler: recovery validates the immutable run context,
idempotently writes the deterministic work item, and only then journals
``RunEnqueued``. A crash after the queue write and before the acknowledgment can
repeat delivery, so consumers and ``Queue.ensure_enqueued`` must stay idempotent.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from mootloop.context import load_run_context
from mootloop.engine.queue import Queue, WorkItem
from mootloop.errors import MootloopError, OrchestratorError
from mootloop.journal import append, fold, read_events
from mootloop.models.common import MatterId
from mootloop.models.events import (
    JournalEvent,
    QueueIntent,
    RunEnqueued,
    RunStarted,
    validate_run_queue_intent,
)
from mootloop.registry import MatterRegistry
from mootloop.vault import RunLock, safe_vault_path, validate_id

logger = logging.getLogger("mootloop.engine.outbox")


def _started_event(events: Sequence[JournalEvent], run_id: str) -> RunStarted:
    started = [event for event in events if isinstance(event, RunStarted)]
    if len(started) != 1:
        raise OrchestratorError(
            f"run {run_id!r} must have exactly one RunStarted event; found {len(started)}"
        )
    return started[0]


def _is_acknowledged(events: Sequence[JournalEvent], run_id: str, intent: QueueIntent) -> bool:
    acknowledgments = [event for event in events if isinstance(event, RunEnqueued)]
    for event in acknowledgments:
        if (event.item_id, event.payload_sha256) != (
            intent.item_id,
            intent.payload_sha256,
        ):
            raise OrchestratorError(
                f"run {run_id!r} has a RunEnqueued acknowledgment for conflicting work"
            )
    return bool(acknowledgments)


def _work_item(started: RunStarted, intent: QueueIntent) -> WorkItem:
    return WorkItem(
        item_id=intent.item_id,
        lane=intent.lane,
        matter_id=started.matter_id,
        run_id=started.run_id,
        kind=intent.kind,
        enqueued_at=intent.enqueued_at,
        visible_at=intent.enqueued_at,
        payload=intent.payload,
    )


def drain_run_outbox(
    vault_root: Path | str,
    queue: Queue,
    run_id: str,
    *,
    expected_matter_id: MatterId | None = None,
) -> bool:
    """Deliver one pending launch intent and durably acknowledge it.

    Returns ``True`` only when this call writes the acknowledgment. The per-matter
    ``RunLock`` makes repeated drains converge on one logical acknowledgment; the
    queue's deterministic identity makes a post-queue/pre-ack retry safe.
    """
    validate_id(run_id, kind="run_id")
    with RunLock(vault_root, run_id):
        events = read_events(vault_root, run_id)
        started = _started_event(events, run_id)
        if expected_matter_id is not None and started.matter_id != expected_matter_id:
            raise OrchestratorError(
                f"run {run_id!r} belongs to matter {started.matter_id!r}, not "
                f"recovery vault {expected_matter_id!r}"
            )
        intent = started.queue_intent
        if intent is None:
            return False
        try:
            intent = validate_run_queue_intent(
                intent,
                matter_id=started.matter_id,
                run_id=started.run_id,
            )
        except (ValidationError, ValueError) as exc:
            raise OrchestratorError(f"run {run_id!r} has an invalid queue intent") from exc
        acknowledged = _is_acknowledged(events, run_id, intent)
        if acknowledged and fold(events).status != "running":
            return False

        # Validate the exact launch snapshot before materializing any queue work.
        # Live matter/config changes are intentionally irrelevant; tampering fails.
        load_run_context(vault_root, run_id)
        queue.ensure_enqueued(_work_item(started, intent))
        if acknowledged:
            return False
        append(
            vault_root,
            run_id,
            RunEnqueued(item_id=intent.item_id, payload_sha256=intent.payload_sha256),
        )
        return True


def drain_pending_vault_outboxes(
    vault: Path | str,
    queue: Queue,
    matter_id: MatterId,
) -> int:
    """Repair every recoverable pending launch in one verified matter vault."""
    delivered = 0
    runs_dir = safe_vault_path(vault, "runs")
    if not runs_dir.is_dir():
        return delivered
    for child in sorted(runs_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        try:
            if drain_run_outbox(
                vault,
                queue,
                child.name,
                expected_matter_id=matter_id,
            ):
                delivered += 1
        except (MootloopError, OSError, ValidationError):
            logger.exception(
                "could not drain run-start outbox (matter=%s run=%s)",
                matter_id,
                child.name,
            )
    return delivered


def drain_pending_run_outboxes(matters_root: Path | str, queue: Queue) -> int:
    """Scan registered matter vaults and repair every recoverable pending launch.

    One corrupt/locked run is logged and skipped so it cannot starve other matters.
    It is never queued: ``drain_run_outbox`` validates context before queue mutation.
    """
    delivered = 0
    for matter_id, vault in MatterRegistry(root=matters_root).recovery_vaults():
        delivered += drain_pending_vault_outboxes(vault, queue, matter_id)
    return delivered
