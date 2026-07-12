"""Driver-coordinated consistent snapshot (plan FD-6 hosted-backup gate).

`backup_matter` writes a timestamped ``tar.gz`` of a matter vault to a destination that
is proven safe first: it refuses any destination inside a background-sync folder or a
git repo (the same containment discipline that keeps active vaults out of those places).
Consistency comes from briefly holding the per-matter `RunLock` — so the snapshot never
races an active run's writes — and the archive is read back to confirm it lists the
expected members before the path is returned. The ``staging`` working dir is excluded;
everything else is captured.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from mootloop.engine.queue import Queue
from mootloop.errors import BackupError, LockHeldError
from mootloop.vault import RunLock, detect_sync_folder, enclosing_git_repo, load_matter

_BACKUP_LOCK_RUN_ID = "backup"


def _compact_ts(now: str) -> str:
    return "".join(ch for ch in now if ch.isdigit())


def _exclude_staging(matter_id: str) -> object:
    top_staging = f"{matter_id}/staging"

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if info.name == top_staging or info.name.startswith(top_staging + "/"):
            return None
        return info

    return _filter


def backup_matter(
    vault_root: Path | str,
    dest_dir: Path | str,
    now: str,
    *,
    queue: Queue | None = None,
) -> Path:
    """Write a consistent ``tar.gz`` snapshot of ``vault_root`` under ``dest_dir``.

    Refuses (`BackupError`) if the destination is inside a sync folder or a git repo, or
    if a consistent snapshot point cannot be acquired (a live run holds the lock). The
    ``queue`` arg is accepted for future intake-pausing; the RunLock already serializes
    against active runs, so it is not required."""
    dest = Path(dest_dir)
    marker = detect_sync_folder(dest)
    if marker:
        raise BackupError(f"backup destination is inside a sync folder ({marker}); refusing")
    repo = enclosing_git_repo(dest)
    if repo is not None:
        raise BackupError(f"backup destination is inside a git repo ({repo}); refusing")

    matter = load_matter(vault_root)
    matter_id = matter.matter_id
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{matter_id}-{_compact_ts(now)}.tar.gz"

    try:
        with RunLock(vault_root, _BACKUP_LOCK_RUN_ID), tarfile.open(out, "w:gz") as tar:
            tar.add(
                str(vault_root),
                arcname=matter_id,
                filter=_exclude_staging(matter_id),  # type: ignore[arg-type]
            )
    except LockHeldError as exc:
        out.unlink(missing_ok=True)
        raise BackupError(f"cannot snapshot {matter_id!r}: a live run holds the lock") from exc

    _verify_readback(out, matter_id)
    return out


def _verify_readback(archive: Path, matter_id: str) -> None:
    """Open the archive and confirm it lists the expected members (fail closed)."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
    except (tarfile.TarError, OSError) as exc:
        archive.unlink(missing_ok=True)
        raise BackupError(f"backup archive {archive} could not be read back") from exc
    if f"{matter_id}/matter.yaml" not in names or len(names) < 1:
        archive.unlink(missing_ok=True)
        raise BackupError(f"backup archive {archive} is missing expected members")
