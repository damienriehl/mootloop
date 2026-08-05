"""Export-service invariants that need no vault: stale-clean-DOCX retirement and the
court reference-doc name guard (plan Phase 7 / D8)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from mootloop.errors import ExportError
from mootloop.export.service import _retire_clean_docx
from mootloop.resources import COURTS_DIR, DEFAULT_REFERENCE_DOC, reference_doc_path


def _touch(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"PK\x03\x04")
    return path


def test_retire_removes_clean_and_keeps_draft(tmp_path: Path) -> None:
    clean = _touch(tmp_path, "rog-set1.docx")
    draft = _touch(tmp_path, "rog-set1.DRAFT.docx")
    other = _touch(tmp_path, "rfp-set1.docx")

    removed = _retire_clean_docx(tmp_path)

    assert sorted(p.name for p in removed) == ["rfp-set1.docx", "rog-set1.docx"]
    assert not clean.exists() and not other.exists()
    assert draft.exists(), "the DRAFT copy is the one the attorney is meant to keep"


def test_retire_is_a_no_op_on_a_missing_dir(tmp_path: Path) -> None:
    assert _retire_clean_docx(tmp_path / "nope") == []


def test_reference_doc_resolves_both_variants() -> None:
    assert reference_doc_path(DEFAULT_REFERENCE_DOC).is_file()
    assert reference_doc_path(DEFAULT_REFERENCE_DOC, draft=True).is_file()
    assert reference_doc_path(DEFAULT_REFERENCE_DOC, draft=True).name.endswith("-draft.docx")


@pytest.mark.parametrize(
    "name",
    ["../../../etc/passwd", "generic/../../x", "generic-mn-district/../evil", "", "Generic"],
)
def test_reference_doc_name_is_validated(name: str) -> None:
    # `reference_doc` is a public keyword on `export_run`; a traversing name would pick
    # an arbitrary file as the template that decides whether a filing is watermarked.
    with pytest.raises(ExportError):
        reference_doc_path(name)


def test_reference_doc_stays_inside_the_courts_dir() -> None:
    resolved = reference_doc_path(DEFAULT_REFERENCE_DOC).resolve()
    assert resolved.parent == COURTS_DIR.resolve()


def _parts(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {
            name: archive.read(name).decode("utf-8", errors="replace")
            for name in archive.namelist()
            if name.endswith(".xml")
        }


def test_draft_template_carries_the_watermark_and_the_clean_one_does_not() -> None:
    """The DRAFT watermark lives ENTIRELY in the reference doc's header — pandoc's
    ``--metadata=draft:`` is inert for the docx writer, and the ``.DRAFT.docx`` suffix
    is only a filename. Regenerating the templates without the header would leave the
    watermark gating with nothing on the page to show for it."""
    draft_parts = _parts(reference_doc_path(DEFAULT_REFERENCE_DOC, draft=True))
    clean_parts = _parts(reference_doc_path(DEFAULT_REFERENCE_DOC))

    headers = [name for name in draft_parts if name.startswith("word/header")]
    assert headers, "the draft reference doc must carry a header part"
    assert any("DRAFT" in draft_parts[name] for name in headers)

    assert not [name for name in clean_parts if name.startswith("word/header")]
    assert not any("DRAFT" in body for body in clean_parts.values())
