from __future__ import annotations

import os
from pathlib import Path

import pytest

from mootloop.models.demo import DemoReleasePin
from mootloop.web.release import validate_collection

ROOT = Path(__file__).resolve().parents[2]


def test_release_pin_is_metadata_and_runtime_has_an_explicit_allowlist():
    pin = DemoReleasePin.model_validate_json((ROOT / "config/demos/release.json").read_bytes())
    assert pin.release_id in pin.url
    dockerfile = (ROOT / "Dockerfile").read_text()
    runtime = dockerfile.split(" AS runtime", 1)[1]
    assert "COPY --from=demo_release /release.tar" in dockerfile
    assert "tools/validate_demo_release.py" in dockerfile
    assert "COPY . ." not in dockerfile
    assert "COPY src ./src" not in runtime
    assert "mootloop web bake" not in dockerfile
    assert "USER 10001" in runtime
    assert "/ready" in runtime
    for writer in ("vault.py", "publish.py", "legacy_prepare.py", "orchestrator.py"):
        assert writer not in runtime


def test_staged_public_collection_is_complete_when_supplied():
    configured = os.environ.get("MOOTLOOP_PUBLIC_RELEASE")
    if not configured:
        pytest.skip("Pinned public release is validated in the image/release CI job")
    root = Path(configured).resolve()
    assert not root.is_relative_to(ROOT)
    catalog = validate_collection(root)
    assert len(catalog.entries) == 20
    assert catalog.legacy_sha256 is not None
