"""Hosted-tier access audit: the append-only, hash-chained ``audit/access.jsonl``
store and its integrity check (plan FD-3).

The path is built ONLY through `safe_vault_path` (the realpath-containment
choke-point). Appends are serialized with an advisory ``flock`` so the read-prev /
compute / append sequence stays race-free and the chain never forks (FD-6:
advisory-locked appends, single logical writer). Every append fails closed: if the
line cannot be durably written and fsync'd, `AuditWriteError` propagates and the
caller must abort — a matter-data view or download that cannot be recorded is never
served.
"""

from __future__ import annotations

import fcntl
import os
from datetime import UTC, datetime
from pathlib import Path

from mootloop.errors import AuditWriteError
from mootloop.models.audit import GENESIS_PREV_HASH, AccessAuditEntry
from mootloop.vault import safe_vault_path

# The one audit store, resolved only via `safe_vault_path`.
AUDIT_SUBPATH: tuple[str, ...] = ("audit", "access.jsonl")


def audit_path(vault_root: Path | str) -> Path:
    """The vault's access-audit JSONL path (containment-checked)."""
    return safe_vault_path(vault_root, *AUDIT_SUBPATH)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _last_entry_hash(existing_text: str) -> str:
    """The chain head: the last non-empty entry's ``entry_hash`` (genesis if empty)."""
    for line in reversed(existing_text.splitlines()):
        if line.strip():
            return AccessAuditEntry.model_validate_json(line).entry_hash
    return GENESIS_PREV_HASH


def append(
    vault_root: Path | str,
    *,
    actor: str,
    action: str,
    matter_id: str,
    resource: str,
    ts: str | None = None,
) -> AccessAuditEntry:
    """Append one hash-chained entry, fsync'd, under an exclusive advisory lock.

    Fails closed: any I/O failure raises `AuditWriteError` — the append is not
    considered to have happened unless the byte is durably on disk.
    """
    path = audit_path(vault_root)
    stamp = ts or _utcnow_iso()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            prev_hash = _last_entry_hash(handle.read())
            entry = AccessAuditEntry.create(
                ts=stamp,
                actor=actor,
                action=action,
                matter_id=matter_id,
                resource=resource,
                prev_hash=prev_hash,
            )
            handle.seek(0, os.SEEK_END)
            handle.write(entry.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry
    except OSError as exc:
        raise AuditWriteError(
            f"access-audit append failed for matter {matter_id!r}: {exc}"
        ) from exc


def verify_chain(vault_root: Path | str) -> bool:
    """Fold the JSONL, recompute each ``entry_hash``, and check ``prev_hash`` linkage.

    Returns True only if every entry links to its predecessor and its recorded hash
    matches the recomputation (an empty/absent log is intact). Any tamper — a reorder,
    an edited field, a broken link, or an unparseable line — returns False.
    """
    path = audit_path(vault_root)
    if not path.is_file():
        return True
    prev = GENESIS_PREV_HASH
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = AccessAuditEntry.model_validate_json(line)
        except ValueError:
            return False
        if entry.prev_hash != prev or entry.entry_hash != entry.expected_hash():
            return False
        prev = entry.entry_hash
    return True


def record_download_audit(
    vault_root: Path | str,
    *,
    actor: str,
    matter_id: str,
    resource: str,
) -> AccessAuditEntry:
    """Audit-append helper the download route handlers MUST call FIRST.

    Downloads fail closed: if this raises `AuditWriteError`, the handler must abort
    and return an error — never stream the bytes when the access audit did not
    durably record the download (plan FD-3, threat-model item 13).
    """
    return append(
        vault_root,
        actor=actor,
        action="download",
        matter_id=matter_id,
        resource=resource,
    )
