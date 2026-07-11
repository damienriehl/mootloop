"""Phase 1 structural invariants over the synthetic fixture.

- Every parsed request ID is canonical (``ROG-3`` / ``RFP-12`` / ``RFA-7`` with an
  optional lettered subpart).
- The corpus manifest and the normalized files reconcile exactly — no orphan in
  either direction — for a vault built from the fixture.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from mootloop.discovery_parser import parse_discovery_document
from mootloop.ingest import ingest_folder
from mootloop.models.common import DocId
from mootloop.models.corpus import Manifest
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestType
from mootloop.vault import init_vault

pytestmark = pytest.mark.invariant

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "fixtures" / "synthetic-matter"
REQUEST_ID_RE = re.compile(r"^(ROG|RFP|RFA)-\d+(\([a-z]\))?$")

_SERVED = [
    ("rogs-set1.txt", RequestType.INTERROGATORY),
    ("rfps-set1.txt", RequestType.RFP),
    ("rfas-set1.txt", RequestType.RFA),
]


def test_every_request_id_is_canonical() -> None:
    for filename, request_type in _SERVED:
        text = (FIXTURE / "served" / filename).read_text(encoding="utf-8")
        report = parse_discovery_document(text, request_type, DocId("doc-000000000000000a"))
        assert report.request_set.items, f"{filename} parsed no items"
        for item in report.request_set.items:
            assert REQUEST_ID_RE.match(item.request_id), f"non-canonical id {item.request_id}"


def test_manifest_and_normalized_files_reconcile(tmp_path: Path) -> None:
    matter = MatterConfig.model_validate(
        yaml.safe_load((FIXTURE / "matter.yaml").read_text(encoding="utf-8"))
    )
    vault = tmp_path / "vault"
    init_vault(vault, matter, registry_path=tmp_path / "canaries.json")
    ingest_folder(vault, FIXTURE / "source-docs", now="2026-07-11T00:00:00+00:00")

    manifest = Manifest.load(vault)
    manifest_normalized = {
        d.normalized_path for d in manifest.docs if d.normalized_path is not None
    }
    # forward: every declared normalized path exists on disk
    for rel in manifest_normalized:
        assert (vault / rel).is_file(), f"manifest references missing file {rel}"

    # backward: every normalized file on disk is declared in the manifest
    normalized_dir = vault / "corpus" / "normalized"
    on_disk = {
        f"corpus/normalized/{p.name}" for p in normalized_dir.iterdir() if p.is_file()
    }
    assert on_disk == manifest_normalized, "orphan normalized files vs. manifest"
