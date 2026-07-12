"""Unit tests for the matter registry (hosted-tier resolver + enumeration)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mootloop.errors import MatterNotFoundError, VaultBoundaryError
from mootloop.models.matter import SCHEMA_VERSION, MatterConfig
from mootloop.registry import MATTERS_ROOT_ENV, MatterRegistry
from mootloop.vault import create_vault


def _matter(matter_id: str) -> MatterConfig:
    return MatterConfig.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "matter_id": matter_id,
            "caption": {
                "court_name": "Superior Court of Example County",
                "case_number": "CV-2026-001",
                "county": "Example",
            },
            "jurisdiction": {"state": "MN", "forum": "state"},
            "parties": [],
            "our_side": "defendant",
            "retention": {"retention_class": "standard"},
        }
    )


def _seed(root: Path, matter_id: str) -> Path:
    return create_vault(root / matter_id, _matter(matter_id))


# --- resolve: charset validation --------------------------------------------


@pytest.mark.parametrize("bad", [".", "..", "a/b", "a\\b", "Acme", "has space", ""])
def test_resolve_rejects_bad_id(tmp_path: Path, bad: str) -> None:
    reg = MatterRegistry(root=tmp_path)
    with pytest.raises(VaultBoundaryError):
        reg.resolve(bad)


def test_resolve_returns_vault_for_valid_matter(tmp_path: Path) -> None:
    _seed(tmp_path, "acme-v-widgets")
    reg = MatterRegistry(root=tmp_path)
    resolved = reg.resolve("acme-v-widgets")
    assert resolved == (tmp_path / "acme-v-widgets").resolve()


# --- resolve: containment ---------------------------------------------------


def test_resolve_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "sneaky").symlink_to(outside)  # id is charset-valid but escapes via symlink
    reg = MatterRegistry(root=root)
    with pytest.raises(VaultBoundaryError):
        reg.resolve("sneaky")


def test_resolve_missing_matter_raises_not_found(tmp_path: Path) -> None:
    reg = MatterRegistry(root=tmp_path)
    with pytest.raises(MatterNotFoundError):
        reg.resolve("nope")


# --- list_matters -----------------------------------------------------------


def test_list_matters_over_mixed_root(tmp_path: Path) -> None:
    _seed(tmp_path, "alpha")
    _seed(tmp_path, "bravo")
    # An invalid dir: exists, but no matter.yaml -> skipped, not fatal.
    (tmp_path / "not-a-matter").mkdir()
    (tmp_path / "not-a-matter" / "readme.txt").write_text("junk", encoding="utf-8")
    # A stray file at the root -> ignored (not a directory).
    (tmp_path / "loose.txt").write_text("x", encoding="utf-8")

    reg = MatterRegistry(root=tmp_path)
    summaries = reg.list_matters()

    ids = [s.matter_id for s in summaries]
    assert ids == ["alpha", "bravo"]
    assert all(s.loaded for s in summaries)
    assert summaries[0].rel_path == "alpha"
    assert summaries[0].case_number == "CV-2026-001"


def test_list_matters_empty_when_root_absent(tmp_path: Path) -> None:
    reg = MatterRegistry(root=tmp_path / "does-not-exist")
    assert reg.list_matters() == []


# --- root injection ---------------------------------------------------------


def test_env_root_used_when_no_arg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path, "charlie")
    monkeypatch.setenv(MATTERS_ROOT_ENV, str(tmp_path))
    reg = MatterRegistry()
    assert [s.matter_id for s in reg.list_matters()] == ["charlie"]


def test_constructor_arg_overrides_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MATTERS_ROOT_ENV, "/srv/should-not-be-used")
    reg = MatterRegistry(root=tmp_path)
    assert reg.root == tmp_path
