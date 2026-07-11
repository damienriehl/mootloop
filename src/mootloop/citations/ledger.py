"""Append-only verification ledger + research-request queue (plan D9/Phase 4).

The ledger is ``law/verifications.jsonl`` — one `VerificationRecord` per line,
append-only, fsync'd; `folded` replays it (staleness-aware) into the current view.
The queue is ``research-requests/queue.jsonl`` — one `ResearchRequest` per line, same
discipline, folded latest-per-``request_id``.

Verification status is thus *derived from the immutable ledger* (plan H8): a persona
can never assert "verified"; only a recorded client outcome can.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from mootloop.models.citations import (
    ResearchRequest,
    VerificationRecord,
    fold_ledger,
)
from mootloop.vault import safe_vault_path

LEDGER_PATH = ("law", "verifications.jsonl")
QUEUE_PATH = ("research-requests", "queue.jsonl")
DEFAULT_MAX_CACHE_AGE_DAYS = 30


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


class VerificationLedger:
    """Append-only JSONL verification cache, folded (staleness-aware) on read."""

    def __init__(self, vault_root: Path | str) -> None:
        self.vault_root = vault_root
        self._path = safe_vault_path(vault_root, *LEDGER_PATH)

    def _records(self) -> list[VerificationRecord]:
        if not self._path.is_file():
            return []
        records: list[VerificationRecord] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(VerificationRecord.model_validate_json(line))
        return records

    def folded(
        self,
        *,
        now: datetime,
        max_cache_age_days: int = DEFAULT_MAX_CACHE_AGE_DAYS,
    ) -> dict[str, VerificationRecord]:
        """Current cache view: latest record per citation, expired ``verified`` ->
        ``pending`` (plan D9)."""
        return fold_ledger(self._records(), now=now, max_cache_age_days=max_cache_age_days)

    def append(self, record: VerificationRecord) -> None:
        _append_line(self._path, record.model_dump_json())


class ResearchQueue:
    """Append-only research-request queue, folded latest-per-``request_id``."""

    def __init__(self, vault_root: Path | str) -> None:
        self.vault_root = vault_root
        self._path = safe_vault_path(vault_root, *QUEUE_PATH)

    def _records(self) -> list[ResearchRequest]:
        if not self._path.is_file():
            return []
        records: list[ResearchRequest] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(ResearchRequest.model_validate_json(line))
        return records

    def folded(self) -> dict[str, ResearchRequest]:
        state: dict[str, ResearchRequest] = {}
        for record in self._records():
            state[record.request_id] = record
        return state

    def open_requests(self) -> list[ResearchRequest]:
        return [r for r in self.folded().values() if r.status == "open"]

    def get(self, request_id: str) -> ResearchRequest | None:
        return self.folded().get(request_id)

    def append(self, request: ResearchRequest) -> None:
        _append_line(self._path, request.model_dump_json())
