"""Regression-oracle isolation and zero-spend CI invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.invariant

REPO_ROOT = Path(__file__).resolve().parents[2]
ANSWER_KEY_ROOT = REPO_ROOT / "tests/oracles/answer_keys"
ISOLATION_SENTINEL = "HIDDEN-ORACLE-NORTHFIELD-7d8f40a3"


def test_answer_keys_are_outside_normal_prompt_and_matter_sources() -> None:
    assert ANSWER_KEY_ROOT.is_dir()
    assert not ANSWER_KEY_ROOT.is_relative_to(REPO_ROOT / "fixtures")
    assert not ANSWER_KEY_ROOT.is_relative_to(REPO_ROOT / "personas")
    assert not ANSWER_KEY_ROOT.is_relative_to(REPO_ROOT / "config")

    scanned = [REPO_ROOT / "fixtures", REPO_ROOT / "personas", REPO_ROOT / "config"]
    for root in scanned:
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                assert ISOLATION_SENTINEL not in text


def test_product_prompt_paths_do_not_reference_answer_key_tree() -> None:
    for path in (REPO_ROOT / "src/mootloop").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "tests/oracles" not in text
        assert "answer_keys" not in text


def test_fast_ci_excludes_paid_oracles_and_explicit_lane_is_available() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '-m "not paid_oracle"' in makefile
    assert "test-paid-oracles:" in makefile
    assert "--run-paid-oracles" in makefile
    assert "make check" in workflow
    assert "--run-paid-oracles" not in workflow
    for marker in ("deterministic:", "replayed:", "paid_oracle:"):
        assert marker in pyproject
