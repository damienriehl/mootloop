"""Defensive raw-OOXML recovery for attorney-edited DOCX work product."""

from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from mootloop.errors import LearningImportError
from mootloop.models.learnings import DocxEditRecovery, DocxRevision, RecoveredAnchor

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DOCUMENT_MEMBER = "word/document.xml"
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_MEMBER_BYTES = 16 * 1024 * 1024
_MAX_MEMBERS = 2_048
_MAX_COMPRESSION_RATIO = 1_000
_MAX_XML_TAGS = 250_000
_ANCHOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SPACE_RE = re.compile(r"\s+")
_SENTINEL_RE = re.compile(r"^MLA1\[([A-Za-z0-9][A-Za-z0-9._:-]{0,127})\]:(START|END)$")


@dataclass
class _Occurrence:
    current: list[str] = field(default_factory=list)
    original: list[str] = field(default_factory=list)
    revisions: list[DocxRevision] = field(default_factory=list)
    closed: bool = False


@dataclass(frozen=True)
class _TextEvent:
    text: str
    hidden: bool


def _tag(local: str) -> str:
    return f"{{{_W}}}{local}"


def _attr(element: ElementTree.Element, local: str) -> str | None:
    return element.attrib.get(_tag(local))


def _normalize(parts: list[str] | str) -> str:
    value = "".join(parts) if isinstance(parts, list) else parts
    return _SPACE_RE.sub(" ", value).strip()


