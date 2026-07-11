"""Repo-relative resource resolution for the ``config/`` and ``personas/`` trees.

These are packaged authoring artifacts (task configs, persona bodies), not vault
data, so they resolve from the repo root — the plugin runs in-repo in v1. Kept in
one place so a future move to ``importlib.resources`` touches a single module.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
PERSONAS_DIR = REPO_ROOT / "personas"
RUBRICS_DIR = REPO_ROOT / "rubrics"


def task_config_path(task: str) -> Path:
    return CONFIG_DIR / "tasks" / f"{task}.yaml"


def rubric_path(rubric_id: str) -> Path:
    return RUBRICS_DIR / f"{rubric_id}.yaml"


def persona_body(slug: str) -> str:
    """The task-agnostic excellence body for a persona (``personas/<slug>.md``)."""
    return (PERSONAS_DIR / f"{slug}.md").read_text(encoding="utf-8")
