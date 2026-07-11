"""Append-only run journal — the single source of truth a run folds from.

Events land one-per-line in ``runs/<run-id>/journal.jsonl`` (append + fsync). Turn
bodies are additionally written write-once to ``runs/<run-id>/turns/<turn-id>.json``
(skip-if-exists — idempotent under discard/relaunch). `fold` replays events into a
`RunState` and is a pure function, so resume after a kill is exactly a re-fold.

`read_events` tolerates a *torn final line* (a crash mid-append): it truncates the
file back to the last complete line and warns, never crashing. A corrupt line that
is not the final one is a hard error — that is real corruption, not a torn write.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from mootloop.models.events import (
    CapRaised,
    GateEvaluated,
    JournalEvent,
    RunFinished,
    RunStarted,
    RunState,
    SpendRecorded,
    StageStarted,
    TurnCompleted,
    TurnDiscarded,
)
from mootloop.models.run import TurnRecord
from mootloop.vault import safe_vault_path

logger = logging.getLogger("mootloop.journal")

_EVENT_ADAPTER: TypeAdapter[JournalEvent] = TypeAdapter(JournalEvent)


def journal_path(vault_root: Path | str, run_id: str) -> Path:
    return safe_vault_path(vault_root, "runs", run_id, "journal.jsonl")


def turn_body_path(vault_root: Path | str, run_id: str, turn_id: str) -> Path:
    return safe_vault_path(vault_root, "runs", run_id, "turns", f"{turn_id}.json")


# --- append -----------------------------------------------------------------


def append(vault_root: Path | str, run_id: str, event: JournalEvent) -> None:
    """Serialize ``event`` and append it as one fsync'd line."""
    path = journal_path(vault_root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = _EVENT_ADAPTER.dump_json(event).decode("utf-8") + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def write_turn_body(vault_root: Path | str, run_id: str, record: TurnRecord) -> Path:
    """Write a completed turn's body write-once (skip if it already exists)."""
    path = turn_body_path(vault_root, run_id, record.spec.turn_id)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same-dir temp + atomic replace keeps a crash from leaving a torn body.
    from mootloop.vault import atomic_write_text

    atomic_write_text(path, record.model_dump_json(indent=2) + "\n")
    return path


# --- read (torn-line tolerant) ----------------------------------------------


def read_events(vault_root: Path | str, run_id: str) -> list[JournalEvent]:
    """Read every event, tolerating a torn final line by truncating it away."""
    path = journal_path(vault_root, run_id)
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    events: list[JournalEvent] = []
    good_bytes = 0
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            good_bytes += len(line.encode("utf-8"))
            continue
        try:
            events.append(_EVENT_ADAPTER.validate_json(stripped))
        except ValidationError:
            is_last = idx == len(lines) - 1
            if is_last:
                logger.warning(
                    "journal %s: torn final line dropped (%d valid events kept)",
                    run_id,
                    len(events),
                )
                _truncate(path, good_bytes)
                break
            raise
        good_bytes += len(line.encode("utf-8"))
    return events


def _truncate(path: Path, size: int) -> None:
    """Best-effort truncate to the last complete line so future appends stay clean."""
    try:
        with path.open("r+b") as handle:
            handle.truncate(size)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:  # pragma: no cover - defensive; a read-only FS still folds fine
        logger.warning("journal %s: could not truncate torn tail", path)


# --- fold (pure) ------------------------------------------------------------


def fold(events: list[JournalEvent]) -> RunState:
    """Replay events into the derived `RunState`. Pure and total (no I/O)."""
    state = RunState()
    for event in events:
        if isinstance(event, RunStarted):
            state.run_id = event.run_id
            state.matter_id = event.matter_id
            state.task = event.task
            state.rubric_version = event.rubric_version
        elif isinstance(event, StageStarted):
            state.current_stage = event.stage
        elif isinstance(event, TurnCompleted):
            state.completed_turns[event.record.spec.turn_id] = event.record
        elif isinstance(event, TurnDiscarded):
            state.discarded[event.turn_id] = event.attempt
        elif isinstance(event, SpendRecorded):
            state.total_spend_usd += event.usd_equiv
            state.total_input_tokens += event.input_tokens
            state.total_cache_read += event.cache_read
            state.total_cache_write += event.cache_write
            state.total_output_tokens += event.output_tokens
        elif isinstance(event, RunFinished):
            state.status = event.status
        elif isinstance(event, CapRaised):
            state.cap_raised_to = event.to_usd
            if state.status == "capped":
                state.status = "running"  # reopen a graceful cap checkpoint
        elif isinstance(event, GateEvaluated):
            pass  # informational; the authoritative gate copy rides on the TurnRecord
    return state


def load_state(vault_root: Path | str, run_id: str) -> RunState:
    """Convenience: read + fold in one call."""
    return fold(read_events(vault_root, run_id))
