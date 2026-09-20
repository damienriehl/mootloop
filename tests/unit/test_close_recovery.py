from pathlib import Path

import pytest
import yaml

from mootloop import close
from mootloop.models.lifecycle import CloseIntent, CloseRecord
from tests.unit.test_close import MID, NOW, _make_matters_root


def test_retry_rechecks_retention_if_vault_was_not_detached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_matters_root(tmp_path)
    real_sync = close.fsync_file_and_parent

    def fail_after_intent(path: Path) -> None:
        real_sync(path)
        if path.name.endswith(".pending.json"):
            raise OSError("interrupted preparation")

    monkeypatch.setattr(close, "fsync_file_and_parent", fail_after_intent)
    with pytest.raises(OSError, match="interrupted preparation"):
        close.close_matter(root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups")
    config = root / MID / "matter.yaml"
    value = yaml.safe_load(config.read_text())
    value["retention"]["litigation_hold"] = True
    config.write_text(yaml.safe_dump(value))
    monkeypatch.setattr(close, "fsync_file_and_parent", real_sync)
    with pytest.raises(close.CloseError, match="litigation hold"):
        close.close_matter(root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups")
    assert config.is_file()


@pytest.mark.parametrize("failure", ["purge", "partial_purge", "record"])
def test_close_retry_recovers_after_destructive_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    root = _make_matters_root(tmp_path)
    real_purge = close._purge_vault
    real_record = close._write_close_record

    def purge(root: Path, matter_id: str) -> Path:
        if failure == "partial_purge":
            (root / matter_id / "matter.yaml").unlink()
            raise OSError("interrupted partial purge")
        real_purge(root, matter_id)
        raise OSError("interrupted after purge")

    def record(root: Path, value: CloseRecord) -> Path:
        raise OSError("interrupted close record")

    monkeypatch.setattr(close, "_purge_vault", real_purge if failure == "record" else purge)
    monkeypatch.setattr(
        close, "_write_close_record", record if failure == "record" else real_record
    )
    with pytest.raises(OSError, match="interrupted"):
        close.close_matter(root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups")
    assert not (root / MID).exists()
    monkeypatch.setattr(close, "_purge_vault", real_purge)
    monkeypatch.setattr(close, "_write_close_record", real_record)
    result = close.close_matter(
        root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups"
    )
    assert result.source_matter_id == MID
    assert result.removed_counts["facts"] == 1
    assert (
        close.close_matter(root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups")
        == result
    )
    tombstones = (root / close.CLOSED_DIRNAME / close.TOMBSTONES_FILE).read_text().splitlines()
    assert len(tombstones) == 1


def test_intent_persistence_failure_leaves_vault_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_matters_root(tmp_path)
    original = close.fsync_file_and_parent

    def fail(path: Path) -> None:
        if path.name.endswith(".pending.json"):
            raise OSError("intent fsync failed")
        original(path)

    monkeypatch.setattr(close, "fsync_file_and_parent", fail)
    with pytest.raises(OSError, match="intent fsync"):
        close.close_matter(root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups")
    assert (root / MID / "facts" / "facts.jsonl").is_file()
    # The visible intent may still be nondurable: retry must not authorize deletion.
    with pytest.raises(OSError, match="intent fsync"):
        close.close_matter(root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups")
    assert (root / MID / "facts" / "facts.jsonl").is_file()


def test_pending_close_refuses_replacement_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_matters_root(tmp_path)

    real_sync = close.fsync_file_and_parent

    def fail(path: Path) -> None:
        real_sync(path)
        if path.name.endswith(".pending.json"):
            raise OSError("interrupted before detach")

    monkeypatch.setattr(close, "fsync_file_and_parent", fail)
    with pytest.raises(OSError):
        close.close_matter(root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups")
    (root / MID).rename(tmp_path / "original")
    (root / MID).mkdir()
    sentinel = root / MID / "preserve.txt"
    sentinel.write_text("replacement")
    monkeypatch.setattr(close, "fsync_file_and_parent", real_sync)
    with pytest.raises(close.CloseError, match="replacement vault"):
        close.close_matter(root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups")
    assert sentinel.read_text() == "replacement"


def test_quarantine_recovery_preserves_new_matter_at_original_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_matters_root(tmp_path)
    real_purge = close._purge_vault

    def fail(root: Path, matter_id: str) -> Path:
        raise OSError("interrupted quarantined purge")

    monkeypatch.setattr(close, "_purge_vault", fail)
    with pytest.raises(OSError, match="quarantined"):
        close.close_matter(root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups")
    assert not (root / MID).exists()
    (root / MID).mkdir()
    sentinel = root / MID / "preserve.txt"
    sentinel.write_text("replacement")
    monkeypatch.setattr(close, "_purge_vault", real_purge)
    close.close_matter(root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups")
    assert sentinel.read_text() == "replacement"


def test_retry_recovers_rename_before_detached_intent_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_matters_root(tmp_path)
    original = close._write_close_intent

    def fail(path: Path, intent: CloseIntent) -> None:
        if intent.detached:
            raise OSError("interrupted after rename")
        original(path, intent)

    monkeypatch.setattr(close, "_write_close_intent", fail)
    with pytest.raises(OSError, match="after rename"):
        close.close_matter(root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups")
    assert not (root / MID).exists()
    intent = CloseIntent.model_validate_json(
        (root / close.CLOSED_DIRNAME / f"{MID}.pending.json").read_text()
    )
    assert not intent.detached
    assert close._quarantine_path(root, intent).is_dir()
    monkeypatch.setattr(close, "_write_close_intent", original)
    result = close.close_matter(
        root, MID, actor="Reviewer", now=NOW, backup_dir=tmp_path / "backups"
    )
    assert result == intent.record
    assert not close._quarantine_path(root, intent).exists()
