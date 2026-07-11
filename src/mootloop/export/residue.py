"""Residue scan (plan Phase 7 / D8): the filed copy must carry no annotations.

Opens the produced DOCX as a raw zip and asserts: no annotation-marker strings
(persona attributions like ``associate:``, and ``confidence=``, ``self_assessment``,
``MOOTLOOP-CANARY`` …), no comments part, and no tracked-change elements
(``w:ins``/``w:del``) in the document body. Returns a `GateResult`; clean export is
blocked on any finding (fail closed).
"""

from __future__ import annotations

import zipfile
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

# Body parts that may carry visible text / revisions.
_TEXT_PARTS = ("word/document.xml",)


def _is_text_part(name: str) -> bool:
    return name == "word/document.xml" or name.startswith(("word/header", "word/footer"))


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
        if any(n.startswith("word/comments") for n in names):
            findings.append(
                GateFinding(
                    code="comments_part",
                    message="DOCX contains a comments part",
                    locator="word/comments.xml",
                )
            )
        for name in names:
            if not _is_text_part(name):
                continue
            raw = archive.read(name).decode("utf-8", errors="replace")
            if "<w:ins" in raw or "<w:del" in raw:
                findings.append(
                    GateFinding(
                        code="tracked_changes",
                        message=f"tracked-change element in {name}",
                        locator=name,
                    )
                )
            lowered = raw.lower()
            for marker in _ANNOTATION_MARKERS:
                if marker in lowered:
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
