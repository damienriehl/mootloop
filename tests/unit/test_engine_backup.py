"""Backup + restore drill for the FD-6 hosted-backup gate.

Proves the encrypted snapshot round-trips (backup -> restore byte-matches the source, minus
``staging``) and fails closed on a wrong key, a single-byte ciphertext tamper, and malicious
tar members (``..`` traversal, absolute path, symlink escape). Every test mints an ephemeral
key and passes it explicitly, so no key material ever touches the repo or the secrets file.
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mootloop.engine.backup import _NONCE_LEN, backup_matter, restore_matter
from mootloop.errors import BackupError
from mootloop.vault import init_vault

NOW = "2026-07-12T00:00:00+00:00"
MATTER_ID = "acme-v-widgets"


def _key() -> bytes:
    return os.urandom(32)


def _make_vault(tmp_path: Path) -> Path:
    from tests.conftest import make_matter

    vault = tmp_path / "vault"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
    (vault / "corpus" / "originals" / "complaint.txt").write_text("the pleading", encoding="utf-8")
    (vault / "facts" / "facts.jsonl").write_text('{"id": "f1"}\n', encoding="utf-8")
    (vault / "staging").mkdir(exist_ok=True)
    (vault / "staging" / "scratch.txt").write_text("working file", encoding="utf-8")
    return vault


def _tree(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = path.read_bytes()
    return out


def _seal(members: list[tuple[tarfile.TarInfo, bytes | None]], key: bytes, out: Path) -> Path:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    nonce = os.urandom(_NONCE_LEN)
    out.write_bytes(nonce + AESGCM(key).encrypt(nonce, buf.getvalue(), None))
    return out


def _file_member(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


# --- encrypted backup -------------------------------------------------------


def test_backup_is_encrypted_by_default(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    out = backup_matter(vault, tmp_path / "backups", NOW, key=_key())
    assert out.name.endswith(".tar.gz.enc")
    # Ciphertext must not be a readable tar, and must not contain plaintext content.
    with pytest.raises(tarfile.TarError):
        tarfile.open(out, "r:gz")  # noqa: SIM115
    assert b"the pleading" not in out.read_bytes()


def test_backup_leaves_no_plaintext_tar_behind(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    dest = tmp_path / "backups"
    backup_matter(vault, dest, NOW, key=_key())
    assert [p.name for p in dest.iterdir()] == [f"{MATTER_ID}-{_ts()}.tar.gz.enc"]


def test_backup_plaintext_opt_in_still_works(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    out = backup_matter(vault, tmp_path / "backups", NOW, encrypt=False)
    assert out.suffix == ".gz"
    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("/matter.yaml") for n in names)
    assert not any("/staging/" in n for n in names)


def test_backup_refuses_destination_inside_git_repo(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(BackupError):
        backup_matter(vault, tmp_path / "repo" / "backups", NOW, key=_key())


def test_backup_refuses_destination_inside_sync_folder(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    (tmp_path / "Dropbox" / ".dropbox").mkdir(parents=True)
    with pytest.raises(BackupError):
        backup_matter(vault, tmp_path / "Dropbox" / "backups", NOW, key=_key())


# --- restore drill (the load-bearing round-trip) ----------------------------


def test_restore_drill_round_trips_byte_for_byte(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    key = _key()
    archive = backup_matter(vault, tmp_path / "backups", NOW, key=key)

    restored_root = tmp_path / "restored"
    out = restore_matter(archive, restored_root, now=NOW, key=key)

    assert out == restored_root / MATTER_ID
    # ``runs/.lock`` is transient snapshot state (held during ``tar.add``, released after),
    # so the source no longer has it — compare everything else byte-for-byte.
    def _content(root: Path) -> dict[str, bytes]:
        return {
            k: v
            for k, v in _tree(root).items()
            if not k.startswith("staging/") and k != "runs/.lock"
        }

    assert _content(out) == _content(vault)
    assert "staging/scratch.txt" not in _tree(out)  # staging never restored


def test_restore_refuses_to_overwrite_non_empty_vault(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    key = _key()
    archive = backup_matter(vault, tmp_path / "backups", NOW, key=key)
    restored_root = tmp_path / "restored"
    restore_matter(archive, restored_root, now=NOW, key=key)

    with pytest.raises(BackupError):
        restore_matter(archive, restored_root, now=NOW, key=key)
    # overwrite=True succeeds.
    restore_matter(archive, restored_root, now=NOW, key=key, overwrite=True)


# --- fail-closed: crypto ----------------------------------------------------


def test_restore_wrong_key_fails_closed(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    archive = backup_matter(vault, tmp_path / "backups", NOW, key=_key())
    with pytest.raises(BackupError):
        restore_matter(archive, tmp_path / "restored", now=NOW, key=_key())
    assert not (tmp_path / "restored" / MATTER_ID).exists()


def test_restore_single_byte_tamper_fails_closed(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    key = _key()
    archive = backup_matter(vault, tmp_path / "backups", NOW, key=key)
    blob = bytearray(archive.read_bytes())
    blob[-1] ^= 0x01  # flip one ciphertext byte -> GCM tag rejects
    archive.write_bytes(blob)
    with pytest.raises(BackupError):
        restore_matter(archive, tmp_path / "restored", now=NOW, key=key)
    assert not (tmp_path / "restored" / MATTER_ID).exists()


def test_restore_truncated_archive_fails_closed(tmp_path: Path) -> None:
    archive = tmp_path / "short.tar.gz.enc"
    archive.write_bytes(b"\x00" * 4)  # shorter than nonce + tag
    with pytest.raises(BackupError):
        restore_matter(archive, tmp_path / "restored", now=NOW, key=_key())


# --- fail-closed: malicious tar members -------------------------------------


def test_restore_rejects_path_traversal_member(tmp_path: Path) -> None:
    key = _key()
    archive = _seal(
        [
            _file_member(f"{MATTER_ID}/matter.yaml", b"matter_id: acme\n"),
            _file_member(f"{MATTER_ID}/../../escape.txt", b"pwned"),
        ],
        key,
        tmp_path / "evil.tar.gz.enc",
    )
    with pytest.raises(BackupError):
        restore_matter(archive, tmp_path / "restored", now=NOW, key=key)
    assert not (tmp_path / "escape.txt").exists()


def test_restore_rejects_absolute_path_member(tmp_path: Path) -> None:
    key = _key()
    archive = _seal(
        [
            _file_member(f"{MATTER_ID}/matter.yaml", b"matter_id: acme\n"),
            _file_member("/tmp/evil-absolute.txt", b"pwned"),
        ],
        key,
        tmp_path / "evil.tar.gz.enc",
    )
    with pytest.raises(BackupError):
        restore_matter(archive, tmp_path / "restored", now=NOW, key=key)


def test_restore_rejects_symlink_escape_member(tmp_path: Path) -> None:
    key = _key()
    link = tarfile.TarInfo(f"{MATTER_ID}/evil-link")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../../../../../etc/passwd"
    archive = _seal(
        [
            _file_member(f"{MATTER_ID}/matter.yaml", b"matter_id: acme\n"),
            (link, None),
        ],
        key,
        tmp_path / "evil.tar.gz.enc",
    )
    with pytest.raises(BackupError):
        restore_matter(archive, tmp_path / "restored", now=NOW, key=key)


def _ts() -> str:
    return "".join(ch for ch in NOW if ch.isdigit())
