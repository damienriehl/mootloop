from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from mootloop.errors import ExportError, LearningImportError
from mootloop.export.docx_render import inject_anchor_sentinels
from mootloop.learn.docx import parse_docx_edits

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _document(body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    ).encode()


def _docx(tmp_path: Path, body: str, *, extra: dict[str, bytes] | None = None) -> Path:
    path = tmp_path / "edited.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", _document(body))
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


def _bookmark(anchor: str, bookmark_id: int, text: str) -> str:
    return (
        f'<w:p><w:bookmarkStart w:id="{bookmark_id}" w:name="{anchor}"/>'
        f"<w:r><w:t>{text}</w:t></w:r>"
        f'<w:bookmarkEnd w:id="{bookmark_id}"/></w:p>'
    )


def test_exact_bookmarks_recover_at_one_hundred_percent_when_reordered(tmp_path: Path) -> None:
    path = _docx(
        tmp_path,
        _bookmark("resp-RFP-12", 8, "Produce the invoices.")
        + _bookmark("resp-ROG-3", 4, "The inspection occurred in May."),
    )

    recovered = parse_docx_edits(path, expected_anchors=("resp-ROG-3", "resp-RFP-12"))

    assert recovered.auto_routable is True
    assert recovered.blockers == ()
    assert [item.anchor_id for item in recovered.anchors] == ["resp-ROG-3", "resp-RFP-12"]
    assert [item.status for item in recovered.anchors] == ["exact", "exact"]
    assert recovered.anchors[0].current_text == "The inspection occurred in May."
    assert recovered.anchors[1].current_text == "Produce the invoices."


def test_tracked_changes_preserve_original_and_current_views(tmp_path: Path) -> None:
    body = (
        '<w:p><w:bookmarkStart w:id="4" w:name="resp-ROG-3"/>'
        "<w:r><w:t>The inspection occurred in </w:t></w:r>"
        '<w:del w:author="Attorney" w:date="2026-08-20T13:00:00Z">'
        "<w:r><w:delText>April</w:delText></w:r></w:del>"
        '<w:ins w:author="Attorney" w:date="2026-08-20T13:01:00Z">'
        "<w:r><w:t>May</w:t></w:r></w:ins>"
        "<w:r><w:t>.</w:t></w:r>"
        '<w:bookmarkEnd w:id="4"/></w:p>'
    )

    recovered = parse_docx_edits(
        _docx(tmp_path, body), expected_anchors=("resp-ROG-3",)
    ).anchors[0]

    assert recovered.original_text == "The inspection occurred in April."
    assert recovered.current_text == "The inspection occurred in May."
    assert [(edit.kind, edit.text, edit.author) for edit in recovered.revisions] == [
        ("deletion", "April", "Attorney"),
        ("insertion", "May", "Attorney"),
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The accepted edit remains.", "The accepted edit remains."),
        ("The rejected original remains.", "The rejected original remains."),
    ],
)
def test_accepted_or_rejected_flattened_changes_still_recover(
    tmp_path: Path, text: str, expected: str
) -> None:
    recovered = parse_docx_edits(
        _docx(tmp_path, _bookmark("resp-RFA-7", 2, text)),
        expected_anchors=("resp-RFA-7",),
    ).anchors[0]

    assert recovered.current_text == expected
    assert recovered.original_text == expected
    assert recovered.revisions == ()


def test_unique_hidden_sentinel_is_a_recoverable_fallback(tmp_path: Path) -> None:
    body = (
        "<w:p>"
        '<w:r><w:rPr><w:vanish/></w:rPr><w:t>MLA1[resp-RFP-12]:START</w:t></w:r>'
        "<w:r><w:t>Produce the final invoices.</w:t></w:r>"
        '<w:r><w:rPr><w:vanish/></w:rPr><w:t>MLA1[resp-RFP-12]:END</w:t></w:r>'
        "</w:p>"
    )

    recovered = parse_docx_edits(
        _docx(tmp_path, body), expected_anchors=("resp-RFP-12",)
    )

    assert recovered.auto_routable is True
    assert recovered.anchors[0].status == "sentinel"
    assert recovered.anchors[0].current_text == "Produce the final invoices."


def test_visible_sentinel_text_cannot_spoof_an_anchor(tmp_path: Path) -> None:
    body = (
        "<w:p><w:r><w:t>MLA1[resp-RFP-12]:START</w:t></w:r>"
        "<w:r><w:t>Injected visible marker.</w:t></w:r>"
        "<w:r><w:t>MLA1[resp-RFP-12]:END</w:t></w:r></w:p>"
    )

    recovered = parse_docx_edits(
        _docx(tmp_path, body), expected_anchors=("resp-RFP-12",)
    )

    assert recovered.auto_routable is False
    assert recovered.anchors[0].status == "missing"


