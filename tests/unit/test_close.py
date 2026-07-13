"""Unit tests for the FD-6 matter-close service."""

from __future__ import annotations

from pathlib import Path

import pytest

from mootloop.close import (
    CLOSED_DIRNAME,
    TOMBSTONES_FILE,
    close_matter,
)
from mootloop.errors import CloseError, VaultBoundaryError
from mootloop.models.audit import AccessAuditEntry
from mootloop.models.lifecycle import CloseRecord
from mootloop.vault import RunLock, init_vault
from mootloop.web import audit
from tests.conftest import make_matter

NOW = "2026-07-13T00:00:00+00:00"
MID = "acme-v-widgets"


def _make_matters_root(tmp_path: Path) -> Path:
    root = tmp_path / "matters"
    vault = root / MID
    init_vault(vault, make_matter(MID), registry_path=tmp_path / "canaries.json")
    # Seed some confidential content across a few stores.
    (vault / "facts" / "facts.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (vault / "corpus" / "originals" / "doc.pdf").write_text("secret", encoding="utf-8")
    return root


def test_close_purges_vault_and_records(tmp_path: Path) -> None:
    root = _make_matters_root(tmp_path)
    record = close_matter(
        root, MID, actor="Clerk", now=NOW, backup_dir=tmp_path / "backups"
    )
    assert isinstance(record, CloseRecord)
    assert not (root / MID).exists()  # vault subtree is gone
    assert record.removed_counts["facts"] == 1
    assert record.removed_counts["matter-config"] == 1


def test_close_backs_up_first(tmp_path: Path) -> None:
    root = _make_matters_root(tmp_path)
    dest = tmp_path / "backups"
    record = close_matter(root, MID, actor="Clerk", now=NOW, backup_dir=dest)
    assert record.backup_ref is not None
    assert Path(record.backup_ref).exists()
    assert list(dest.glob("*.tar.gz.enc"))  # hosted close produces an encrypted snapshot


def test_close_requires_backup_dir_by_default(tmp_path: Path) -> None:
    root = _make_matters_root(tmp_path)
    with pytest.raises(CloseError):
        close_matter(root, MID, actor="Clerk", now=NOW, backup_dir=None)
    assert (root / MID).exists()  # nothing purged on refusal


def test_skip_backup_needs_acknowledgement(tmp_path: Path) -> None:
    root = _make_matters_root(tmp_path)
    with pytest.raises(CloseError):
        close_matter(root, MID, actor="Clerk", now=NOW, backup_dir=None, skip_backup=True)
    assert (root / MID).exists()


def test_skip_backup_acknowledged_closes(tmp_path: Path) -> None:
    root = _make_matters_root(tmp_path)
    record = close_matter(
        root,
        MID,
        actor="Clerk",
        now=NOW,
        backup_dir=None,
        skip_backup=True,
        acknowledge_skip_backup=True,
    )
    assert record.backup_ref is None
    assert not (root / MID).exists()


def test_close_refuses_on_active_lock(tmp_path: Path) -> None:
    root = _make_matters_root(tmp_path)
    with RunLock(root / MID, "live-run"), pytest.raises(CloseError):
        close_matter(root, MID, actor="Clerk", now=NOW, backup_dir=tmp_path / "b")
    assert (root / MID).exists()  # a live run blocks the purge


def test_tombstone_retained_and_anonymized(tmp_path: Path) -> None:
    root = _make_matters_root(tmp_path)
    record = close_matter(root, MID, actor="Clerk", now=NOW, backup_dir=tmp_path / "b")
    tomb = record.tombstone
    assert tomb.action == "matter-closed"
    assert tomb.resource == ""  # no party-derived content
    assert tomb.expected_hash() == tomb.entry_hash  # self-consistent
    # The off-vault close log survives the purge.
    tomb_file = root / CLOSED_DIRNAME / TOMBSTONES_FILE
    assert tomb_file.is_file()
    persisted = AccessAuditEntry.model_validate_json(tomb_file.read_text().splitlines()[-1])
    assert persisted.entry_hash == tomb.entry_hash
    assert (root / CLOSED_DIRNAME / f"{MID}.json").is_file()


def test_tombstone_preserves_audit_chain(tmp_path: Path) -> None:
    root = _make_matters_root(tmp_path)
    vault = root / MID
    entry = audit.append(
        vault, actor="viewer", action="view", matter_id=MID, resource="page", ts=NOW
    )
    record = close_matter(root, MID, actor="Clerk", now=NOW, backup_dir=tmp_path / "b")
    # The tombstone links to the pre-close chain head.
    assert record.tombstone.prev_hash == entry.entry_hash


def test_re_close_is_safe(tmp_path: Path) -> None:
    root = _make_matters_root(tmp_path)
    close_matter(root, MID, actor="Clerk", now=NOW, backup_dir=tmp_path / "b")
    # Re-closing an already-purged matter is refused, not a destructive no-op elsewhere.
    with pytest.raises(CloseError):
        close_matter(root, MID, actor="Clerk", now=NOW, backup_dir=tmp_path / "b2")


def test_crafted_matter_id_cannot_escape(tmp_path: Path) -> None:
    root = _make_matters_root(tmp_path)
    with pytest.raises(VaultBoundaryError):
        close_matter(root, "../etc", actor="Clerk", now=NOW, backup_dir=tmp_path / "b")
