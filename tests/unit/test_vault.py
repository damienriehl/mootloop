"""Unit tests for the vault module."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from mootloop.errors import LockHeldError, MatterConfigError, VaultBoundaryError
from mootloop.models.matter import MatterConfig
from mootloop.vault import (
    MATTER_YAML,
    RunLock,
    assert_vault_outside_repo,
    create_vault,
    detect_sync_folder,
    load_matter,
    safe_vault_path,
    validate_id,
)

# --- ID validation ----------------------------------------------------------


@pytest.mark.parametrize("good", ["a", "acme-v-widgets", "m1", "a.b_c-d", "0" + "a" * 63])
def test_validate_id_accepts(good: str) -> None:
    assert validate_id(good) == good


@pytest.mark.parametrize(
    "bad",
    [".", "..", "-leading", "_leading", "Acme", "has space", "a/b", "a\\b", "x" * 65, ""],
)
def test_validate_id_rejects(bad: str) -> None:
    with pytest.raises(VaultBoundaryError):
        validate_id(bad)


# --- safe_vault_path traversal ---------------------------------------------


def test_safe_vault_path_allows_within(tmp_path: Path) -> None:
    result = safe_vault_path(tmp_path, "facts", "f1.json")
    assert str(result).startswith(str(tmp_path.resolve()))


@pytest.mark.parametrize("part", ["../escape", "../../etc/passwd"])
def test_safe_vault_path_rejects_dotdot(tmp_path: Path, part: str) -> None:
    with pytest.raises(VaultBoundaryError):
        safe_vault_path(tmp_path, part)


def test_safe_vault_path_rejects_absolute(tmp_path: Path) -> None:
    with pytest.raises(VaultBoundaryError):
        safe_vault_path(tmp_path, "/etc/passwd")


def test_safe_vault_path_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    link = vault / "sneaky"
    link.symlink_to(outside)  # real symlink pointing outside the vault
    with pytest.raises(VaultBoundaryError):
        safe_vault_path(vault, "sneaky", "secret.txt")


# --- vault outside repo -----------------------------------------------------


def test_vault_inside_repo_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    vault = repo / "matters" / "m1"
    vault.mkdir(parents=True)
    with pytest.raises(VaultBoundaryError):
        assert_vault_outside_repo(vault, repo)


def test_repo_inside_vault_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    repo = vault / "repo"
    repo.mkdir(parents=True)
    with pytest.raises(VaultBoundaryError):
        assert_vault_outside_repo(vault, repo)


def test_disjoint_vault_repo_ok(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    repo.mkdir()
    vault.mkdir()
    assert_vault_outside_repo(vault, repo)  # no raise


# --- sync-folder detection --------------------------------------------------


def test_detect_sync_folder_name_marker(tmp_path: Path) -> None:
    vault = tmp_path / "Dropbox" / "matters" / "m1"
    vault.mkdir(parents=True)
    assert detect_sync_folder(vault) == "Dropbox"


def test_detect_sync_folder_file_marker(tmp_path: Path) -> None:
    (tmp_path / ".dropbox").write_text("x")
    vault = tmp_path / "m1"
    vault.mkdir()
    assert detect_sync_folder(vault) == ".dropbox"


def test_detect_sync_folder_none(tmp_path: Path) -> None:
    vault = tmp_path / "plain" / "m1"
    vault.mkdir(parents=True)
    assert detect_sync_folder(vault) is None


# --- create + load ----------------------------------------------------------


def test_create_vault_builds_tree_and_canary(tmp_path: Path, matter: MatterConfig) -> None:
    vault = tmp_path / "vault"
    registry = tmp_path / "canaries.json"
    create_vault(vault, matter, registry_path=registry)
    for sub in (
        "corpus/originals",
        "corpus/normalized",
        "facts",
        "requests",
        "law",
        "runs",
        "deliverables",
        "learnings",
        "research-requests",
    ):
        assert (vault / sub).is_dir()
    assert (vault / MATTER_YAML).is_file()
    assert (vault / ".canary").is_file()
    assert registry.is_file()


def test_create_vault_refuses_nonempty(tmp_path: Path, matter: MatterConfig) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "junk.txt").write_text("x")
    with pytest.raises(VaultBoundaryError):
        create_vault(vault, matter, registry_path=tmp_path / "c.json")


def test_round_trip_load_matter(tmp_path: Path, matter: MatterConfig) -> None:
    vault = tmp_path / "vault"
    create_vault(vault, matter, registry_path=tmp_path / "c.json")
    loaded = load_matter(vault)
    assert loaded.matter_id == matter.matter_id
    assert loaded.our_side == "defendant"


def test_load_matter_missing_field_names_it(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / MATTER_YAML).write_text(
        "schema_version: '1.0'\nmatter_id: m1\n"  # missing caption, jurisdiction, etc.
    )
    with pytest.raises(MatterConfigError) as exc:
        load_matter(vault)
    assert "caption" in str(exc.value)


def test_load_matter_missing_file(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(MatterConfigError):
        load_matter(vault)


# --- run lock ---------------------------------------------------------------


def test_run_lock_context_manager_releases(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "runs").mkdir(parents=True)
    with RunLock(vault, "run-1") as lock:
        assert lock._path.is_file()
    assert not lock._path.is_file()


def test_run_lock_heartbeat_updates(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "runs").mkdir(parents=True)
    with RunLock(vault, "run-1") as lock:
        before = json.loads(lock._path.read_text())["heartbeat_at"]
        lock.heartbeat()
        after = json.loads(lock._path.read_text())
        assert after["heartbeat_at"] >= before
        assert after["started_at"] <= after["heartbeat_at"]


def test_run_lock_takes_over_dead_pid(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    lock_path = vault / "runs" / ".lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 999999,  # not a live process
                "hostname": __import__("socket").gethostname(),
                "run_id": "old",
                "started_at": "2020-01-01T00:00:00+00:00",
                "heartbeat_at": "2020-01-01T00:00:00+00:00",
            }
        )
    )
    with RunLock(vault, "run-2") as lock:
        assert json.loads(lock._path.read_text())["run_id"] == "run-2"


def test_run_lock_takes_over_stale_heartbeat(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    lock_path = vault / "runs" / ".lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),  # alive
                "hostname": __import__("socket").gethostname(),
                "run_id": "old",
                "started_at": "2020-01-01T00:00:00+00:00",
                "heartbeat_at": "2020-01-01T00:00:00+00:00",  # ancient
            }
        )
    )
    lock = RunLock(vault, "run-3", heartbeat_threshold=timedelta(minutes=15))
    lock.acquire()
    assert json.loads(lock._path.read_text())["run_id"] == "run-3"
    lock.release()


def test_run_lock_refuses_live_local_lock(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "runs").mkdir(parents=True)
    held = RunLock(vault, "run-live")
    held.acquire()
    contender = RunLock(vault, "run-other")
    with pytest.raises(LockHeldError):
        contender.acquire()
    held.release()


def test_run_lock_refuses_cross_host_without_override(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    lock_path = vault / "runs" / ".lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 4242,
                "hostname": "some-other-host",
                "run_id": "old",
                "started_at": "2020-01-01T00:00:00+00:00",
                "heartbeat_at": "2020-01-01T00:00:00+00:00",
            }
        )
    )
    with pytest.raises(LockHeldError):
        RunLock(vault, "run-4").acquire()
    # override succeeds
    lock = RunLock(vault, "run-4", override=True)
    lock.acquire()
    assert json.loads(lock._path.read_text())["run_id"] == "run-4"
    lock.release()
