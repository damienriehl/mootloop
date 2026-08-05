"""Unit tests for the vault module."""

from __future__ import annotations

import json
import multiprocessing
import os
import time
from datetime import timedelta
from pathlib import Path

import pytest

from mootloop.errors import LockHeldError, MatterConfigError, VaultBoundaryError
from mootloop.models.matter import MatterConfig
from mootloop.vault import (
    MATTER_YAML,
    RunLock,
    _real,
    assert_vault_outside_repo,
    create_vault,
    detect_sync_folder,
    enclosing_git_repo,
    init_vault,
    load_matter,
    safe_vault_path,
    validate_id,
)
from tests.conftest import make_matter

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


# --- what counts as an enclosing repo ---------------------------------------


def test_enclosing_git_repo_finds_a_real_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    vault = repo / "matters" / "m1"
    vault.mkdir(parents=True)
    assert enclosing_git_repo(vault) == _real(repo)


def test_enclosing_git_repo_accepts_a_gitlink_file(tmp_path: Path) -> None:
    """Worktrees and submodules carry a .git *file* pointing at the real dir."""
    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n", encoding="utf-8")
    assert enclosing_git_repo(repo) == _real(repo)


def test_empty_git_directory_is_not_a_repo(tmp_path: Path) -> None:
    """A stray empty .git (they turn up in shared parents like /tmp) must not
    make every directory beneath it look like a repo — git would not resolve it."""
    (tmp_path / ".git").mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    assert enclosing_git_repo(vault) is None


def test_stray_git_directory_does_not_block_vault_creation(
    tmp_path: Path, matter: MatterConfig
) -> None:
    """End-to-end: a vault under a stray empty .git still initializes."""
    (tmp_path / ".git").mkdir()
    vault = init_vault(
        tmp_path / "vault",
        matter,
        registry_path=tmp_path / "canaries.json",
    )
    assert (vault / MATTER_YAML).is_file()


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


# --- run lock: acquisition is atomic, not read-then-write --------------------


def _hammer_lock(vault: Path, barrier_dir: Path, idx: int) -> int:
    """Child process: take the lock, prove exclusivity with a marker file, release."""
    marker = barrier_dir / "held"
    try:
        with RunLock(vault, f"run-{idx}"):
            if marker.exists():
                return 2  # someone else was inside the critical section
            marker.write_text(str(idx), encoding="utf-8")
            time.sleep(0.02)
            marker.unlink()
            return 0
    except LockHeldError:
        return 1  # correctly refused — this is the expected loser outcome


def test_concurrent_acquire_never_lets_two_processes_in(tmp_path: Path) -> None:
    """`acquire` used to read, then write, with no atomic gate. Two processes that both
    read "free" before either wrote both believed they held the lock — and this lock is
    the only thing serializing `record_turn`'s load-fold-append cycle."""
    vault = tmp_path / "vault"
    (vault / "runs").mkdir(parents=True)
    barrier = tmp_path / "barrier"
    barrier.mkdir()

    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(6) as pool:
        codes = pool.starmap(_hammer_lock, [(vault, barrier, i) for i in range(6)])

    assert 2 not in codes, f"two processes were inside the critical section: {codes}"
    assert codes.count(0) >= 1, f"nobody acquired the lock: {codes}"


def test_unreadable_lock_file_fails_closed(tmp_path: Path) -> None:
    """A lock whose contents cannot be parsed is a blocker, not a green light — the
    same posture the cross-host branch already takes. Overriding is the escape hatch."""
    vault = tmp_path / "vault"
    lock_path = vault / "runs" / ".lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("not json at all", encoding="utf-8")

    with pytest.raises(LockHeldError):
        RunLock(vault, "run-x").acquire()

    lock = RunLock(vault, "run-x", override=True)
    lock.acquire()
    assert json.loads(lock_path.read_text())["run_id"] == "run-x"
    lock.release()


def test_lock_file_is_never_observable_half_written(tmp_path: Path) -> None:
    """The lock is published by `os.link` from a finished temp file, so a contender's
    read after losing the race always sees complete JSON."""
    vault = tmp_path / "vault"
    (vault / "runs").mkdir(parents=True)
    lock = RunLock(vault, "run-1")
    lock.acquire()
    payload = json.loads((vault / "runs" / ".lock").read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-1" and payload["pid"] == os.getpid()
    assert {"pid", "hostname", "run_id", "started_at", "heartbeat_at"} <= payload.keys()
    lock.release()


# --- boundary hardening ------------------------------------------------------


@pytest.mark.parametrize("bad", ["abc\n", "abc\r", "abc\n\n", "ok-id\n"])
def test_validate_id_rejects_trailing_whitespace_and_newlines(bad: str) -> None:
    """Python's `$` also matches before a trailing newline, so `re.match` accepted
    `"abc\\n"` — while the pydantic field built from the same pattern rejected it. Ids
    from here reach the run lock, on-disk filenames, and the canary token body."""
    with pytest.raises(VaultBoundaryError):
        validate_id(bad, kind="matter_id")


def test_safe_vault_path_rejects_a_nul_byte_as_a_boundary_error(tmp_path: Path) -> None:
    """`realpath` raises ValueError on a NUL, which the web tier would surface as a 500
    instead of the typed 400 it maps `VaultBoundaryError` to."""
    with pytest.raises(VaultBoundaryError):
        safe_vault_path(tmp_path, "runs", "a\0b")


@pytest.mark.parametrize(
    "dirname",
    [
        "OneDrive",  # named in the non-negotiable rule, previously undetected
        "OneDrive - Riehl Law",  # the business-tenant shape
        "GoogleDrive-attorney@example.com",  # modern macOS ~/Library/CloudStorage mount
        "dropbox",  # casing is not a bypass
    ],
)
def test_detect_sync_folder_covers_onedrive_and_scoped_mounts(
    tmp_path: Path, dirname: str
) -> None:
    vault = tmp_path / dirname / "matters" / "m1"
    vault.mkdir(parents=True)
    assert detect_sync_folder(vault) is not None


def test_create_vault_refuses_a_location_inside_a_git_repo(tmp_path: Path) -> None:
    """The preflight used to live only in `init_vault`, so every other creation path
    (notably `MatterRegistry.create`) skipped the non-negotiable repo-boundary rule."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    matter = make_matter("m1")
    with pytest.raises(VaultBoundaryError, match="repo"):
        create_vault(tmp_path / "vault", matter, registry_path=tmp_path / "c.json")


def test_create_vault_refuses_a_sync_folder(tmp_path: Path) -> None:
    dest = tmp_path / "OneDrive" / "matters" / "m1"
    dest.parent.mkdir(parents=True)
    matter = make_matter("m1")
    with pytest.raises(VaultBoundaryError, match="sync"):
        create_vault(dest, matter, registry_path=tmp_path / "c.json")
    # The documented override still works.
    create_vault(
        dest, matter, registry_path=tmp_path / "c.json", allow_sync_folder=True
    )
    assert (dest / MATTER_YAML).is_file()
