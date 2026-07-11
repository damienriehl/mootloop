"""Integration fixtures: one session-scoped baked demo vault shared by the web
bake + API tests (the bake drives the full pipeline, so bake it once)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mootloop.web.bake import build_demo_vault


@pytest.fixture(scope="session")
def demo_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("demo") / "vault"
    return build_demo_vault(dest)
