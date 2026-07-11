"""Residue-scan deterministic cases (plan Phase 7 / D8). Builds DOCX directly with
python-docx (no pandoc needed) so these run everywhere."""

from __future__ import annotations

import zipfile
from pathlib import Path

from docx import Document

from mootloop.export.residue import scan_docx


def _docx(tmp_path: Path, text: str, name: str = "d.docx") -> Path:
    document = Document()
    document.add_paragraph(text)
    path = tmp_path / name
    document.save(str(path))
    return path


def test_clean_docx_passes(tmp_path: Path) -> None:
    path = _docx(tmp_path, "Defendant objects on relevance grounds. Admitted in part.")
    assert scan_docx(path).status == "pass"


def test_planted_annotation_marker_fails(tmp_path: Path) -> None:
    path = _docx(tmp_path, "associate: my self_assessment here with confidence=0.9")
    result = scan_docx(path)
    assert result.status == "fail"
    assert any(f.code == "annotation_marker" for f in result.findings)


def test_canary_marker_fails(tmp_path: Path) -> None:
    path = _docx(tmp_path, "Body text MOOTLOOP-CANARY leaked into the filing.")
    result = scan_docx(path)
    assert result.status == "fail"
    assert any(f.code == "annotation_marker" for f in result.findings)


def test_comments_part_fails(tmp_path: Path) -> None:
    clean = _docx(tmp_path, "A clean paragraph.")
    # Inject a comments part into the zip to simulate un-stripped review comments.
    doctored = tmp_path / "with-comments.docx"
    with zipfile.ZipFile(clean) as src, zipfile.ZipFile(doctored, "w") as dst:
        for item in src.namelist():
            dst.writestr(item, src.read(item))
        dst.writestr("word/comments.xml", "<w:comments/>")
    result = scan_docx(doctored)
    assert result.status == "fail"
    assert any(f.code == "comments_part" for f in result.findings)


def test_missing_file_fails(tmp_path: Path) -> None:
    result = scan_docx(tmp_path / "nope.docx")
    assert result.status == "fail"
