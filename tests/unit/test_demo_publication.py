from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from mootloop.models.demo import DemoCatalog, DemoDescriptor
from mootloop.web.catalog import PublicationError, PublicCatalog
from mootloop.web.publish import publish_release

ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def prepared_release(path: Path, release_id="release-1") -> Path:
    entries = []
    descriptors = yaml.safe_load((ROOT / "config/demos/catalog.yaml").read_text())["entries"]
    for descriptor in descriptors:
        demo_id = descriptor["demo_id"]
        # Publication mechanics use synthetic text, never real case packets.
        descriptor = dict(
            descriptor, collection="synthetic", task="complaint", court_level="state-trial"
        )
        descriptor.pop("cutoff", None)
        descriptor.pop("represented_side", None)
        descriptor = DemoDescriptor.model_validate(descriptor).model_dump(mode="json")
        base = path / demo_id / "r1"
        matter = {
            "schema_version": "1.0",
            "matter_id": demo_id,
            "caption": {"court_name": "Test court", "case_number": "test", "county": "Test"},
            "parties": [],
            "our_side": "plaintiff",
            "jurisdiction": {"state": "MN", "forum": "state"},
            "retention": {"retention_class": "synthetic"},
        }
        matter_text = json.dumps(matter)
        document_text = json.dumps(
            {
                "input_id": "test-input",
                "task": "complaint",
                "represented_side": "Applicant",
                "jurisdiction": "Test only",
                "court_level": "trial",
                "cutoff": "2026-09-20",
                "strategy_id": "first",
                "units": [
                    {
                        "unit_id": "one",
                        "title": "Test",
                        "instructions": "Test publication mechanics.",
                    }
                ],
            }
        )
        bundle_hash = write_json(
            base / "inputs.json",
            {
                "schema_version": "1.0",
                "demo_id": demo_id,
                "revision": "r1",
                "task": "complaint",
                "software_revision": "test",
                "instructions": "Local test inputs only.",
                "files": [
                    {
                        "name": "matter.json",
                        "text": matter_text,
                        "sha256": hashlib.sha256(matter_text.encode()).hexdigest(),
                    },
                    {
                        "name": "document-test-input.json",
                        "text": document_text,
                        "sha256": hashlib.sha256(document_text.encode()).hexdigest(),
                    },
                ],
            },
        )
        snapshot_hash = write_json(
            base / "snapshot.json",
            {
                "schema_version": "1.0",
                "descriptor": descriptor,
                "revision": "r1",
                "provenance": {
                    "preparation": "scripted-replay",
                    "authorship": "Test fixture",
                    "editorial_changes": "Synthetic test data",
                    "provider_calls": 0,
                    "limitations": ["Not legal analysis"],
                },
                "strategies": [
                    {
                        "strategy_id": "first",
                        "title": "Test strategy",
                        "run_id": "test-run",
                        "input_summary": "Test-only data.",
                        "stages": [
                            {
                                "kind": kind,
                                "title": kind,
                                "text": "Test-only prose.",
                                "turn_ids": [kind],
                            }
                            for kind in ("initial", "critique", "revised", "assessment")
                        ],
                        "gate_state": {
                            "run_status": "finished",
                            "export_ready": False,
                            "blockers": ["attestation"],
                            "results": {"rubric": "pass"},
                        },
                    }
                ],
                "local_instructions": "Local test instructions.",
                "bundle_sha256": bundle_hash,
            },
        )
        review_hash = write_json(
            base / "review.json",
            {
                "schema_version": "1.0",
                "demo_id": demo_id,
                "revision": "r1",
                "snapshot_sha256": snapshot_hash,
                "bundle_sha256": bundle_hash,
                "reviewer_kind": "agent-editor",
                "source_support_review": "Synthetic test content only.",
                "publication_eligible": True,
            },
        )
        entries.append(
            {
                "descriptor": descriptor,
                "revision": "r1",
                "snapshot_sha256": snapshot_hash,
                "bundle_sha256": bundle_hash,
                "review_sha256": review_hash,
            }
        )
    write_json(
        path / "catalog.json",
        {"schema_version": "1.0", "release_id": release_id, "entries": entries},
    )
    return path


def test_publish_and_read_immutable_reviewed_release(tmp_path):
    source = prepared_release(tmp_path / "source")
    target = tmp_path / "public"
    publish_release(
        source,
        target,
        manifest=DemoCatalog.model_validate_json((source / "catalog.json").read_bytes()),
    )
    reader = PublicCatalog(target)
    assert len(reader.catalog().entries) == 20
    snapshot = reader.snapshot("supplier-discovery", "r1")
    assert snapshot.strategies[0].gate_state.blockers == ("attestation",)
    assert not snapshot.provenance.attorney_approval
    assert reader.bundle("supplier-discovery", "r1").demo_id == "supplier-discovery"
    with pytest.raises(PublicationError):
        reader.snapshot("../outside", "r1")


