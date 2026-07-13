"""The hosted driver engine (plan FE-1).

This package turns the sync orchestrator core into a supervised, sandboxed service:

- `HeadlessClaudeProvider` — an `LLMProvider` that runs each persona turn as a
  sandboxed ``claude -p`` subprocess on the operator's Max-plan subscription.
- `Queue` / `WorkItem` — a file-based, two-lane priority work queue (no Redis),
  concurrency-safe under ``flock`` and crash-safe via atomic rewrites.
- `Worker` — the driver loop that claims work, drains a run's turns through a provider,
  and routes seat-limit / auth / turn failures without ever holding the run lock across
  a model call.
- `backup_matter` — a driver-coordinated, lock-consistent, encrypted vault snapshot.
- `restore_matter` — the fail-closed, traversal-safe inverse (plan FD-6 restore drill).
"""

from __future__ import annotations

from mootloop.engine.backup import backup_matter, restore_matter
from mootloop.engine.claude_provider import HeadlessClaudeProvider
from mootloop.engine.queue import Queue, WorkItem
from mootloop.engine.worker import Worker

__all__ = [
    "HeadlessClaudeProvider",
    "Queue",
    "WorkItem",
    "Worker",
    "backup_matter",
    "restore_matter",
]