def read_docx_source(path: Path) -> bytes:
    try:
        file_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise LearningImportError(f"edited DOCX could not be opened safely: {exc}") from exc
    try:
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise LearningImportError("edited DOCX must be a regular file")
        if metadata.st_size > _MAX_ARCHIVE_BYTES:
            raise LearningImportError(
                f"edited DOCX exceeds the {_MAX_ARCHIVE_BYTES}-byte archive limit"
            )
        with os.fdopen(file_fd, "rb") as handle:
            file_fd = -1
            return handle.read(_MAX_ARCHIVE_BYTES + 1)
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _safe_member_name(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name:
        return False
    member = PurePosixPath(name)
    return not member.is_absolute() and ".." not in member.parts


def _validated_document_xml(source: bytes) -> bytes:
    try:
        archive = zipfile.ZipFile(io.BytesIO(source))
    except (OSError, zipfile.BadZipFile) as exc:
        raise LearningImportError("edited file is not a valid DOCX ZIP archive") from exc
    with archive:
        members = archive.infolist()
        if len(members) > _MAX_MEMBERS:
            raise LearningImportError(f"DOCX ZIP contains more than {_MAX_MEMBERS} members")
        seen: set[str] = set()
        total = 0
        document: zipfile.ZipInfo | None = None
        for member in members:
            if not _safe_member_name(member.filename):
                raise LearningImportError(f"unsafe ZIP member path: {member.filename!r}")
            if member.filename in seen:
                raise LearningImportError(f"duplicate ZIP member: {member.filename!r}")
            seen.add(member.filename)
            if member.flag_bits & 0x1:
                raise LearningImportError("encrypted DOCX ZIP members are not supported")
            mode = (member.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise LearningImportError(f"symlink ZIP member is forbidden: {member.filename!r}")
            if member.file_size > _MAX_MEMBER_BYTES:
                raise LearningImportError(
                    f"DOCX ZIP member exceeds {_MAX_MEMBER_BYTES} bytes: {member.filename!r}"
                )
            total += member.file_size
            if total > _MAX_ARCHIVE_BYTES:
                raise LearningImportError(
                    f"expanded DOCX ZIP exceeds {_MAX_ARCHIVE_BYTES} bytes"
                )
            if member.compress_size == 0:
                if member.file_size > 0:
                    raise LearningImportError(
                        f"DOCX ZIP member has an invalid compression ratio: {member.filename!r}"
                    )
            elif member.file_size / member.compress_size > _MAX_COMPRESSION_RATIO:
                raise LearningImportError(
                    f"DOCX ZIP member compression ratio is unsafe: {member.filename!r}"
                )
            if member.filename == _DOCUMENT_MEMBER:
                document = member
        if document is None:
            raise LearningImportError(f"DOCX ZIP is missing {_DOCUMENT_MEMBER}")
        try:
            payload = archive.read(document)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise LearningImportError("DOCX document XML could not be read safely") from exc
    if len(payload) != document.file_size:
        raise LearningImportError("DOCX document XML length does not match its ZIP record")
    if b"\x00" in payload:
        raise LearningImportError("DOCX document XML uses a forbidden NUL or UTF-16 encoding")
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise LearningImportError("DOCX document XML contains a forbidden DTD or entity")
    if payload.count(b"<") > _MAX_XML_TAGS:
        raise LearningImportError(f"DOCX document XML exceeds the {_MAX_XML_TAGS}-tag limit")
    return payload


def _revision_text(element: ElementTree.Element, *, deletion: bool) -> str:
    wanted = _tag("delText" if deletion else "t")
    return _normalize([child.text or "" for child in element.iter() if child.tag == wanted])


def _collect(
    root: ElementTree.Element,
) -> tuple[dict[str, list[_Occurrence]], list[_TextEvent], list[_TextEvent]]:
    occurrences: dict[str, list[_Occurrence]] = {}
    active: dict[str, _Occurrence] = {}
    global_current: list[_TextEvent] = []
    global_original: list[_TextEvent] = []

    def append_regular(text: str, *, hidden: bool = False) -> None:
        event = _TextEvent(text=text, hidden=hidden)
        global_current.append(event)
        global_original.append(event)
        if hidden and _SENTINEL_RE.fullmatch(text):
            return
        for occurrence in active.values():
            occurrence.current.append(text)
            occurrence.original.append(text)

    def append_revision(element: ElementTree.Element, *, deletion: bool) -> None:
        text = _revision_text(element, deletion=deletion)
        if not text:
            return
        if deletion:
            global_original.append(_TextEvent(text=text, hidden=False))
        else:
            global_current.append(_TextEvent(text=text, hidden=False))
        revision = DocxRevision(
            kind="deletion" if deletion else "insertion",
            text=text,
            author=_attr(element, "author"),
            date=_attr(element, "date"),
        )
        for occurrence in active.values():
            (occurrence.original if deletion else occurrence.current).append(text)
            occurrence.revisions.append(revision)

    def walk(element: ElementTree.Element, *, hidden: bool = False) -> None:
        if element.tag == _tag("bookmarkStart"):
            bookmark_id = _attr(element, "id")
            name = _attr(element, "name")
            if bookmark_id is not None and name is not None and not name.startswith("_"):
                occurrence = _Occurrence()
                occurrences.setdefault(name, []).append(occurrence)
                active[bookmark_id] = occurrence
            return
        if element.tag == _tag("bookmarkEnd"):
            bookmark_id = _attr(element, "id")
            if bookmark_id is not None and bookmark_id in active:
                active.pop(bookmark_id).closed = True
            return
        if element.tag == _tag("ins"):
            append_revision(element, deletion=False)
            return
        if element.tag == _tag("del"):
            append_revision(element, deletion=True)
            return
        if element.tag == _tag("t"):
            append_regular(element.text or "", hidden=hidden)
            return
        child_hidden = hidden
        if element.tag == _tag("r"):
            properties = element.find(_tag("rPr"))
            child_hidden = properties is not None and properties.find(_tag("vanish")) is not None
        for child in element:
            walk(child, hidden=child_hidden)
        if element.tag == _tag("p"):
            append_regular("\n")

    walk(root)
    return occurrences, global_current, global_original


def _sentinel_matches(events: list[_TextEvent], anchor_id: str) -> tuple[list[str], bool]:
    start = f"MLA1[{anchor_id}]:START"
    end = f"MLA1[{anchor_id}]:END"
    matches: list[str] = []
    current: list[str] | None = None
    unbalanced = False
    for event in events:
        if event.hidden and event.text == start:
            if current is not None:
                unbalanced = True
            current = []
            continue
        if event.hidden and event.text == end:
            if current is None:
                unbalanced = True
            else:
                matches.append(_normalize(current))
                current = None
            continue
        if current is not None:
            current.append(event.text)
    return matches, unbalanced or current is not None


def _recover_anchor(
    anchor_id: str,
    occurrences: dict[str, list[_Occurrence]],
    global_current: list[_TextEvent],
    global_original: list[_TextEvent],
) -> tuple[RecoveredAnchor, str | None]:
    exact = occurrences.get(anchor_id, [])
    if len(exact) == 1 and exact[0].closed:
        occurrence = exact[0]
        return (
            RecoveredAnchor(
                anchor_id=anchor_id,
                status="exact",
                original_text=_normalize(occurrence.original),
                current_text=_normalize(occurrence.current),
                revisions=tuple(occurrence.revisions),
            ),
            None,
        )
    if exact:
        return (
            RecoveredAnchor(anchor_id=anchor_id, status="ambiguous"),
            (
                f"anchor {anchor_id!r} occurs more than once"
                if len(exact) > 1
                else f"anchor {anchor_id!r} has an incomplete bookmark boundary"
            ),
        )
    current_sentinels, current_unbalanced = _sentinel_matches(global_current, anchor_id)
    original_sentinels, original_unbalanced = _sentinel_matches(global_original, anchor_id)
    if (
        len(current_sentinels) == 1
        and len(original_sentinels) == 1
        and not current_unbalanced
        and not original_unbalanced
    ):
        return (
            RecoveredAnchor(
                anchor_id=anchor_id,
                status="sentinel",
                original_text=original_sentinels[0],
                current_text=current_sentinels[0],
            ),
            None,
        )
    if current_sentinels or original_sentinels or current_unbalanced or original_unbalanced:
        return (
            RecoveredAnchor(anchor_id=anchor_id, status="ambiguous"),
            f"anchor {anchor_id!r} occurs more than once",
        )
    return (
        RecoveredAnchor(anchor_id=anchor_id, status="missing"),
        f"anchor {anchor_id!r} is missing",
    )


def parse_docx_edits(
    path: Path | str,
    *,
    expected_anchors: tuple[str, ...],
) -> DocxEditRecovery:
    """Recover exact stable-anchor text views directly from bounded, hostile OOXML."""
    return parse_docx_edits_bytes(
        read_docx_source(Path(path)), expected_anchors=expected_anchors
    )


def parse_docx_edits_bytes(
    source: bytes,
    *,
    expected_anchors: tuple[str, ...],
) -> DocxEditRecovery:
    """Recover stable anchors from caller-owned exact DOCX bytes."""
    if len(set(expected_anchors)) != len(expected_anchors):
        raise LearningImportError("expected anchor list contains duplicates")
    if not expected_anchors:
        raise LearningImportError("at least one expected anchor is required")
    for anchor_id in expected_anchors:
        if not _ANCHOR_RE.fullmatch(anchor_id):
            raise LearningImportError(f"invalid expected anchor id: {anchor_id!r}")
    if len(source) > _MAX_ARCHIVE_BYTES:
        raise LearningImportError(
            f"edited DOCX exceeds the {_MAX_ARCHIVE_BYTES}-byte archive limit"
        )
    payload = _validated_document_xml(source)
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise LearningImportError(f"DOCX document XML is malformed: {exc}") from exc
    occurrences, global_current, global_original = _collect(root)
    anchors: list[RecoveredAnchor] = []
    blockers: list[str] = []
    for anchor_id in expected_anchors:
        anchor, blocker = _recover_anchor(
            anchor_id, occurrences, global_current, global_original
        )
        anchors.append(anchor)
        if blocker is not None:
            blockers.append(blocker)
    return DocxEditRecovery(
        source_sha256=hashlib.sha256(source).hexdigest(),
        anchors=tuple(anchors),
        auto_routable=not blockers,
        blockers=tuple(blockers),
    )
