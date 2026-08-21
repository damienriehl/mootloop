"""Stable-anchor baseline extraction and deterministic CriticMarkup diffs."""

from __future__ import annotations

import difflib
import hashlib
import re
from pathlib import Path

from mootloop.errors import LearningImportError
from mootloop.vault import safe_vault_path

_MASTER_ANCHOR_RE = re.compile(
    r"^:::\s*\{#(?P<anchor>[A-Za-z0-9][A-Za-z0-9._:-]{0,127})\}\s*$"
    r"(?P<body>.*?)^:::\s*$",
    re.MULTILINE | re.DOTALL,
)
_MARKDOWN_MARKUP_RE = re.compile(r"[*_`]+")
_SPACE_RE = re.compile(r"\s+")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def baseline_anchors(vault_root: Path | str, run_id: str) -> dict[str, str]:
    path = safe_vault_path(vault_root, "deliverables", run_id, "master.md")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LearningImportError("run master.md is required for anchored edit comparison") from exc
    anchors: dict[str, str] = {}
    for match in _MASTER_ANCHOR_RE.finditer(raw):
        anchor = match.group("anchor")
        if anchor in anchors:
            raise LearningImportError(f"baseline anchor {anchor!r} occurs more than once")
        anchors[anchor] = _SPACE_RE.sub(
            " ", _MARKDOWN_MARKUP_RE.sub("", match.group("body"))
        ).strip()
    return anchors


def critic_markup(before: str, after: str) -> tuple[str, int]:
    left = before.split()
    right = after.split()
    output: list[str] = []
    changes = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=left, b=right).get_opcodes():
        old = " ".join(left[i1:i2])
        new = " ".join(right[j1:j2])
        if tag == "equal":
            output.append(old)
        elif tag == "delete":
            output.append(f"{{--{old}--}}")
            changes += i2 - i1
        elif tag == "insert":
            output.append(f"{{++{new}++}}")
            changes += j2 - j1
        else:
            output.append(f"{{~~{old}~>{new}~~}}")
            changes += max(i2 - i1, j2 - j1)
    return " ".join(output), changes
