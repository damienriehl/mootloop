"""Server-Sent Events for a live run's journal (plan FE-1).

The journal is the run's single source of truth, so a stream is just an incremental
tail of ``journal.jsonl``: `journal.tail_events` returns the events appended past a byte
offset (read-only, never truncating — unlike ``read_events``). We format each event as
one SSE ``data:`` frame, advance the offset, emit a keep-alive comment on idle, and stop
once the run reaches a terminal state or a bounded max-duration lapses (so a test can
read the whole stream without it hanging forever).

`format_sse` and `iter_sse_lines` are pure/sync and unit-testable without the ASGI
layer; only `sse_run_events` is async, because the ASGI streaming response requires it.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from mootloop.journal import journal_path, load_state, tail_events

# Bounds keep the async generator from streaming forever (env-overridable for ops).
_MAX_DURATION_ENV = "MOOTLOOP_SSE_MAX_SECONDS"
_POLL_INTERVAL_ENV = "MOOTLOOP_SSE_POLL_SECONDS"
_DEFAULT_MAX_DURATION = 30.0
_DEFAULT_POLL_INTERVAL = 0.5
_KEEPALIVE_AFTER = 30.0

KEEPALIVE_FRAME = ": keep-alive\n\n"


def format_sse(event_json: str) -> str:
    """Frame one JSON event line as an SSE ``data:`` event (pure)."""
    return f"data: {event_json}\n\n"


def iter_sse_lines(vault_root: Path | str, run_id: str, *, after_offset: int = 0) -> Iterator[str]:
    """Yield SSE ``data:`` frames for events appended past ``after_offset`` (one pass).

    Pure and sync — a unit test can exercise the tail-to-SSE formatting without ASGI."""
    events, _new_offset = tail_events(journal_path(vault_root, run_id), after_offset)
    for event in events:
        yield format_sse(event.model_dump_json())


def _bounds() -> tuple[float, float]:
    max_duration = float(os.environ.get(_MAX_DURATION_ENV, _DEFAULT_MAX_DURATION))
    poll_interval = float(os.environ.get(_POLL_INTERVAL_ENV, _DEFAULT_POLL_INTERVAL))
    return max_duration, poll_interval


async def sse_run_events(vault_root: Path | str, run_id: str) -> AsyncIterator[str]:
    """Stream a run's journal as SSE frames until it is terminal or the bound lapses."""
    max_duration, poll_interval = _bounds()
    loop = asyncio.get_event_loop()
    path = journal_path(vault_root, run_id)
    offset = 0
    started = loop.time()
    last_emit = started
    while True:
        events, offset = tail_events(path, offset)
        now = loop.time()
        if events:
            for event in events:
                yield format_sse(event.model_dump_json())
            last_emit = now
        if load_state(vault_root, run_id).is_terminal:
            return
        if now - started >= max_duration:
            return
        if now - last_emit >= _KEEPALIVE_AFTER:
            yield KEEPALIVE_FRAME
            last_emit = now
        await asyncio.sleep(poll_interval)
