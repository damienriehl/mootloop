from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mootloop.demo_inputs import import_bundle, validate_bundle
from mootloop.models.demo import BundleFile, LocalInputBundle
from mootloop.web.catalog import PublicationError
from tests.unit.test_document_tasks import document_vault


def bundle_for(vault: Path) -> LocalInputBundle:
    import yaml

    from mootloop.models.matter import MatterConfig

    matter = MatterConfig.model_validate(yaml.safe_load((vault / "matter.yaml").read_text()))
    files = [
        ("matter.json", matter.model_dump_json()),
        ("document-motion-a.json", (vault / "documents/motion-a.json").read_text()),
    ]
    return LocalInputBundle(
        demo_id="employment-retaliation",
        revision="r1",
        task="complaint",
        software_revision="test",
        instructions="Import and start complaint.",
        files=tuple(
            BundleFile(name=name, text=text, sha256=hashlib.sha256(text.encode()).hexdigest())
            for name, text in files
        ),
    )


def test_import_fresh_identity_without_operational_state(tmp_path):
    source = document_vault(tmp_path / "source", "complaint")
    bundle = bundle_for(source)
    target = tmp_path / "fresh"
    import_bundle(
        bundle,
        target,
        matter_id="2026-09-20-example-review",
        registry_path=tmp_path / "registry.json",
    )
    assert (target / "documents/motion-a.json").read_bytes() == (
        source / "documents/motion-a.json"
    ).read_bytes()
    assert "2026-09-20-example-review" in (target / "matter.yaml").read_text()
    assert not list((target / "runs").glob("*/journal.jsonl"))
    with pytest.raises(PublicationError, match="empty"):
        import_bundle(bundle, target, matter_id="another", registry_path=tmp_path / "registry.json")


@pytest.mark.parametrize(
    "name", ["journal.jsonl", "secrets.env", "seal.json", "canary.txt", "matter.yaml"]
)
def test_bundle_rejects_unapproved_files(tmp_path, name):
    bundle = bundle_for(document_vault(tmp_path, "complaint"))
    extra = BundleFile(name=name, text="sentinel", sha256=hashlib.sha256(b"sentinel").hexdigest())
    bundle = bundle.model_copy(update={"files": (*bundle.files, extra)})
    with pytest.raises(PublicationError, match="allowlist"):
        validate_bundle(bundle)


def test_bundle_rejects_wrong_task(tmp_path):
    bundle = bundle_for(document_vault(tmp_path, "complaint"))
    with pytest.raises(PublicationError, match="task"):
        validate_bundle(bundle.model_copy(update={"task": "motion"}))


def test_cli_import_exposes_clean_local_workflow(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from mootloop.cli import app

    monkeypatch.setenv("MOOTLOOP_CANARY_REGISTRY", str(tmp_path / "new-registry.json"))
    source = document_vault(tmp_path / "source", "complaint")
    path = tmp_path / "bundle.json"
    path.write_text(bundle_for(source).model_dump_json())
    result = CliRunner().invoke(
        app,
        [
            "web",
            "import-demo",
            str(path),
            str(tmp_path / "imported"),
            "--matter-id",
            "2026-09-20-local-example",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "no run has been started" in result.output
    assert (tmp_path / "imported/documents/motion-a.json").exists()