@pytest.mark.parametrize("damage", ["missing", "changed", "extra", "symlink", "canary", "secret"])
def test_bad_twentieth_entry_preserves_previous_release(tmp_path, damage):
    source = prepared_release(tmp_path / "source")
    target = tmp_path / "public"
    manifest = DemoCatalog.model_validate_json((source / "catalog.json").read_bytes())
    publish_release(source, target, manifest=manifest)
    last = source / manifest.entries[-1].descriptor.demo_id / "r1"
    if damage == "missing":
        (last / "snapshot.json").unlink()
    elif damage == "changed":
        (last / "snapshot.json").write_text("{}")
    elif damage == "extra":
        (last / "journal.jsonl").write_text("private")
    elif damage == "symlink":
        (last / "review.json").unlink()
        (last / "review.json").symlink_to(last / "snapshot.json")
    else:
        raw = (last / "snapshot.json").read_text()
        marker = (
            "MOOTLOOP-CANARY-test-secret"
            if damage == "canary"
            else "sk-proj-sentinelcredential01234567890123456789"
        )
        (last / "snapshot.json").write_text(raw.replace("Test-only prose.", marker))
    with pytest.raises(PublicationError):
        publish_release(source, target, manifest=manifest)
    assert len(PublicCatalog(target).catalog().entries) == 20
    assert (target / "CURRENT").read_text() == "release-1\n"


def test_corrupt_public_artifact_fails_closed(tmp_path):
    source = prepared_release(tmp_path / "source")
    manifest = DemoCatalog.model_validate_json((source / "catalog.json").read_bytes())
    target = tmp_path / "public"
    publish_release(source, target, manifest=manifest)
    (target / "releases/release-1/supplier-discovery/r1/snapshot.json").write_text("{}")
    with pytest.raises(PublicationError):
        PublicCatalog(target).snapshot("supplier-discovery", "r1")


def rebind_review(source, demo_id):
    catalog_path = source / "catalog.json"
    catalog = json.loads(catalog_path.read_text())
    entry = next(item for item in catalog["entries"] if item["descriptor"]["demo_id"] == demo_id)
    base = source / demo_id / "r1"
    for filename, field in [("snapshot.json", "snapshot_sha256"), ("inputs.json", "bundle_sha256")]:
        entry[field] = hashlib.sha256((base / filename).read_bytes()).hexdigest()
    review = json.loads((base / "review.json").read_text())
    review.update(snapshot_sha256=entry["snapshot_sha256"], bundle_sha256=entry["bundle_sha256"])
    entry["review_sha256"] = write_json(base / "review.json", review)
    write_json(catalog_path, catalog)


@pytest.mark.parametrize(
    "marker", ["MOOTLOOP-CANARY-test-secret", "sk-proj-sentinelcredential01234567890123456789"]
)
def test_review_cannot_authorize_credential_markers(tmp_path, marker):
    source = prepared_release(tmp_path / "source")
    manifest = DemoCatalog.model_validate_json((source / "catalog.json").read_bytes())
    path = source / "supplier-discovery/r1/snapshot.json"
    path.write_text(path.read_text().replace("Test-only prose.", marker))
    rebind_review(source, "supplier-discovery")
    with pytest.raises(PublicationError, match="credential or canary"):
        publish_release(source, tmp_path / "public", manifest=manifest)
    assert not (tmp_path / "public/CURRENT").exists()


def test_immutable_release_id_cannot_be_reused_for_changed_reviewed_bytes(tmp_path):
    source = prepared_release(tmp_path / "source")
    manifest = DemoCatalog.model_validate_json((source / "catalog.json").read_bytes())
    target = tmp_path / "public"
    publish_release(source, target, manifest=manifest)
    path = source / "supplier-discovery/r1/snapshot.json"
    path.write_text(path.read_text().replace("Test-only prose.", "Changed reviewed prose."))
    rebind_review(source, "supplier-discovery")
    with pytest.raises(PublicationError, match="new revision|different bytes"):
        publish_release(source, target, manifest=manifest)
    assert (
        PublicCatalog(target).snapshot("supplier-discovery", "r1").strategies[0].stages[0].text
        == "Test-only prose."
    )


def test_production_manifest_cannot_be_replaced_by_twenty_wrong_descriptors(tmp_path):
    source = prepared_release(tmp_path / "source")
    with pytest.raises(PublicationError, match="approved collection"):
        publish_release(source, tmp_path / "public")


def test_public_case_url_is_not_mistaken_for_an_api_key():
    from mootloop.web.publish import _CREDENTIAL

    assert (
        _CREDENTIAL.search(b"https://example.org/musk-vs-altman-motion-preliminary-injunction.pdf")
        is None
    )
    assert _CREDENTIAL.search(b'"key":"sk-proj-abcdefghijklmnopqrstuvwxyz"') is not None
