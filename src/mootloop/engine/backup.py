"""Driver-coordinated consistent snapshot, encrypted at rest (plan FD-6 hosted-backup gate).

`backup_matter` writes a timestamped snapshot of a matter vault to a destination that is
proven safe first: it refuses any destination inside a background-sync folder or a git repo
(the same containment discipline that keeps active vaults out of those places). Consistency
comes from briefly holding the per-matter `RunLock` — so the snapshot never races an active
run's writes. The ``staging`` working dir is excluded; everything else is captured.

The on-disk artifact is **encrypted** (``<matter>-<ts>.tar.gz.enc``): the tar.gz is produced
to a same-dir temp file, encrypted with AES-256-GCM (random 12-byte nonce prepended to the
ciphertext), and the temp plaintext is shredded in a ``finally`` so it never lingers. The
readback gate now decrypts-and-verifies the archive lists the expected members before the
path is returned. `restore_matter` decrypts (fail-closed on wrong key / truncation / a
tampered GCM tag) and extracts with tar member-path hardening into a staging dir it only
promotes on success — an attacker-authored archive can never escape the target or leave a
partial extract behind.
"""

from __future__ import annotations

import io
import os
import tarfile
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mootloop.engine.queue import Queue
from mootloop.errors import BackupError, LockHeldError
from mootloop.secrets import load_or_create_backup_key
from mootloop.vault import (
    MATTER_YAML,
    RunLock,
    detect_sync_folder,
    enclosing_git_repo,
    load_matter,
    validate_id,
)

_BACKUP_LOCK_RUN_ID = "backup"
_NONCE_LEN = 12
_GCM_TAG_LEN = 16


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
    encrypt: bool = True,
    key: bytes | None = None,
) -> Path:
    """Write a consistent snapshot of ``vault_root`` under ``dest_dir``, encrypted by default.

    Refuses (`BackupError`) if the destination is inside a sync folder or a git repo, or if
    a consistent snapshot point cannot be acquired (a live run holds the lock). With
    ``encrypt=True`` (the hosted default) the artifact is ``<matter>-<ts>.tar.gz.enc``,
    AES-256-GCM sealed with the persisted backup key (or ``key`` if given, for tests); the
    intermediate plaintext tar.gz never survives the call. ``encrypt=False`` keeps the legacy
    plaintext ``.tar.gz`` for the demo/local tier. The ``queue`` arg is accepted for future
    intake-pausing; the RunLock already serializes against active runs, so it is not required.
    """
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
    ts = _compact_ts(now)

    if not encrypt:
        out = dest / f"{matter_id}-{ts}.tar.gz"
        _snapshot_tar(vault_root, out, matter_id)
        _verify_readback_plaintext(out, matter_id)
        return out

    key_bytes = key if key is not None else load_or_create_backup_key()
    out = dest / f"{matter_id}-{ts}.tar.gz.enc"
    fd, tmp_name = tempfile.mkstemp(dir=str(dest), prefix=f".{matter_id}-{ts}-", suffix=".tar.gz")
    os.close(fd)
    tmp_plain = Path(tmp_name)
    try:
        _snapshot_tar(vault_root, tmp_plain, matter_id)
        _encrypt_file(tmp_plain, out, key_bytes)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    finally:
        _shred(tmp_plain)

    _verify_readback_encrypted(out, matter_id, key_bytes)
    return out


def _snapshot_tar(vault_root: Path | str, out: Path, matter_id: str) -> None:
    """Write the lock-consistent tar.gz to ``out`` (staging excluded). Fails closed."""
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


def _encrypt_file(plaintext: Path, out: Path, key: bytes) -> None:
    """AES-256-GCM seal ``plaintext`` into ``out`` (nonce prepended), via a same-dir temp."""
    nonce = os.urandom(_NONCE_LEN)
    sealed = nonce + AESGCM(key).encrypt(nonce, plaintext.read_bytes(), None)
    fd, tmp = tempfile.mkstemp(dir=str(out.parent), prefix=".tmp-enc-", suffix=".enc")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(sealed)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _shred(path: Path) -> None:
    """Best-effort overwrite-then-unlink so a temp plaintext tar leaves no readable bytes."""
    try:
        size = path.stat().st_size
    except OSError:
        return
    try:
        with open(path, "r+b") as handle:
            handle.write(os.urandom(size))
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass
    path.unlink(missing_ok=True)


def _decrypt_archive(archive: Path, key: bytes) -> bytes:
    """Return the decrypted tar.gz bytes, or raise `BackupError` (fail closed)."""
    blob = archive.read_bytes()
    if len(blob) < _NONCE_LEN + _GCM_TAG_LEN:
        raise BackupError(f"backup archive {archive} is truncated")
    nonce, ciphertext = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except InvalidTag as exc:
        raise BackupError(
            f"backup archive {archive} failed authentication (wrong key or tampered)"
        ) from exc


def _verify_readback_plaintext(archive: Path, matter_id: str) -> None:
    try:
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
    except (tarfile.TarError, OSError) as exc:
        archive.unlink(missing_ok=True)
        raise BackupError(f"backup archive {archive} could not be read back") from exc
    _assert_expected_members(names, matter_id, archive)


