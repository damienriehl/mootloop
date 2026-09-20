"""Synthetic regression scenarios for creation, restore, and privacy persistence."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mootloop import privacy, secrets
from mootloop.engine import backup
from mootloop.errors import BackupError, OutboundPrivacyError, VaultBoundaryError
from mootloop.registry import MatterRegistry
from mootloop.vault import create_vault
from tests.conftest import make_matter

NOW = "2026-09-19T12:00:00+00:00"
MATTER_ID = "synthetic-restored-matter"


@pytest.fixture
def archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(privacy.CANARY_REGISTRY_ENV, str(tmp_path / "destination-canaries.json"))
    source = create_vault(
        tmp_path / "source",
        make_matter(MATTER_ID),
        registry_path=tmp_path / "source-canaries.json",
    )
    return backup.backup_matter(source, tmp_path / "backups", NOW, encrypt=False)


@pytest.mark.parametrize("kind", ["repo", "sync"])
def test_restore_preflight_precedes_extraction(
    tmp_path: Path, archive: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    parent = tmp_path / ("repo" if kind == "repo" else "Dropbox")
    parent.mkdir()
    if kind == "repo":
        (parent / ".git").mkdir()
        (parent / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    extracted = False

    def extract(*args: object) -> None:
        nonlocal extracted
        extracted = True
        raise AssertionError("extraction entered forbidden location")

    monkeypatch.setattr(backup, "_safe_extract", extract)
    with pytest.raises((BackupError, VaultBoundaryError)):
        backup.restore_matter(archive, parent / "matters", now=NOW)
    assert not extracted
    assert not (parent / "matters").exists()


def test_restore_cannot_delete_target_created_during_extraction(
    tmp_path: Path, archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "restored"
    target = destination / MATTER_ID
    original = backup._safe_extract

    def extract(*args: object) -> None:
        original(*args)
        target.mkdir()
        (target / "sentinel").write_text("concurrent creator")

    monkeypatch.setattr(backup, "_safe_extract", extract)
    with pytest.raises(BackupError):
        backup.restore_matter(archive, destination, now=NOW)
    assert (target / "sentinel").read_text() == "concurrent creator"


def test_restore_and_create_share_destination_reservation(
    tmp_path: Path, archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "restored"
    target = destination / MATTER_ID
    original = backup._safe_extract
    attempted = threading.Event()
    completed = threading.Event()
    crossed_reservation: list[bool] = []
    errors: list[Exception] = []

    def competing_create() -> None:
        attempted.set()
        try:
            create_vault(target, make_matter(MATTER_ID))
        except Exception as exc:
            errors.append(exc)
        finally:
            completed.set()

    competitor = threading.Thread(target=competing_create)

    def extract(*args: object) -> None:
        competitor.start()
        assert attempted.wait(2)
        crossed_reservation.append(completed.wait(0.15))
        original(*args)

    monkeypatch.setattr(backup, "_safe_extract", extract)
    try:
        backup.restore_matter(archive, destination, now=NOW)
    finally:
        competitor.join(3)
    assert not competitor.is_alive()
    assert crossed_reservation == [False]
    assert len(errors) == 1 and isinstance(errors[0], VaultBoundaryError)


def test_restore_registers_original_canary_before_return(tmp_path: Path, archive: Path) -> None:
    out = backup.restore_matter(archive, tmp_path / "restored", now=NOW)
    token = (out / ".canary").read_text().strip()
    assert privacy.load_registry()["canaries"][token] == MATTER_ID
    with pytest.raises(OutboundPrivacyError, match="canary"):
        privacy.scrub_outbound(token, secrets_file=tmp_path / "absent-secrets")


def test_restore_policy_failure_preserves_existing_target(tmp_path: Path, archive: Path) -> None:
    target = tmp_path / "restored" / MATTER_ID
    target.mkdir(parents=True)
    (target / "sentinel").write_text("existing vault")
    (tmp_path / "destination-canaries.json").write_text('{"canaries":[]}')
    with pytest.raises(OutboundPrivacyError):
        backup.restore_matter(archive, target.parent, now=NOW, overwrite=True)
    assert (target / "sentinel").read_text() == "existing vault"


@pytest.mark.parametrize("token", ["broken", "MOOTLOOP-CANARY-another-" + "a" * 32])
def test_restore_rejects_invalid_existing_canary(
    tmp_path: Path, archive: Path, monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    original = backup._safe_extract

    def extract(*args: object) -> None:
        original(*args)
        (args[2] / MATTER_ID / ".canary").write_text(token + "\n")

    monkeypatch.setattr(backup, "_safe_extract", extract)
    with pytest.raises((BackupError, OutboundPrivacyError)):
        backup.restore_matter(archive, tmp_path / "restored", now=NOW)
    assert not (tmp_path / "restored" / MATTER_ID).exists()


def test_restore_seeds_missing_legacy_canary(
    tmp_path: Path, archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = backup._safe_extract

    def extract(*args: object) -> None:
        original(*args)
        (args[2] / MATTER_ID / ".canary").unlink()

    monkeypatch.setattr(backup, "_safe_extract", extract)
    out = backup.restore_matter(archive, tmp_path / "restored", now=NOW)
    token = (out / ".canary").read_text().strip()
    assert privacy.load_registry()["canaries"][token] == MATTER_ID


def test_restore_rejects_config_directory_identity_mismatch(
    tmp_path: Path, archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = backup._safe_extract

    def extract(*args: object) -> None:
        original(*args)
        config = args[2] / MATTER_ID / "matter.yaml"
        config.write_text(config.read_text().replace(MATTER_ID, "different-matter"))

    monkeypatch.setattr(backup, "_safe_extract", extract)
    with pytest.raises(BackupError, match="identity"):
        backup.restore_matter(archive, tmp_path / "restored", now=NOW)
    assert not (tmp_path / "restored" / MATTER_ID).exists()


def test_registry_rejects_config_directory_identity_mismatch(tmp_path: Path) -> None:
    create_vault(tmp_path / "alpha", make_matter("bravo"), registry_path=tmp_path / "c.json")
    registry = MatterRegistry(tmp_path)
    with pytest.raises(VaultBoundaryError, match="identity"):
        registry.resolve("alpha")
    with pytest.raises(VaultBoundaryError, match="identity"):
        registry.list_matters()


def test_failed_canary_registration_does_not_publish_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save = privacy._save_registry

    def fail(*args: object) -> None:
        raise OSError("synthetic registry failure")

    monkeypatch.setattr(privacy, "_save_registry", fail)
    target = tmp_path / "matter"
    with pytest.raises(OSError, match="registry failure"):
        create_vault(target, make_matter(), registry_path=tmp_path / "c.json")
    assert not (target / "matter.yaml").exists()
    monkeypatch.setattr(privacy, "_save_registry", save)
    create_vault(target, make_matter(), registry_path=tmp_path / "c.json")


def test_concurrent_canary_registration_keeps_both_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_saving = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    save = privacy._save_registry
    registry = tmp_path / "c.json"

    def paused_save(data: dict[str, object], path: Path) -> None:
        if threading.current_thread().name.endswith("_0"):
            first_saving.set()
            assert release_first.wait(3)
        save(data, path)

    def seed(name: str) -> str:
        target = tmp_path / name
        target.mkdir()
        if name == "second":
            second_started.set()
        return privacy.seed_canary(target, name, registry_path=registry)

    monkeypatch.setattr(privacy, "_save_registry", paused_save)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(seed, "first")
        assert first_saving.wait(2)
        second = pool.submit(seed, "second")
        assert second_started.wait(2)
        try:
            with pytest.raises(TimeoutError):
                second.result(timeout=0.15)
        finally:
            release_first.set()
        tokens = [first.result(timeout=3), second.result(timeout=3)]
    assert set(privacy.load_registry(registry)["canaries"]) == set(tokens)


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {"canaries": [], "denylist": {}},
        {"canaries": {"t": 1}, "denylist": []},
        {"canaries": {}, "denylist": [1]},
        {"canaries": {}, "denylist": "secret"},
    ],
)
def test_local_registry_rejects_malformed_policy(tmp_path: Path, data: object) -> None:
    registry = tmp_path / "c.json"
    registry.write_text(json.dumps(data))
    with pytest.raises(OutboundPrivacyError):
        privacy.load_registry(registry)


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_local_registry_rejects_nonregular_path(tmp_path: Path, kind: str) -> None:
    registry = tmp_path / "canaries.json"
    if kind == "directory":
        registry.mkdir()
    else:
        target = tmp_path / "valid-canaries.json"
        target.write_text(json.dumps({"canaries": {}, "denylist": []}))
        registry.symlink_to(target)

    with pytest.raises(OutboundPrivacyError, match="must be a regular file"):
        privacy.load_registry(registry)


def test_canary_registration_preserves_conflicting_existing_binding(tmp_path: Path) -> None:
    registry = tmp_path / "canaries.json"
    token = f"{privacy.CANARY_PREFIX}{MATTER_ID}-{'a' * 32}"
    original = {
        "canaries": {token: "different-synthetic-matter"},
        "denylist": ["synthetic-protected-name"],
    }
    registry.write_text(json.dumps(original))
    before = registry.read_bytes()

    with pytest.raises(OutboundPrivacyError, match="conflicting matter identity"):
        privacy.register_canary(token, MATTER_ID, registry_path=registry)

    assert registry.read_bytes() == before
    assert privacy.load_registry(registry) == original


@pytest.mark.parametrize("kind", ["backup", "signing"])
def test_concurrent_first_use_returns_one_durable_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    key = secrets.BACKUP_KEY if kind == "backup" else secrets.DOWNLOAD_SIGNING_KEY
    monkeypatch.delenv(key, raising=False)
    path = tmp_path / "synthetic-secrets.env"
    load = secrets.load_secret
    barrier = threading.Barrier(2)
    local = threading.local()

    def synchronized_load(key: str, *, secrets_file: Path) -> str | None:
        value = load(key, secrets_file=secrets_file)
        if not getattr(local, "seen", False):
            local.seen = True
            barrier.wait(timeout=3)
        return value

    monkeypatch.setattr(secrets, "load_secret", synchronized_load)
    loader = (
        secrets.load_or_create_backup_key
        if kind == "backup"
        else secrets.load_or_create_signing_key
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(loader, secrets_file=path) for _ in range(2)]
        values = [future.result(timeout=4) for future in futures]
    monkeypatch.setattr(secrets, "load_secret", load)
    assert values[0] == values[1] == loader(secrets_file=path)
    assert len([line for line in path.read_text().splitlines() if line.startswith(key + "=")]) == 1


def test_existing_key_never_requires_writable_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "synthetic-secrets.env"
    path.write_text(f"{secrets.DOWNLOAD_SIGNING_KEY}=preseeded-test-value\n")

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("preseeded key must not need mkdir")

    monkeypatch.setattr(Path, "mkdir", fail)
    assert secrets.load_or_create_signing_key(secrets_file=path) == "preseeded-test-value"


def test_existing_duplicate_key_keeps_historical_last_value(tmp_path: Path) -> None:
    path = tmp_path / "synthetic-secrets.env"
    contents = (
        f"{secrets.DOWNLOAD_SIGNING_KEY}=historical-first-value\n"
        f"{secrets.DOWNLOAD_SIGNING_KEY}=historical-winning-value\n"
    )
    path.write_text(contents)
    assert secrets.load_or_create_signing_key(secrets_file=path) == "historical-winning-value"
    assert path.read_text() == contents


def test_key_fastpath_waits_for_inflight_durable_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(secrets.DOWNLOAD_SIGNING_KEY, raising=False)
    path = tmp_path / "synthetic-secrets.env"
    before_sync = threading.Event()
    release_sync = threading.Event()
    sync = secrets.fsync_file_and_parent

    def paused_sync(path: Path) -> None:
        before_sync.set()
        assert release_sync.wait(3)
        sync(path)

    monkeypatch.setattr(secrets, "fsync_file_and_parent", paused_sync)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(secrets.load_or_create_signing_key, secrets_file=path)
        assert before_sync.wait(2)
        second = pool.submit(secrets.load_or_create_signing_key, secrets_file=path)
        try:
            with pytest.raises(TimeoutError):
                second.result(timeout=0.15)
        finally:
            release_sync.set()
        assert first.result(timeout=3) == second.result(timeout=3)


def test_key_retry_requires_successful_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(secrets.DOWNLOAD_SIGNING_KEY, raising=False)
    path = tmp_path / "synthetic-secrets.env"

    def fail(path: Path) -> None:
        raise OSError("key durability failed")

    monkeypatch.setattr(secrets, "fsync_file_and_parent", fail)
    for _ in range(2):
        with pytest.raises(OSError, match="key durability"):
            secrets.load_or_create_signing_key(secrets_file=path)
