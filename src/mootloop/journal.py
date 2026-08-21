"""Append-only run journal — the single source of truth a run folds from.

Events land one-per-line in ``runs/<run-id>/journal.jsonl`` (append + fsync). Turn
bodies are additionally written write-once to ``runs/<run-id>/turns/<turn-id>.json``
(exact-content replay is idempotent; conflicts fail closed). `fold` replays events
into a `RunState` and is a pure function, so resume after a kill is exactly a re-fold.

`read_events` tolerates a *torn final line* (a crash mid-append): it truncates the
file back to the last complete line and warns, never crashing. A corrupt line that
is not the final one is a hard error — that is real corruption, not a torn write.

It also reads INCREMENTALLY. A turn folds the journal about ten times, so parsing
the whole file every call made a run quadratic in its own length: doubling the turns
quadrupled the bytes re-validated. The cache below keeps the parsed prefix of a
journal and re-reads only the bytes appended since, which is sound precisely because
the file is append-only. Soundness is not assumed, though — the cached prefix is
re-verified against the file's bytes on every call (`_prefix_intact`), so a shorter
file, a rewritten one, or another process's torn-tail truncation all fall back to a
full parse. Every event still comes from the file, and the fold is still the pure
replay of every event; nothing about the audit chain is taken on trust.

`tail_events` is the read-only incremental reader (plan FE-1): it returns the events
appended since a byte offset and NEVER truncates. It tolerates a torn final line by
leaving it in place for the writer (advancing the offset only past complete,
newline-terminated lines); a malformed *complete* line still raises — that is real
corruption, not a torn write.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from mootloop.errors import JournalIntegrityError
from mootloop.models.events import (
    CapRaised,
    CheckpointCleared,
    CheckpointReached,
    DecisionRecorded,
    GateEvaluated,
    JournalEvent,
    RunEnqueued,
    RunFinished,
    RunPaused,
    RunReopened,
    RunResumed,
    RunStarted,
    RunState,
    SpendRecorded,
    StageStarted,
    TurnCompleted,
    TurnDiscarded,
    TurnIntent,
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
    first_record = not path.exists()
    line = _EVENT_ADAPTER.dump_json(event).decode("utf-8") + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    if first_record:
        # The file fsync makes the record durable; the directory fsync makes the new
        # journal name durable. Later appends do not change the directory entry.
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)


def write_turn_body(vault_root: Path | str, run_id: str, record: TurnRecord) -> Path:
    """Write a completed turn body once; accept only an exact idempotent replay."""
    path = turn_body_path(vault_root, run_id, record.spec.turn_id)
    serialized = record.model_dump_json(indent=2) + "\n"
    from mootloop.vault import atomic_write_once_text

    try:
        atomic_write_once_text(path, serialized)
    except FileExistsError:
        try:
            existing = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise JournalIntegrityError(f"cannot verify write-once turn body {path.name}") from exc
        if existing == serialized:
            return path
        raise JournalIntegrityError(
            f"conflicting write-once turn body for {record.spec.turn_id}"
        ) from None
    return path


# --- read (torn-line tolerant, incremental) ---------------------------------

# Journals held in the cache. A process drives one run at a time; a handful covers
# export/audit paths that interleave runs without pinning many event lists in memory.
_MAX_CACHED = 4


@dataclass
class _ParsedPrefix:
    """The parsed, newline-terminated prefix of one journal file."""

    offset: int  # bytes consumed: complete lines only, never a trailing partial one
    events: list[JournalEvent]
    digest: bytes  # SHA-256 of every byte before `offset`


_CACHE: OrderedDict[str, _ParsedPrefix] = OrderedDict()


def clear_cache() -> None:
    """Drop every cached prefix, forcing the next read to parse from byte zero."""
    _CACHE.clear()


def _store(key: str, prefix: _ParsedPrefix) -> None:
    _CACHE[key] = prefix
    _CACHE.move_to_end(key)
    while len(_CACHE) > _MAX_CACHED:
        _CACHE.popitem(last=False)


def _prefix_intact(snapshot: bytes, prefix: _ParsedPrefix) -> bool:
    """True when the file still begins with the bytes we parsed into ``prefix``.

    Checks the length (a shorter file means a truncated torn tail, or a rewrite) and
    hashes the complete cached prefix. Anything else and the caller reparses from
    zero — the cache is an optimization that must never decide what the log says.
    """
    if prefix.offset == 0:
        return True
    if len(snapshot) < prefix.offset:
        return False
    return hashlib.sha256(snapshot[: prefix.offset]).digest() == prefix.digest


def read_events(vault_root: Path | str, run_id: str) -> list[JournalEvent]:
    """Read every event, tolerating a torn final line by truncating it away.

    Lines are split on ``\\n`` — the byte the writer actually appends — so a record
    carrying a raw U+2028/U+2029 or form feed stays one line, as `tail_events` has
    always treated it.
    """
    path = journal_path(vault_root, run_id)
    key = str(path)
    if not path.is_file():
        _CACHE.pop(key, None)
        return []

    with path.open("rb") as handle:
        snapshot = handle.read()
    cached = _CACHE.get(key)
    if cached is not None and not _prefix_intact(snapshot, cached):
        cached = None
    start = cached.offset if cached is not None else 0
    events: list[JournalEvent] = list(cached.events) if cached is not None else []
    chunk = snapshot[start:]

    # An empty chunk (nothing appended, or an empty file) falls through: the loop does
    # not run, and the store below re-seats the unchanged prefix and keeps it hot.
    consumed = 0  # bytes of complete lines parsed out of `chunk`
    cacheable = len(events)  # events at `start + consumed`
    pos = 0
    while pos < len(chunk):
        newline = chunk.find(b"\n", pos)
        line = chunk[pos:] if newline == -1 else chunk[pos : newline + 1]
        stripped = line.strip()
        if stripped:
            try:
                events.append(_EVENT_ADAPTER.validate_json(stripped))
            except ValidationError:
                if newline != -1 and newline + 1 != len(chunk):
                    raise  # a bad line with good lines after it is real corruption
                logger.warning(
                    "journal %s: torn final line dropped (%d valid events kept)",
                    run_id,
                    len(events),
                )
                _truncate(path, start + consumed)
                break
        if newline == -1:
            # A final line with no newline that nonetheless parsed. Return it, but
            # leave it out of the cached prefix: only a newline proves the writer
            # finished with it, and the cache must never freeze a half-written line.
            break
        pos = newline + 1
        consumed = pos
        cacheable = len(events)

    offset = start + consumed
    # Hash the same immutable byte snapshot that supplied the parsed events. Reopening
    # here lets a concurrent same-length rewrite pair old events with the new bytes'
    # digest, making that stale pairing look valid on the next cached read.
    digest = hashlib.sha256(snapshot[:offset]).digest()
    _store(key, _ParsedPrefix(offset, events[:cacheable], digest))
    return events


def tail_events(path: Path | str, after_offset: int = 0) -> tuple[list[JournalEvent], int]:
    """Read events appended since ``after_offset``; return ``(events, new_offset)``.

    A read-only incremental reader (plan FE-1) — unlike `read_events`, it NEVER
    truncates the file. It seeks to ``after_offset``, reads to EOF, and parses every
    COMPLETE (newline-terminated) line. A torn/in-progress final line (no trailing
    newline) is left untouched for the writer: it is not parsed and the returned
    offset does not advance past it. Blank lines are skipped but their bytes still
    count toward the offset. A malformed *complete* line raises (real corruption).
    """
    path = Path(path)
    if not path.is_file():
        return [], after_offset
    with path.open("rb") as handle:
        handle.seek(after_offset)
        chunk = handle.read()
    events: list[JournalEvent] = []
    consumed = 0
    start = 0
    while True:
        nl = chunk.find(b"\n", start)
        if nl == -1:
            break  # trailing bytes with no newline are a torn/in-progress line
        line = chunk[start : nl + 1]
        stripped = line.strip()
        if stripped:
            events.append(_EVENT_ADAPTER.validate_json(stripped))
        consumed = nl + 1
        start = nl + 1
    return events, after_offset + consumed


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
            state.context_manifest_sha256 = event.context_manifest_sha256
            state.task_spec_id = event.task_spec_id
            state.mode = event.mode
        elif isinstance(event, StageStarted):
            state.current_stage = event.stage
        elif isinstance(event, TurnCompleted):
            state.completed_turns[event.record.spec.turn_id] = event.record
            # Reconcile the write-ahead intent: a completed turn (even one with no
            # usage) clears its pending max-plausible reservation (plan FD-6).
            state.pending_intents.pop(event.record.spec.turn_id, None)
        elif isinstance(event, TurnDiscarded):
            state.discarded[event.turn_id] = event.attempt
            if event.detail:
                state.discard_details[event.turn_id] = event.detail
        elif isinstance(event, SpendRecorded):
            state.total_spend_usd += event.usd_equiv
            state.total_input_tokens += event.input_tokens
            state.total_cache_read += event.cache_read
            state.total_cache_write += event.cache_write
            state.total_output_tokens += event.output_tokens
            state.pending_intents.pop(event.turn_id, None)  # reconcile intent
        elif isinstance(event, TurnIntent):
            state.pending_intents[event.turn_id] = event.max_plausible_usd
        elif isinstance(event, RunFinished):
            state.status = event.status
        elif isinstance(event, CapRaised):
            state.cap_raised_to = event.to_usd
            if state.status == "capped":
                state.status = "running"  # reopen a graceful cap checkpoint
        elif isinstance(event, CheckpointReached):
            state.status = "checkpoint"
        elif isinstance(event, CheckpointCleared):
            state.cleared_checkpoints.add(event.boundary)
            if state.status == "checkpoint":
                state.status = "running"  # reopen a gated stage-boundary pause
        elif isinstance(event, RunPaused):
            state.status = "paused"
        elif isinstance(event, RunResumed):
            if state.status == "paused":
                state.status = "running"  # reopen an operator/worker pause
        elif isinstance(event, RunReopened):
            # The grant lands whether or not the status flips, so a replayed journal
            # always shows the retry ceiling the run actually ran under.
            state.attempts_granted += event.grant_attempts
            if state.status == "needs_attention":
                state.status = "running"  # reopen an operator-cleared attention halt
        elif isinstance(event, (GateEvaluated, DecisionRecorded, RunEnqueued)):
            pass  # informational; authoritative copies ride on TurnRecord/decisions
    return state


def load_state(vault_root: Path | str, run_id: str) -> RunState:
    """Convenience: read + fold in one call."""
    return fold(read_events(vault_root, run_id))
