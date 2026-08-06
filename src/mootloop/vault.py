"""Matter-vault module: path hardening, sync-folder detection, matter load/create,
and the per-matter run lock.

Every write into a vault goes through `safe_vault_path` — the single
realpath-containment choke-point. `assert_vault_outside_repo` keeps matter data
structurally out of the repo tree.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import re
import shutil
import socket
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any

import yaml
from pydantic import ValidationError

from mootloop.errors import LockHeldError, MatterConfigError, VaultBoundaryError
from mootloop.models.common import MATTER_ID_PATTERN
from mootloop.models.matter import MatterConfig

logger = logging.getLogger("mootloop.vault")

MATTER_ID_RE = re.compile(MATTER_ID_PATTERN)

MATTER_YAML = "matter.yaml"
CANARY_FILE = ".canary"
LOCK_FILE = ".lock"

# Canonical vault tree, created by `create_vault`.
VAULT_TREE: tuple[str, ...] = (
    "corpus/originals",
    "corpus/normalized",
    "facts",
    "requests",
    "law",
    "runs",
    "deliverables",
    "learnings",
    "research-requests",
)

DEFAULT_HEARTBEAT_THRESHOLD = timedelta(minutes=15)

# Sync-folder markers. Ancestor directory *names* that flag a sync root, plus
# marker files/dirs that a sync client drops at its root.
_SYNC_NAME_MARKERS: tuple[str, ...] = (
    "Dropbox",
    "Google Drive",
    "GoogleDrive",
    "Mobile Documents",  # iCloud Drive on macOS
    "OneDrive",  # named in the non-negotiable rule but previously undetected
)
_SYNC_FILE_MARKERS: tuple[str, ...] = (
    ".dropbox",
    ".dropbox.cache",
    ".tmp.driveupload",
    ".icloud",
)


# --- ID validation ----------------------------------------------------------


def validate_id(value: str, *, kind: str = "id") -> str:
    """Validate a matter/run id. Rejects ``.``, ``..``, and path separators.

    ``fullmatch``, not ``match``: Python's ``$`` also matches immediately before a
    trailing newline, so ``match`` accepted ``"abc\\n"`` — while the pydantic
    ``MatterIdStr`` field built from the same pattern (rust regex, true end anchor)
    rejected it. The two validators disagreeing is the bug; ids reach the run lock,
    on-disk filenames, and the canary token body through this function.
    """
    if value in {".", ".."} or "/" in value or "\\" in value or os.sep in value:
        raise VaultBoundaryError(f"invalid {kind} {value!r}: path components are not allowed")
    if not MATTER_ID_RE.fullmatch(value):
        raise VaultBoundaryError(f"invalid {kind} {value!r}: must match {MATTER_ID_PATTERN}")
    return value


# --- Path hardening ---------------------------------------------------------


def _real(path: Path | str) -> Path:
    return Path(os.path.realpath(path))


def _is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def safe_vault_path(vault_root: Path | str, *parts: str) -> Path:
    """Resolve ``vault_root/parts`` and assert it stays inside ``realpath(vault_root)``.

    The single choke-point before any vault write. Absolute parts, ``..``, and
    symlinks that escape the vault all resolve outside the root and are rejected.
    """
    # A NUL byte makes `realpath` raise ValueError, which callers mapping
    # `VaultBoundaryError` to a typed 400 would surface as a 500 instead.
    if any("\0" in part for part in parts):
        raise VaultBoundaryError("path parts must not contain a NUL byte")
    root_real = _real(vault_root)
    candidate = _real(root_real.joinpath(*parts))
    if not _is_within(candidate, root_real):
        raise VaultBoundaryError(f"path {candidate} escapes vault root {root_real}")
    return candidate


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Durably replace ``path`` with ``text`` via a same-dir temp file + ``os.replace``.

    The temp file is fsync'd before the rename so a crash leaves either the old
    file or the complete new one — never a truncated write. Callers resolve ``path``
    through `safe_vault_path` first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_copy(src: Path, dst: Path) -> None:
    """Copy ``src`` onto ``dst`` atomically (same-dir temp + ``os.replace``).

    Content-addressed callers may re-copy identical bytes; the rename keeps that
    idempotent and crash-safe. ``dst`` is expected to be a `safe_vault_path` result.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=".tmp-", suffix=dst.suffix)
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def assert_vault_outside_repo(vault_root: Path | str, repo_root: Path | str) -> None:
    """Assert the vault and the repo tree do not overlap (either direction)."""
    vault_real = _real(vault_root)
    repo_real = _real(repo_root)
    if _is_within(vault_real, repo_real):
        raise VaultBoundaryError(
            f"vault {vault_real} is inside the repo tree {repo_real}: matter data "
            "must never live in the repo"
        )
    if _is_within(repo_real, vault_real):
        raise VaultBoundaryError(f"repo {repo_real} is inside the vault {vault_real}")


