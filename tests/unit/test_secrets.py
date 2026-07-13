"""Secrets loader precedence + redaction (Phase 4 Unit 2 — never log a token)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mootloop.errors import BackupError
from mootloop.secrets import (
    _REGISTERED_SECRETS,
    load_or_create_backup_key,
    load_secret,
    redact,
)


def test_secrets_file_takes_precedence_over_env(tmp_path: Path, monkeypatch) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("COURTLISTENER_TOKEN=fromfile\n", encoding="utf-8")
    monkeypatch.setenv("COURTLISTENER_TOKEN", "fromenv")
    assert load_secret("COURTLISTENER_TOKEN", secrets_file=secrets) == "fromfile"


def test_falls_back_to_env_when_file_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COURTLISTENER_TOKEN", "fromenv")
    assert load_secret("COURTLISTENER_TOKEN", secrets_file=tmp_path / "nope.env") == "fromenv"


def test_missing_secret_is_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("COURTLISTENER_TOKEN", raising=False)
    assert load_secret("COURTLISTENER_TOKEN", secrets_file=tmp_path / "nope.env") is None


def test_secrets_file_ignores_comments_and_quotes(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("# a comment\nKEY = 'quoted-value'\n", encoding="utf-8")
    assert load_secret("KEY", secrets_file=secrets) == "quoted-value"


def test_redact_scrubs_token_header_and_hex() -> None:
    token = "a" * 40
    text = f"Authorization: Token {token} and sk-abcdef012345"
    scrubbed = redact(text)
    assert token not in scrubbed
    assert "sk-abcdef012345" not in scrubbed
    assert "REDACTED" in scrubbed


def test_redact_scrubs_explicit_extra_value() -> None:
    assert "supersecret" not in redact("value=supersecret", extra=("supersecret",))


def test_backup_key_mints_persists_and_reuses(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MOOTLOOP_BACKUP_KEY", raising=False)
    secrets = tmp_path / "secrets.env"
    first = load_or_create_backup_key(secrets_file=secrets)
    assert isinstance(first, bytes) and len(first) == 32  # AES-256
    assert secrets.exists() and oct(secrets.stat().st_mode)[-3:] == "600"
    # A second call reads the persisted key back (stable across restarts).
    assert load_or_create_backup_key(secrets_file=secrets) == first
    # The stored base64 form is registered for verbatim redaction.
    stored = load_secret("MOOTLOOP_BACKUP_KEY", secrets_file=secrets)
    assert stored is not None and stored in _REGISTERED_SECRETS


def test_backup_key_rejects_wrong_length(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MOOTLOOP_BACKUP_KEY", raising=False)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("MOOTLOOP_BACKUP_KEY=dG9vLXNob3J0\n", encoding="utf-8")  # 9 bytes
    with pytest.raises(BackupError):
        load_or_create_backup_key(secrets_file=secrets)
