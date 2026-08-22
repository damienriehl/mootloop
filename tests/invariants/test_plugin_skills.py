"""Plugin namespace and side-effecting-skill invocation guards."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SKILLS = _ROOT / ".claude" / "skills"
_SIDE_EFFECTING = {"setup", "ingest", "run", "decide", "export", "learn"}
_EXPECTED = {*_SIDE_EFFECTING, "status"}


def _frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    _, raw, _ = text.split("---", 2)
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict), path
    return parsed


def test_plugin_manifest_namespaces_repo_skills() -> None:
    manifest = json.loads(
        (_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "mootloop"
    assert manifest["license"] == "MIT"
    assert manifest["skills"] == "./.claude/skills/"
    assert set(manifest["agents"]) == {
        f"./.claude/agents/{name}.md"
        for name in {
            "associate",
            "judge",
            "oc-associate",
            "oc-partner",
            "partner",
            "rubric-judge",
        }
    }


def test_every_planned_skill_exists_and_side_effects_need_explicit_invocation() -> None:
    frontmatters = {
        str(frontmatter["name"]): frontmatter
        for skill in sorted(_SKILLS.iterdir())
        if skill.is_dir() and (path := skill / "SKILL.md").is_file()
        for frontmatter in [_frontmatter(path)]
    }
    assert set(frontmatters) == _EXPECTED
    for name in _SIDE_EFFECTING:
        assert frontmatters[name].get("disable-model-invocation") is True, name
    assert "disable-model-invocation" not in frontmatters["status"]