# --- Sync-folder detection --------------------------------------------------


def _is_sync_dir_name(name: str) -> bool:
    """True for a sync-root directory name, including its tenant-scoped variants.

    Exact case-sensitive equality missed the shapes these clients actually create:
    ``OneDrive - Riehl Law`` (business tenants) and ``GoogleDrive-user@example.com``
    (the modern macOS mount under ``~/Library/CloudStorage``).
    """
    folded = name.casefold()
    for marker in _SYNC_NAME_MARKERS:
        base = marker.casefold()
        if folded == base or folded.startswith((f"{base} -", f"{base}-", f"{base}_")):
            return True
    return False


def detect_sync_folder(vault_root: Path | str) -> str | None:
    """Walk the vault's ancestors for background-sync markers.

    Returns the first marker found (a directory name or marker filename), or None.
    Walks never follow symlinks — ancestors are resolved lexically off realpath.
    """
    start = _real(vault_root)
    for ancestor in (start, *start.parents):
        if _is_sync_dir_name(ancestor.name):
            return ancestor.name
        for marker in _SYNC_FILE_MARKERS:
            if (ancestor / marker).exists():
                return marker
    return None


# --- Matter load / create ---------------------------------------------------


def load_matter(vault_root: Path | str) -> MatterConfig:
    """Load and validate ``matter.yaml``.

    Re-raises pydantic validation failures as `MatterConfigError`, naming each bad
    field path so the user knows exactly what to fix.
    """
    path = safe_vault_path(vault_root, MATTER_YAML)
    if not path.is_file():
        raise MatterConfigError(f"no {MATTER_YAML} found at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise MatterConfigError(f"{MATTER_YAML} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise MatterConfigError(f"{MATTER_YAML} must be a mapping, got {type(raw).__name__}")
    try:
        return MatterConfig.model_validate(raw)
    except ValidationError as exc:
        raise MatterConfigError(_format_validation_error(exc)) from exc


def _format_validation_error(exc: ValidationError) -> str:
    lines = [f"{MATTER_YAML} has {exc.error_count()} validation error(s):"]
    for issue in _issues_from_validation(exc):
        lines.append(f"  - {issue['loc']}: {issue['msg']}")
    return "\n".join(lines)


def _issues_from_validation(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {"loc": ".".join(str(p) for p in err["loc"]) or "<root>", "msg": err["msg"]}
        for err in exc.errors()
    ]


def matter_validation_issues(vault_root: Path | str) -> list[dict[str, str]]:
    """Return structured validation issues (``[]`` if valid). Never raises for a
    merely-invalid matter; used by ``mootloop validate --json``."""
    try:
        load_matter(vault_root)
    except MatterConfigError as exc:
        cause = exc.__cause__
        if isinstance(cause, ValidationError):
            return _issues_from_validation(cause)
        return [{"loc": "<file>", "msg": str(exc)}]
    return []


def preflight_vault_location(vault_path: Path | str, *, allow_sync_folder: bool = False) -> None:
    """Assert a vault may be created here: outside any repo, outside any sync folder.

    Lives here, and is called by `create_vault`, so EVERY creation path inherits it.
    It used to sit only in `init_vault`, which meant `MatterRegistry.create` — the
    documented single entry point for the hosted tier — happily provisioned a
    privileged vault inside a git work tree or under ``~/OneDrive``.
    """
    repo = enclosing_git_repo(vault_path)
    if repo is not None:
        assert_vault_outside_repo(vault_path, repo)
    marker = detect_sync_folder(vault_path)
    if marker and not allow_sync_folder:
        raise VaultBoundaryError(
            f"vault path is inside a background-sync folder ({marker}); active "
            "vaults must not live in sync folders — pass allow_sync_folder to override"
        )


def create_vault(
    vault_root: Path | str,
    matter: MatterConfig,
    *,
    registry_path: Path | str | None = None,
    allow_sync_folder: bool = False,
) -> Path:
    """Create the canonical vault tree, write ``matter.yaml``, and seed a canary.

    Refuses if the target directory already exists and is non-empty, or if the location
    fails `preflight_vault_location`.
    """
    # Lazy import breaks the vault<->privacy cycle (privacy imports vault helpers).
    from mootloop.privacy import seed_canary

    validate_id(matter.matter_id, kind="matter_id")
    preflight_vault_location(vault_root, allow_sync_folder=allow_sync_folder)
    root = Path(vault_root)
    if root.exists() and any(root.iterdir()):
        raise VaultBoundaryError(f"refusing to create vault: {root} exists and is non-empty")
    root.mkdir(parents=True, exist_ok=True)

    for subdir in VAULT_TREE:
        safe_vault_path(root, *subdir.split("/")).mkdir(parents=True, exist_ok=True)

    matter_path = safe_vault_path(root, MATTER_YAML)
    payload = matter.model_dump(mode="json")
    matter_path.write_text(
        yaml.safe_dump(payload, sort_keys=True, default_flow_style=False),
        encoding="utf-8",
    )

    seed_canary(root, matter.matter_id, registry_path=registry_path)
    return root


def _is_git_marker(dot_git: Path) -> bool:
    """True when ``.git`` really marks a repo, mirroring git's own test.

    A gitlink file (worktree/submodule) counts. A directory counts only when it
    carries ``HEAD`` — an empty or partial ``.git`` directory is not a repo, and
    git itself would not resolve one. Stray ``.git`` directories do appear in
    shared parents like ``/tmp``; treating those as repos would wrongly forbid
    every vault beneath them.
    """
    if dot_git.is_file():
        return True
    return (dot_git / "HEAD").exists()


def enclosing_git_repo(path: Path | str) -> Path | None:
    """Return the git work-tree root enclosing ``path`` (or the nearest existing
    ancestor), or None. Used to keep vaults out of any repo."""
    cur = Path(path)
    while not cur.exists() and cur != cur.parent:
        cur = cur.parent
    cur = _real(cur)
    for ancestor in (cur, *cur.parents):
        if _is_git_marker(ancestor / ".git"):
            return ancestor
    return None


def init_vault(
    vault_path: Path | str,
    matter: MatterConfig,
    *,
    allow_sync_folder: bool = False,
    registry_path: Path | str | None = None,
) -> Path:
    """Preflight (repo boundary + sync-folder) then create the vault.

    The preflight now lives in `create_vault`, so this is a thin alias kept for the CLI
    and the demo baker; both paths get the identical checks."""
    return create_vault(
        vault_path,
        matter,
        registry_path=registry_path,
        allow_sync_folder=allow_sync_folder,
    )


# --- Run lock ---------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    return True


class RunLock:
    """Per-matter run lock at ``runs/.lock``.

    Context manager. On `acquire`, a stale lock (dead PID on this host, or a
    heartbeat older than the threshold) is logged and taken over; a lock held by a
    live process on this host, or any lock from a different host, raises
    `LockHeldError` unless `override=True`.
    """

    def __init__(
        self,
        vault_root: Path | str,
        run_id: str,
        *,
        heartbeat_threshold: timedelta = DEFAULT_HEARTBEAT_THRESHOLD,
        override: bool = False,
    ) -> None:
        validate_id(run_id, kind="run_id")
        self.vault_root = Path(vault_root)
        self.run_id = run_id
        self.heartbeat_threshold = heartbeat_threshold
        self.override = override
        self.hostname = socket.gethostname()
        self.pid = os.getpid()
        self._path = safe_vault_path(vault_root, "runs", LOCK_FILE)
        self._acquired = False
        self._token: str | None = None

    # -- lifecycle --
    def acquire(self) -> RunLock:
        """Take the lock, or raise `LockHeldError`.

        The gate is an atomic exclusive create, NOT a read followed by a write. Two
        processes that both read "no lock" before either wrote would both have believed
        they held it — and this lock is the only thing serializing `record_turn`'s
        load-fold-append cycle, `attest`, decision resolution, and the backup snapshot
        that documents itself as never racing an active run. The `Queue` alongside it
        already takes an `flock`; this did not.

        The file is published by `os.link` from a fully-written temp file, so it is
        never observable half-written — a loser's follow-up read always sees complete
        JSON and can make a real takeover decision.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        started = _now()
        self._token = uuid.uuid4().hex
        with self._serialized_update():
            try:
                self._create_exclusive(started_at=started)
            except FileExistsError:
                existing = self._read()
                if existing is None:
                    # The lock exists but says nothing readable. Fail closed rather than
                    # steal it: an unreadable control is a blocker, not a green light.
                    if not self.override:
                        self._token = None
                        raise LockHeldError(
                            f"lock file {self._path} is unreadable or corrupt; pass "
                            "override=True to take it over"
                        ) from None
                    logger.warning("overriding an unreadable lock at %s", self._path)
                else:
                    try:
                        self._check_takeover(existing)
                    except BaseException:
                        self._token = None
                        raise
                self._write(started_at=started)
        self._acquired = True
        return self

    def heartbeat(self, *, best_effort: bool = False) -> bool:
        """Refresh ``heartbeat_at`` so a held lock does not age into looking stale.

        A long run holds this lock for hours. Nothing else moves ``heartbeat_at``,
        so without periodic refreshes the lock crosses `heartbeat_threshold` and
        `_check_takeover` hands the vault to a second process while the first is
        still writing to it — the exact collision the lock exists to prevent.

        ``best_effort`` is for that run loop: it returns ``False`` instead of
        raising, because a transient failure to touch a lock file (a full disk, a
        blipping mount) must never be what kills an otherwise healthy run mid-turn.
        The next turn tries again; if the condition persists, the run degrades to
        exactly the takeover-eligible state it had before this existed.
        """
        try:
            if not self._acquired:
                raise LockHeldError("cannot heartbeat a lock that is not held")
            with self._serialized_update():
                current = self._read()
                if current is None or current.get("token") != self._token:
                    raise LockHeldError("cannot heartbeat a lock owned by another run")
                started = current["started_at"]
                self._write(started_at=datetime.fromisoformat(started))
        except (LockHeldError, OSError, KeyError, TypeError, ValueError):
            if not best_effort:
                raise
            logger.warning(
                "heartbeat failed for lock %s (run %s); it may age into takeover-eligible",
                self._path,
                self.run_id,
                exc_info=True,
            )
            return False
        return True

    def release(self) -> None:
        if not self._acquired:
            return
        with self._serialized_update():
            current = self._read()
            if current and current.get("token") == self._token:
                self._path.unlink(missing_ok=True)
        self._acquired = False
        self._token = None

    # -- internals --
    def _check_takeover(self, existing: dict[str, Any]) -> None:
        host = existing.get("hostname")
        pid = int(existing.get("pid", -1))
        if host != self.hostname:
            if not self.override:
                raise LockHeldError(
                    f"lock held by run {existing.get('run_id')} on host {host}; "
                    "pass override=True to take over a cross-host lock"
                )
            logger.warning("overriding cross-host lock held by host %s", host)
            return
        if _pid_alive(pid):
            if self._heartbeat_stale(existing):
                logger.warning("taking over stale lock (pid %s, heartbeat expired)", pid)
                return
            raise LockHeldError(
                f"lock held by live process pid {pid} (run {existing.get('run_id')})"
            )
        logger.warning("taking over lock from dead pid %s", pid)

    def _heartbeat_stale(self, existing: dict[str, Any]) -> bool:
        hb = existing.get("heartbeat_at")
        if not hb:
            return True
        try:
            last = datetime.fromisoformat(hb)
        except ValueError:
            return True
        return _now() - last > self.heartbeat_threshold

    def _read(self) -> dict[str, Any] | None:
        if not self._path.is_file():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def _payload(self, started_at: datetime) -> str:
        return json.dumps(
            {
                "pid": self.pid,
                "hostname": self.hostname,
                "run_id": self.run_id,
                "token": self._token,
                "started_at": started_at.isoformat(),
                "heartbeat_at": _now().isoformat(),
            },
            indent=2,
        )

    def _create_exclusive(self, *, started_at: datetime) -> None:
        """Publish a complete lock file, or raise `FileExistsError` if one is there.

        `os.link` is the atomic step: the destination either does not exist (and now
        holds the finished bytes) or it does (and we lost). `O_CREAT|O_EXCL` alone would
        expose a window where the file exists but is still empty, and a loser reading it
        then would see "corrupt" and steal the lock it had just lost.
        """
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".lock-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(self._payload(started_at))
            os.link(tmp, self._path)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)

    def _write(self, *, started_at: datetime) -> None:
        atomic_write_text(self._path, self._payload(started_at))

    @contextlib.contextmanager
    def _serialized_update(self) -> Iterator[None]:
        """Serialize lock-file decisions while fencing each published owner.

        The runs directory is the stable inode shared by all contenders, unlike the
        lock file whose inode changes on every atomic write. Holding its advisory lock
        closes the stale read/check/replace race without creating another vault file.
        """
        fd = os.open(self._path.parent, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    # -- context manager --
    def __enter__(self) -> RunLock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
