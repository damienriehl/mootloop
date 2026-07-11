"""Deterministic served-discovery parser — NO LLM.

Turns the text of a served interrogatory / RFP / RFA document into numbered
`RequestItem` work units with canonical opponent-owned IDs (``ROG-3``, ``RFP-12`` …),
lettered subparts (``ROG-3(a)``), and a `ParseReport` that surfaces numbering gaps
and duplicates as warnings — a request is never silently dropped.

Two shapes are handled: verbose per-request markers ("INTERROGATORY NO. 1:") and,
when none are present, a plain numbered list under a plural section heading
("INTERROGATORIES" then "1." … "2." …). Everything before the first request is
preamble (caption, instructions, definitions) and is discarded.
"""

from __future__ import annotations

import re
from pathlib import Path

from mootloop.models.common import DocId
from mootloop.models.requests import (
    ParseReport,
    RequestItem,
    RequestSet,
    RequestType,
    heading_label,
    make_request_id,
    marker_label,
    type_code,
)
from mootloop.vault import atomic_write_text, safe_vault_path

# A lettered subpart marker at the start of a line: "(a) ...".
_SUBPART_RE = re.compile(r"(?m)^[ \t]*\(([a-z])\)[ \t]*")


def _spaced_label(label: str) -> str:
    """Escape each word and rejoin with ``\\s+`` so runs of whitespace (including
    line wraps) between words still match."""
    return r"\s+".join(re.escape(word) for word in label.split())


def _verbose_marker_re(request_type: RequestType) -> re.Pattern[str]:
    label = _spaced_label(marker_label(request_type))
    return re.compile(
        rf"^[ \t]*{label}\s+(?:NO\.?|NUMBER|#)\s*(\d+)\s*[:.]?",
        re.IGNORECASE | re.MULTILINE,
    )


def _heading_re(request_type: RequestType) -> re.Pattern[str]:
    label = _spaced_label(heading_label(request_type))
    return re.compile(rf"^[ \t]*{label}[ \t]*:?[ \t]*$", re.IGNORECASE | re.MULTILINE)


_NUMBERED_RE = re.compile(r"^[ \t]*(\d+)\.[ \t]+", re.MULTILINE)


def _spans_to_items(
    matches: list[tuple[int, int, int]], text: str
) -> list[tuple[int, str]]:
    """Given ``(number, marker_start, marker_end)`` in document order, return
    ``(number, body_text)`` where the body runs to the next marker start."""
    items: list[tuple[int, str]] = []
    for idx, (number, _start, end) in enumerate(matches):
        next_start = matches[idx + 1][1] if idx + 1 < len(matches) else len(text)
        items.append((number, text[end:next_start].strip()))
    return items


def _find_requests(text: str, request_type: RequestType) -> list[tuple[int, str]]:
    """Locate top-level requests, preferring verbose markers, else a numbered list."""
    verbose = [
        (int(m.group(1)), m.start(), m.end())
        for m in _verbose_marker_re(request_type).finditer(text)
    ]
    if verbose:
        return _spans_to_items(verbose, text)

    heading = _heading_re(request_type).search(text)
    if heading is None:
        return []
    body = text[heading.end() :]
    offset = heading.end()
    numbered = [
        (int(m.group(1)), offset + m.start(), offset + m.end())
        for m in _NUMBERED_RE.finditer(body)
    ]
    return _spans_to_items(numbered, text)


def _subpart_items(
    request_type: RequestType,
    number: int,
    set_number: int,
    body: str,
    source_doc: DocId,
) -> list[RequestItem]:
    """Return the lettered subpart items found in ``body`` (empty if none)."""
    marks = list(_SUBPART_RE.finditer(body))
    items: list[RequestItem] = []
    for idx, mark in enumerate(marks):
        letter = mark.group(1)
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(body)
        items.append(
            RequestItem(
                request_id=make_request_id(request_type, number, letter),
                set_number=set_number,
                number=number,
                subpart=letter,
                text=body[mark.end() : end].strip(),
                source_doc=source_doc,
            )
        )
    return items


def _numbering_warnings(numbers: list[int], request_type: RequestType) -> list[str]:
    warnings: list[str] = []
    prefix = make_request_id(request_type, 0).rsplit("-", 1)[0]
    seen: set[int] = set()
    for n in numbers:
        if n in seen:
            warnings.append(f"duplicate {prefix} number {n}")
        seen.add(n)
    if numbers:
        expected = set(range(min(numbers), max(numbers) + 1))
        missing = sorted(expected - set(numbers))
        if missing:
            joined = ", ".join(str(m) for m in missing)
            warnings.append(f"numbering gap: missing {prefix} number(s) {joined}")
    return warnings


def parse_discovery_document(
    text: str,
    request_type: RequestType,
    source_doc: DocId,
    set_number: int = 1,
) -> ParseReport:
    """Parse ``text`` into a `RequestSet` (wrapped in a `ParseReport` with warnings)."""
    found = _find_requests(text, request_type)
    items: list[RequestItem] = []
    numbers: list[int] = []
    for number, body in found:
        numbers.append(number)
        items.append(
            RequestItem(
                request_id=make_request_id(request_type, number),
                set_number=set_number,
                number=number,
                subpart=None,
                text=body,
                source_doc=source_doc,
            )
        )
        items.extend(_subpart_items(request_type, number, set_number, body, source_doc))

    warnings = _numbering_warnings(numbers, request_type)
    if not found:
        warnings.append("no requests parsed — check the request type and document shape")

    request_set = RequestSet(
        request_type=request_type,
        set_number=set_number,
        title=f"{marker_label(request_type).title()} Set {set_number}",
        items=items,
    )
    return ParseReport(request_set=request_set, warnings=warnings)


def save_requests(vault_root: Path | str, request_set: RequestSet) -> Path:
    """Persist ``request_set`` atomically to ``requests/<code>-set<NN>.json``."""
    code = type_code(request_set.request_type)
    filename = f"{code}-set{request_set.set_number:02d}.json"
    path = safe_vault_path(vault_root, "requests", filename)
    atomic_write_text(path, request_set.model_dump_json(indent=2) + "\n")
    return path
