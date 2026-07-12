"""Light smoke tests for the driver-coordinated vault backup (thorough set in Unit 3)."""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from mootloop.engine.backup import backup_matter
from mootloop.errors import BackupError
from mootloop.vault import init_vault

NOW = "2026-07-12T00:00:00+00:00"


def _make_vault(tmp_path: Path) -> Path:
    from tests.conftest import make_matter

    vault = tmp_path / "vault"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
    (vault / "staging").mkdir(exist_ok=True)
    (vault / "staging" / "scratch.txt").write_text("working file", encoding="utf-8")
    return vault


def test_backup_writes_tar_excluding_staging(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    dest = tmp_path / "backups"
    out = backup_matter(vault, dest, NOW)
    assert out.exists() and out.suffix == ".gz"
    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("/matter.yaml") for n in names)
    assert not any("/staging/" in n for n in names)  # staging excluded


def test_backup_refuses_destination_inside_git_repo(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(BackupError):
        backup_matter(vault, repo / "backups", NOW)


def test_backup_refuses_destination_inside_sync_folder(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    synced = tmp_path / "Dropbox"
    (synced / ".dropbox").mkdir(parents=True)  # a sync-client root marker
    with pytest.raises(BackupError):
        backup_matter(vault, synced / "backups", NOW)


def test_backup_readback_confirms_matter_yaml_member(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    out = backup_matter(vault, tmp_path / "backups", NOW)
    # The readback gate (fail-closed) only returns a path once matter.yaml is present.
    with tarfile.open(out, "r:gz") as tar:
        assert any(n.endswith("/matter.yaml") for n in tar.getnames())
