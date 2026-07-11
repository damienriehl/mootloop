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


def _with_comments_part(tmp_path: Path, comments_xml: str, name: str) -> Path:
    clean = _docx(tmp_path, "A clean paragraph.", name=f"src-{name}")
    doctored = tmp_path / name
    with zipfile.ZipFile(clean) as src, zipfile.ZipFile(doctored, "w") as dst:
        for item in src.namelist():
            dst.writestr(item, src.read(item))
        dst.writestr("word/comments.xml", comments_xml)
    return doctored


def test_comments_part_with_actual_comments_fails(tmp_path: Path) -> None:
    doctored = _with_comments_part(
        tmp_path,
        '<w:comments><w:comment w:id="1"><w:p>review note</w:p></w:comment></w:comments>',
        "with-comments.docx",
    )
    result = scan_docx(doctored)
    assert result.status == "fail"
    assert any(f.code == "comments_part" for f in result.findings)


def test_empty_comments_scaffold_passes(tmp_path: Path) -> None:
    # Pandoc >= 3.1 always emits an empty <w:comments/> part; an empty scaffold
    # carries no review-comment residue and must not block a clean export.
    doctored = _with_comments_part(tmp_path, "<w:comments/>", "empty-comments.docx")
    assert scan_docx(doctored).status == "pass"


def test_missing_file_fails(tmp_path: Path) -> None:
    result = scan_docx(tmp_path / "nope.docx")
    assert result.status == "fail"
