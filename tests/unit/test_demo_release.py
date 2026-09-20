from __future__ import annotations

import hashlib
import io
import json
import tarfile

import pytest

from mootloop.web.catalog import PublicationError
from mootloop.web.release import stage_archive


def archive_and_pin(tmp_path, name, kind=tarfile.REGTYPE):
    archive = tmp_path / "release.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = "/outside-sentinel" if kind == tarfile.SYMTYPE else ""
        info.size = 2 if kind == tarfile.REGTYPE else 0
        tar.addfile(info, io.BytesIO(b"{}") if kind == tarfile.REGTYPE else None)
    pin = tmp_path / "pin.json"
    pin.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "release_id": "r1",
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "url": "https://github.com/damienriehl/mootloop/releases/download/test/release.tar",
            }
        )
    )
    return archive, pin


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", tarfile.REGTYPE),
        ("/outside", tarfile.REGTYPE),
        ("one//snapshot.json", tarfile.REGTYPE),
        ("link", tarfile.SYMTYPE),
        ("device", tarfile.CHRTYPE),
    ],
)
def test_archive_rejects_unsafe_entries_before_publication(tmp_path, name, kind):
    archive, pin = archive_and_pin(tmp_path, name, kind)
    with pytest.raises(PublicationError):
        stage_archive(archive, pin, tmp_path / "public")
    assert not (tmp_path / "public").exists()
    assert not (tmp_path.parent / "escape").exists()


def test_wrong_archive_digest_fails_before_extraction(tmp_path):
    archive, pin = archive_and_pin(tmp_path, "catalog.json")
    archive.write_bytes(archive.read_bytes() + b"changed")
    with pytest.raises(PublicationError, match="digest"):
        stage_archive(archive, pin, tmp_path / "public")
    assert not (tmp_path / "public").exists()


def test_duplicate_archive_member_cannot_overwrite_an_earlier_entry(tmp_path):
    archive, pin = archive_and_pin(tmp_path, "catalog.json")
    with tarfile.open(archive, "a") as tar:
        info = tarfile.TarInfo("catalog.json")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"{}"))
    payload = json.loads(pin.read_text())
    payload["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    pin.write_text(json.dumps(payload))
    with pytest.raises(PublicationError, match="duplicate"):
        stage_archive(archive, pin, tmp_path / "public")
    assert not (tmp_path / "public").exists()


def test_pin_rejects_noncanonical_download_url(tmp_path):
    archive, pin = archive_and_pin(tmp_path, "catalog.json")
    payload = json.loads(pin.read_text())
    payload["url"] = "http://untrusted.invalid/release.tar"
    pin.write_text(json.dumps(payload))
    with pytest.raises(PublicationError, match="schema"):
        stage_archive(archive, pin, tmp_path / "public")