def _verify_readback_encrypted(archive: Path, matter_id: str, key: bytes) -> None:
    """Decrypt the archive and confirm it lists the expected members (fail closed)."""
    try:
        data = _decrypt_archive(archive, key)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            names = tar.getnames()
    except BackupError:
        archive.unlink(missing_ok=True)
        raise
    except (tarfile.TarError, OSError) as exc:
        archive.unlink(missing_ok=True)
        raise BackupError(f"backup archive {archive} could not be read back") from exc
    _assert_expected_members(names, matter_id, archive)


def _assert_expected_members(names: list[str], matter_id: str, archive: Path) -> None:
    if f"{matter_id}/{MATTER_YAML}" not in names or len(names) < 1:
        archive.unlink(missing_ok=True)
        raise BackupError(f"backup archive {archive} is missing expected members")


# --- restore ----------------------------------------------------------------


def restore_matter(
    archive: Path | str,
    dest_matters_root: Path | str,
    *,
    now: str,
    key: bytes | None = None,
    overwrite: bool = False,
) -> Path:
    """Decrypt and safely extract ``archive`` into ``dest_matters_root/<matter_id>/``.

    Fails closed on a wrong key, truncation, or a tampered GCM tag. Every tar member is
    validated for path containment (absolute paths, ``..``, and symlink/hardlink escapes are
    rejected — tar members are never trusted) before anything is written; extraction lands in
    a staging dir that is only promoted into place on success, so a bad archive never leaves a
    partial extract. Refuses to overwrite an existing non-empty vault unless ``overwrite`` is
    set. Returns the restored vault path. ``now`` is accepted for call-site symmetry/auditing.
    """
    src = Path(archive)
    if not src.is_file():
        raise BackupError(f"backup archive {src} does not exist")

    if src.name.endswith(".enc"):
        key_bytes = key if key is not None else load_or_create_backup_key()
        tar_bytes: bytes | None = _decrypt_archive(src, key_bytes)
    else:
        tar_bytes = None

    root = Path(dest_matters_root)
    root.mkdir(parents=True, exist_ok=True)

    with _open_archive(src, tar_bytes) as tar:
        members = tar.getmembers()
        matter_id = _archive_matter_id(members, src)
        target = root / matter_id
        if target.exists() and any(target.iterdir()) and not overwrite:
            raise BackupError(
                f"refusing to restore over non-empty vault {target}; pass overwrite=True"
            )
        staging = Path(tempfile.mkdtemp(dir=str(root), prefix=f".restore-{matter_id}-"))
        try:
            _safe_extract(tar, members, staging)
            extracted = staging / matter_id
            _assert_restored_vault(extracted, matter_id, src)
            if target.exists():
                _rmtree(target)
            os.replace(extracted, target)
        finally:
            _rmtree(staging)

    return target


def _open_archive(src: Path, tar_bytes: bytes | None) -> tarfile.TarFile:
    """Open the (decrypted) tar.gz for reading; used as a context manager by the caller."""
    try:
        if tar_bytes is not None:
            return tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz")  # noqa: SIM115
        return tarfile.open(src, "r:gz")  # noqa: SIM115
    except (tarfile.TarError, OSError) as exc:
        raise BackupError(f"backup archive {src} could not be opened") from exc


def _archive_matter_id(members: list[tarfile.TarInfo], archive: Path) -> str:
    tops = {m.name.split("/", 1)[0] for m in members if m.name and m.name not in {".", "/"}}
    if len(tops) != 1:
        raise BackupError(
            f"backup archive {archive} must contain exactly one top-level matter dir, "
            f"found {sorted(tops)}"
        )
    matter_id = tops.pop()
    try:
        return validate_id(matter_id, kind="matter_id")
    except Exception as exc:  # noqa: BLE001 — normalize any id-shape failure to BackupError
        raise BackupError(f"backup archive {archive} has an invalid matter id") from exc


def _safe_extract(tar: tarfile.TarFile, members: list[tarfile.TarInfo], dest: Path) -> None:
    """Extract every member with realpath containment; reject anything that could escape."""
    dest_real = Path(os.path.realpath(dest))
    for member in members:
        if member.issym() or member.islnk():
            raise BackupError(f"refusing archive member {member.name!r}: links are not allowed")
        if not (member.isfile() or member.isdir()):
            raise BackupError(f"refusing archive member {member.name!r}: unsupported type")
        resolved = Path(os.path.realpath(dest_real / member.name))
        if resolved != dest_real and dest_real not in resolved.parents:
            raise BackupError(f"refusing archive member {member.name!r}: escapes restore root")
    # ``filter='data'`` is a second, stdlib-maintained guard on top of the checks above.
    tar.extractall(str(dest_real), members=members, filter="data")


def _assert_restored_vault(extracted: Path, matter_id: str, archive: Path) -> None:
    if not extracted.is_dir():
        raise BackupError(f"backup archive {archive} did not restore a {matter_id} vault")
    if not (extracted / MATTER_YAML).is_file():
        raise BackupError(f"restored vault {extracted} is missing {MATTER_YAML}")


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
