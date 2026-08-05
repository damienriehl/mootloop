"""Repo-relative resource resolution for the ``config/`` and ``personas/`` trees.

These are packaged authoring artifacts (task configs, persona bodies), not vault
data, so they resolve from the repo root — the plugin runs in-repo in v1. Kept in
one place so a future move to ``importlib.resources`` touches a single module.
"""

from __future__ import annotations

import re
from pathlib import Path

from mootloop.errors import ExportError

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
PERSONAS_DIR = REPO_ROOT / "personas"
RUBRICS_DIR = REPO_ROOT / "rubrics"
COURTS_DIR = CONFIG_DIR / "courts"

DEFAULT_REFERENCE_DOC = "generic-mn-district"


def task_config_path(task: str) -> Path:
    return CONFIG_DIR / "tasks" / f"{task}.yaml"


_REFERENCE_DOC_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def reference_doc_path(name: str, *, draft: bool = False) -> Path:
    """The pandoc court reference-doc for ``name`` (``generic-mn-district``), or its
    ``-draft`` variant (the DRAFT-watermark template) when ``draft`` (plan D8).

    ``name`` reaches this from `export_run`'s public keyword argument, so it is
    validated rather than joined: a traversing name would otherwise select an
    arbitrary file as the template that decides whether a filing is watermarked.
    """
    if not _REFERENCE_DOC_NAME.match(name):
        raise ExportError(f"invalid court reference-doc name: {name!r}")
    stem = f"{name}-draft" if draft else name
    return COURTS_DIR / f"{stem}.docx"


def rubric_path(rubric_id: str) -> Path:
    return RUBRICS_DIR / f"{rubric_id}.yaml"


def persona_body(slug: str) -> str:
    """The task-agnostic excellence body for a persona (``personas/<slug>.md``)."""
    return (PERSONAS_DIR / f"{slug}.md").read_text(encoding="utf-8")
