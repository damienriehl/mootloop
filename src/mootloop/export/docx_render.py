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
from pathlib import Path

from mootloop.errors import ExportError, PandocMissingError


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
    return out
