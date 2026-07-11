"""Matter-vault module: path hardening, sync-folder detection, matter load/create,
and the per-matter run lock.

Every write into a vault goes through `safe_vault_path` — the single
realpath-containment choke-point. `assert_vault_outside_repo` keeps matter data
structurally out of the repo tree.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import socket
import tempfile
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
)
_SYNC_FILE_MARKERS: tuple[str, ...] = (
    ".dropbox",
    ".dropbox.cache",
    ".tmp.driveupload",
    ".icloud",
)


# --- ID validation ----------------------------------------------------------


def validate_id(value: str, *, kind: str = "id") -> str:
    """Validate a matter/run id. Rejects ``.``, ``..``, and path separators."""
    if value in {".", ".."} or "/" in value or "\\" in value or os.sep in value:
        raise VaultBoundaryError(f"invalid {kind} {value!r}: path components are not allowed")
    if not MATTER_ID_RE.match(value):
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


def detect_sync_folder(vault_root: Path | str) -> str | None:
    """Walk the vault's ancestors for background-sync markers.

    Returns the first marker found (a directory name or marker filename), or None.
    Walks never follow symlinks — ancestors are resolved lexically off realpath.
    """
    start = _real(vault_root)
    for ancestor in (start, *start.parents):
        if ancestor.name in _SYNC_NAME_MARKERS:
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


def create_vault(
    vault_root: Path | str,
    matter: MatterConfig,
    *,
    registry_path: Path | str | None = None,
) -> Path:
    """Create the canonical vault tree, write ``matter.yaml``, and seed a canary.

    Refuses if the target directory already exists and is non-empty.
    """
    # Lazy import breaks the vault<->privacy cycle (privacy imports vault helpers).
    from mootloop.privacy import seed_canary

    validate_id(matter.matter_id, kind="matter_id")
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


def enclosing_git_repo(path: Path | str) -> Path | None:
    """Return the git work-tree root enclosing ``path`` (or the nearest existing
    ancestor), or None. Used to keep vaults out of any repo."""
    cur = Path(path)
    while not cur.exists() and cur != cur.parent:
        cur = cur.parent
    cur = _real(cur)
    for ancestor in (cur, *cur.parents):
        if (ancestor / ".git").exists():
            return ancestor
    return None


def init_vault(
    vault_path: Path | str,
    matter: MatterConfig,
    *,
    allow_sync_folder: bool = False,
    registry_path: Path | str | None = None,
) -> Path:
    """Preflight (repo boundary + sync-folder) then create the vault."""
    repo = enclosing_git_repo(vault_path)
    if repo is not None:
        assert_vault_outside_repo(vault_path, repo)
    marker = detect_sync_folder(vault_path)
    if marker and not allow_sync_folder:
        raise VaultBoundaryError(
            f"vault path is inside a background-sync folder ({marker}); active "
            "vaults must not live in sync folders — pass allow_sync_folder to override"
        )
    return create_vault(vault_path, matter, registry_path=registry_path)


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

    # -- lifecycle --
    def acquire(self) -> RunLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        existing = self._read()
        if existing is not None:
            self._check_takeover(existing)
        self._write(started_at=_now())
        self._acquired = True
        return self

    def heartbeat(self) -> None:
        if not self._acquired:
            raise LockHeldError("cannot heartbeat a lock that is not held")
        current = self._read()
        started = current["started_at"] if current else _now().isoformat()
        self._write(started_at=datetime.fromisoformat(started))

    def release(self) -> None:
        if not self._acquired:
            return
        current = self._read()
        if current and current.get("pid") == self.pid and current.get("hostname") == self.hostname:
            self._path.unlink(missing_ok=True)
        self._acquired = False

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

    def _write(self, *, started_at: datetime) -> None:
        now = _now()
        payload = {
            "pid": self.pid,
            "hostname": self.hostname,
            "run_id": self.run_id,
            "started_at": started_at.isoformat(),
            "heartbeat_at": now.isoformat(),
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

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