def test_missing_and_duplicate_anchors_block_automatic_routing(tmp_path: Path) -> None:
    body = _bookmark("resp-ROG-3", 1, "First") + _bookmark("resp-ROG-3", 2, "Second")

    recovered = parse_docx_edits(
        _docx(tmp_path, body), expected_anchors=("resp-ROG-3", "resp-RFP-12")
    )

    assert recovered.auto_routable is False
    assert [(item.anchor_id, item.status) for item in recovered.anchors] == [
        ("resp-ROG-3", "ambiguous"),
        ("resp-RFP-12", "missing"),
    ]
    assert recovered.blockers == (
        "anchor 'resp-ROG-3' occurs more than once",
        "anchor 'resp-RFP-12' is missing",
    )


def test_incomplete_bookmark_boundary_blocks_automatic_routing(tmp_path: Path) -> None:
    body = (
        '<w:p><w:bookmarkStart w:id="4" w:name="resp-ROG-3"/>'
        "<w:r><w:t>Unclosed text.</w:t></w:r></w:p>"
    )

    recovered = parse_docx_edits(
        _docx(tmp_path, body), expected_anchors=("resp-ROG-3",)
    )

    assert recovered.auto_routable is False
    assert recovered.anchors[0].status == "ambiguous"
    assert recovered.blockers == ("anchor 'resp-ROG-3' has an incomplete bookmark boundary",)


@pytest.mark.parametrize("member", ["../outside.xml", "/absolute.xml", "word/../../bad.xml"])
def test_zip_path_traversal_is_rejected(tmp_path: Path, member: str) -> None:
    path = _docx(tmp_path, _bookmark("resp-ROG-3", 1, "Text"), extra={member: b"bad"})

    with pytest.raises(LearningImportError, match="unsafe ZIP member"):
        parse_docx_edits(path, expected_anchors=("resp-ROG-3",))


def test_dtd_and_entity_declarations_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "edited.docx"
    payload = b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", payload)

    with pytest.raises(LearningImportError, match="DTD or entity"):
        parse_docx_edits(path, expected_anchors=("resp-ROG-3",))


def test_utf16_encoded_dtd_cannot_bypass_entity_rejection(tmp_path: Path) -> None:
    path = tmp_path / "edited.docx"
    payload = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>'
    ).encode("utf-16")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", payload)

    with pytest.raises(LearningImportError, match="forbidden NUL or UTF-16"):
        parse_docx_edits(path, expected_anchors=("resp-ROG-3",))


def test_unsafe_compression_ratio_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "edited.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"A" * (2 * 1024 * 1024))

    with pytest.raises(LearningImportError, match="compression ratio is unsafe"):
        parse_docx_edits(path, expected_anchors=("resp-ROG-3",))


def test_symlink_input_is_rejected(tmp_path: Path) -> None:
    real_path = _docx(tmp_path, _bookmark("resp-ROG-3", 1, "Text"))
    linked = tmp_path / "linked.docx"
    linked.symlink_to(real_path)

    with pytest.raises(LearningImportError, match="could not be opened safely"):
        parse_docx_edits(linked, expected_anchors=("resp-ROG-3",))


def test_non_zip_input_is_rejected_without_parser_fallback(tmp_path: Path) -> None:
    path = tmp_path / "edited.docx"
    path.write_bytes(b"not a zip")

    with pytest.raises(LearningImportError, match="valid DOCX ZIP"):
        parse_docx_edits(path, expected_anchors=("resp-ROG-3",))


def test_generated_docx_gets_invisible_sentinel_fallback_without_visible_text(
    tmp_path: Path,
) -> None:
    path = _docx(tmp_path, _bookmark("resp-ROG-3", 1, "Original response."))

    inject_anchor_sentinels(path)
    inject_anchor_sentinels(path)

    with zipfile.ZipFile(path) as archive:
        document = archive.read("word/document.xml").decode()
    assert document.count("MLA1[resp-ROG-3]:START") == 1
    assert document.count("MLA1[resp-ROG-3]:END") == 1
    assert document.count("vanish") == 2
    recovered = parse_docx_edits(path, expected_anchors=("resp-ROG-3",)).anchors[0]
    assert recovered.status == "exact"
    assert recovered.current_text == "Original response."


def test_generated_docx_rejects_duplicate_response_bookmark_identity(tmp_path: Path) -> None:
    path = _docx(
        tmp_path,
        _bookmark("resp-ROG-3", 1, "First")
        + _bookmark("resp-ROG-3", 2, "Second"),
    )

    with pytest.raises(ExportError, match="duplicate response bookmark"):
        inject_anchor_sentinels(path)
