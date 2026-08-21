"""DOCX rendering via pandoc (plan D8).

`render_docx` shells out to the pandoc CLI (subprocess, no shell) with
``--reference-doc`` so the court template owns page geometry, base font, and the
DRAFT-watermark chrome that python-docx cannot inject into rendered output. When
pandoc is not installed the render degrades gracefully: the caller keeps the
court-formatted markdown and surfaces a clear error (this environment has no pandoc,
so the DOCX-dependent tests skip).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from mootloop.errors import ExportError, PandocMissingError
from mootloop.vault import fsync_file_and_parent

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DOCUMENT_MEMBER = "word/document.xml"


def _w(local: str) -> str:
    return f"{{{_W}}}{local}"


def _sentinel_run(text: str) -> ElementTree.Element:
    run = ElementTree.Element(_w("r"))
    properties = ElementTree.SubElement(run, _w("rPr"))
    ElementTree.SubElement(properties, _w("vanish"))
    content = ElementTree.SubElement(run, _w("t"))
    content.text = text
    return run


def _is_hidden_sentinel(element: ElementTree.Element, token: str) -> bool:
    properties = element.find(_w("rPr"))
    text = element.find(_w("t"))
    return (
        element.tag == _w("r")
        and properties is not None
        and properties.find(_w("vanish")) is not None
        and text is not None
        and text.text == token
    )


def inject_anchor_sentinels(path: Path | str) -> None:
    """Add hidden fallback markers inside every generated ``resp-*`` bookmark."""
    docx = Path(path)
    try:
        with zipfile.ZipFile(docx) as archive:
            members = archive.infolist()
            payloads = {member.filename: archive.read(member) for member in members}
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ExportError("generated DOCX could not be reopened for anchor hardening") from exc
    document = payloads.get(_DOCUMENT_MEMBER)
    if document is None:
        raise ExportError(f"generated DOCX is missing {_DOCUMENT_MEMBER}")
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise ExportError("generated DOCX document XML is malformed") from exc
    parents = {child: parent for parent in root.iter() for child in parent}
    name_by_id: dict[str, str] = {}
    starts: list[tuple[ElementTree.Element, str]] = []
    ends: list[tuple[ElementTree.Element, str]] = []
    seen_names: set[str] = set()
    for element in root.iter(_w("bookmarkStart")):
        bookmark_id = element.attrib.get(_w("id"))
        name = element.attrib.get(_w("name"))
        if bookmark_id is not None and name is not None and name.startswith("resp-"):
            if bookmark_id in name_by_id or name in seen_names:
                raise ExportError("generated DOCX has duplicate response bookmark identity")
            name_by_id[bookmark_id] = name
            seen_names.add(name)
            starts.append((element, name))
    seen_ends: set[str] = set()
    for element in root.iter(_w("bookmarkEnd")):
        bookmark_id = element.attrib.get(_w("id"))
        if bookmark_id is not None and bookmark_id in name_by_id:
            if bookmark_id in seen_ends:
                raise ExportError("generated DOCX has duplicate response bookmark boundary")
            seen_ends.add(bookmark_id)
            ends.append((element, name_by_id[bookmark_id]))
    if not starts:
        return
    if len(starts) != len(ends):
        raise ExportError("generated DOCX has incomplete response bookmark boundaries")
    for element, name in starts:
        token = f"MLA1[{name}]:START"
        parent = parents[element]
        index = list(parent).index(element)
        siblings = list(parent)
        if index + 1 >= len(siblings) or not _is_hidden_sentinel(siblings[index + 1], token):
            parent.insert(index + 1, _sentinel_run(token))
    for element, name in ends:
        token = f"MLA1[{name}]:END"
        parent = parents[element]
        index = list(parent).index(element)
        siblings = list(parent)
        if index == 0 or not _is_hidden_sentinel(siblings[index - 1], token):
            parent.insert(index, _sentinel_run(token))
    ElementTree.register_namespace("w", _W)
    payloads[_DOCUMENT_MEMBER] = ElementTree.tostring(
        root, encoding="utf-8", xml_declaration=True
    )
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=docx.parent, prefix=".tmp-anchor-", suffix=".docx", delete=False
        ) as handle:
            temp_path = Path(handle.name)
        with zipfile.ZipFile(temp_path, "w") as output:
            for member in members:
                output.writestr(member, payloads[member.filename])
        temp_path.replace(docx)
        fsync_file_and_parent(docx)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExportError("generated DOCX anchor hardening could not be published") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def pandoc_available() -> bool:
    """True iff the pandoc CLI is on PATH."""
    return shutil.which("pandoc") is not None


def render_docx(
    master_path: Path | str,
    out_path: Path | str,
    reference_doc: Path | str,
    draft: bool,
) -> Path:
    """Render ``master_path`` (markdown) to ``out_path`` (DOCX) via pandoc.

    Raises `PandocMissingError` when pandoc is absent (the caller degrades to the
    markdown deliverables) and `ExportError` on a bad input or a pandoc failure.
    """
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise PandocMissingError(
            "pandoc is not installed — DOCX not rendered; the court-formatted "
            "markdown was still written"
        )
    master = Path(master_path)
    reference = Path(reference_doc)
    if not master.is_file():
        raise ExportError(f"master markdown not found: {master}")
    if not reference.is_file():
        raise ExportError(f"reference doc not found: {reference}")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        pandoc,
        str(master),
        "--from=markdown",
        "--to=docx",
        f"--reference-doc={reference}",
        f"--metadata=draft:{'true' if draft else 'false'}",
        "-o",
        str(out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)  # noqa: S603 - fixed argv, no shell
    except subprocess.CalledProcessError as exc:  # pragma: no cover - needs pandoc
        detail = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
        raise ExportError(f"pandoc failed rendering {master.name}: {detail}") from exc
    inject_anchor_sentinels(out)
    return out
