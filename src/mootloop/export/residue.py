"""Residue scan (plan Phase 7 / D8): the filed copy must carry no annotations.

Opens the produced DOCX as a raw zip and asserts, across every part that can carry
visible text (body, footnotes/endnotes, running heads) plus the package metadata:

  * no annotation-marker strings (persona attributions like ``associate:``, and
    ``confidence=``, ``self_assessment``, ``MOOTLOOP-CANARY`` …);
  * no draft-only renderer placeholders (``[ATTORNEY NAME]``, ``no response
    drafted`` …) — a served copy must never carry the scaffolding the court-master
    renderer emits for an incomplete matter/run;
  * no review comments (an actual ``<w:comment>`` element, or a dangling comment
    anchor left behind after the comments part was stripped);
  * no tracked-change elements — insertions, deletions, moves, *and* the formatting
    revisions (``w:rPrChange``/``w:pPrChange`` …) that carry a reviser's name and
    timestamp.

Two hardening rules the naive version got wrong, both load-bearing:

  * Revision elements are matched as *elements*, never as substrings — ``<w:ins``
    also prefixes ``<w:insideH>``/``<w:insideV>`` (ordinary table borders), so a
    substring test can delete a perfectly clean filing.
  * Markers are matched against the extracted visible text as well as the raw XML,
    so a marker split across runs (``<w:t>associ</w:t><w:t>ate:</w:t>``, which is
    exactly what a Word round-trip produces) cannot slip through.

Returns a `GateResult`; clean export is blocked on any finding (fail closed).
"""

from __future__ import annotations

import re
import zipfile
from html import unescape
from pathlib import Path

from mootloop.models.gates import GateFail, GateFinding, GatePass, GateResult

GATE_NAME = "residue"

# Case-insensitive substrings that betray un-stripped annotation/attribution residue.
_ANNOTATION_MARKERS: tuple[str, ...] = (
    "self_assessment",
    "confidence=",
    "persuasion_notes",
    "objection_basis",
    "would_objection_survive",
    "mootloop-canary",
    "associate:",
    "partner:",
    "oc_associate:",
    "oc_partner:",
    "rubric_judge:",
    "cite_checker:",
)

# Draft-only scaffolding the court-master renderer emits when the matter or the run is
# incomplete (a matter with no signing attorney, a request with no operative draft).
# Matched on the RENDERED text: markdown emphasis around ``_no response drafted_`` is
# gone by the time pandoc has written the DOCX, the brackets are not.
_PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "[attorney name]",
    "[plaintiff]",
    "[defendant]",
    "no response drafted",
)

_MARKERS: tuple[str, ...] = _ANNOTATION_MARKERS + _PLACEHOLDER_MARKERS

# Parts that carry visible text: the body, the notes, and the running heads.
_BODY_PARTS: frozenset[str] = frozenset(
    {"word/document.xml", "word/footnotes.xml", "word/endnotes.xml"}
)
# Package metadata (title/subject/description/keywords) can carry leaked text too.
_METADATA_PARTS: frozenset[str] = frozenset({"docProps/core.xml"})

# Every WordprocessingML revision-tracking element. Formatting revisions
# (``w:rPrChange`` …) matter as much as content ones: they carry the reviser's name.
_REVISION_ELEMENTS: tuple[str, ...] = (
    "ins",
    "del",
    "delText",
    "delInstrText",
    "moveFrom",
    "moveTo",
    "moveFromRangeStart",
    "moveToRangeStart",
    "pPrChange",
    "rPrChange",
    "sectPrChange",
    "tblPrChange",
    "trPrChange",
    "tcPrChange",
    "tblGridChange",
    "numberingChange",
    "cellIns",
    "cellDel",
    "cellMerge",
)
# ``(?=[\s/>])`` is what keeps ``<w:ins`` from matching ``<w:insideH>``.
_REVISION_RE = re.compile(r"<w:(?:" + "|".join(_REVISION_ELEMENTS) + r")(?=[\s/>])")

# Comment anchors that survive when only the comments part is stripped.
_COMMENT_ANCHOR_RE = re.compile(
    r"<w:(?:commentRangeStart|commentRangeEnd|commentReference)(?=[\s/>])"
)

# Run text, excluding self-closing ``<w:t/>`` (which carries none).
_TEXT_RE = re.compile(
    r"<w:(?:t|delText|instrText|delInstrText)(?:\s[^>]*)?>(.*?)</w:", re.DOTALL
)


def _is_body_part(name: str) -> bool:
    return name in _BODY_PARTS or name.startswith(("word/header", "word/footer"))


def _is_scanned_part(name: str) -> bool:
    return _is_body_part(name) or name in _METADATA_PARTS


def _visible_text(raw: str) -> str:
    """The concatenated run text of a WordprocessingML part.

    Concatenating (rather than joining) is deliberate: a marker split across adjacent
    runs must reassemble into the marker, which is the whole point of this pass.
    """
    return unescape("".join(_TEXT_RE.findall(raw)))


def scan_docx(docx_path: Path | str) -> GateResult:
    """Scan a produced DOCX for annotation residue, comments, or tracked changes."""
    path = Path(docx_path)
    if not path.is_file():
        return GateFail(
            gate=GATE_NAME,
            findings=[GateFinding(code="missing", message=f"DOCX not found: {path}")],
        )
    findings: list[GateFinding] = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name in (n for n in names if n.startswith("word/comments")):
            # Modern pandoc (>= 3.1) always emits an EMPTY <w:comments/> scaffold
            # part; only actual <w:comment> elements are review-comment residue.
            raw = archive.read(name).decode("utf-8", errors="replace")
            if "<w:comment " in raw or "<w:comment>" in raw:
                findings.append(
                    GateFinding(
                        code="comments_part",
                        message=f"DOCX contains review comments in {name}",
                        locator=name,
                    )
                )
        for name in names:
            if not _is_scanned_part(name):
                continue
            raw = archive.read(name).decode("utf-8", errors="replace")
            if _is_body_part(name):
                if _REVISION_RE.search(raw):
                    findings.append(
                        GateFinding(
                            code="tracked_changes",
                            message=f"tracked-change element in {name}",
                            locator=name,
                        )
                    )
                if _COMMENT_ANCHOR_RE.search(raw):
                    findings.append(
                        GateFinding(
                            code="comments_part",
                            message=f"dangling review-comment anchor in {name}",
                            locator=name,
                        )
                    )
            haystacks = (raw.lower(), _visible_text(raw).lower())
            for marker in _MARKERS:
                if any(marker in haystack for haystack in haystacks):
                    findings.append(
                        GateFinding(
                            code="annotation_marker",
                            message=f"annotation marker {marker!r} found in {name}",
                            locator=name,
                        )
                    )
    if findings:
        return GateFail(gate=GATE_NAME, findings=findings)
    return GatePass(gate=GATE_NAME)
