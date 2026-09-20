from pathlib import Path

from mootloop.attest import current_master_sha256
from mootloop.export.master import build_court_master
from mootloop.llm import FakeLLMProvider
from mootloop.orchestrator import run_with_provider, start_run
from tests.unit.test_document_tasks import NOW, document_vault


def test_document_master_is_available_after_run(tmp_path: Path) -> None:
    vault = document_vault(tmp_path, "complaint")
    run = start_run(vault, "complaint", NOW, run_id="complaint")
    run_with_provider(vault, run, FakeLLMProvider(), NOW)
    assert current_master_sha256(vault, run) is not None
    master = build_court_master(vault, run, NOW)
    text = master.read_text()
    assert "DRAFT" in text
    assert "motion-a" in text
    assert "Certificate of Service" not in text
    assert "RESPONSES AND OBJECTIONS" not in text


def test_business_master_contains_every_reviewed_unit(tmp_path: Path) -> None:
    from mootloop.export.service import export_run
    from mootloop.models.document_task import DocumentTaskInput, DocumentUnit

    vault = document_vault(tmp_path, "business-advice")
    source = vault / "documents" / "motion-a.json"
    packet = DocumentTaskInput.model_validate_json(source.read_text())
    packet.units += (DocumentUnit(unit_id="notice", title="Notice", instructions="Draft notice"),)
    source.write_text(packet.model_dump_json())
    run = start_run(vault, "business-advice", NOW, run_id="business")
    run_with_provider(vault, run, FakeLLMProvider(), NOW)
    result = export_run(vault, run, NOW, force_draft=True)
    text = result.master.read_text()
    assert "#resp-motion-a" in text and "#resp-notice" in text
    assert "no document drafted" not in text
    from mootloop.orchestrator import operative_drafts

    for _, draft in operative_drafts(vault, run):
        assert draft is not None and draft.response_text in text
    assert result.is_draft and not result.export_ready
    assert result.verification is None
    assert "Certificate of Service" not in text


def test_document_human_blocker_preserves_review_copy(tmp_path: Path, monkeypatch) -> None:
    import pytest
    import yaml

    from mootloop import attest
    from mootloop.errors import AttestationBlockedError
    from mootloop.export.service import export_run
    from mootloop.llm import _default_output

    vault = document_vault(tmp_path, "complaint")
    path = vault / "matter.yaml"
    matter = yaml.safe_load(path.read_text())
    matter["gates"] = [{"name": "unsupported_assertion", "mode": "hard-human"}]
    path.write_text(yaml.safe_dump(matter))

    def unresolved(spec, prompt):
        output = _default_output(spec)
        output["attorney_gate_items"] = ["Confirm client authority"]
        return output

    monkeypatch.setattr("mootloop.export.docx_render.pandoc_available", lambda: True)
    run = start_run(vault, "complaint", NOW, run_id="blocked")
    state = run_with_provider(vault, run, FakeLLMProvider({"associate_draft": unresolved}), NOW)
    assert state.status == "needs_decisions"
    with pytest.raises(AttestationBlockedError):
        attest.attest(vault, run, "Attorney", NOW)
    for force in (False, True):
        result = export_run(vault, run, NOW, force_draft=force)
        assert result.is_draft and not result.export_ready
        assert "DRAFT" in result.master.read_text()
        assert not result.docx
        assert result.verification is None
        assert attest.latest_export_seal(vault, run) is None


def test_attested_document_export_preserves_reviewed_bytes(tmp_path: Path) -> None:
    from mootloop import attest
    from mootloop.export.service import export_run
    from mootloop.orchestrator import verify_run_citations

    vault = document_vault(tmp_path, "motion")
    run = start_run(vault, "motion", NOW, run_id="reviewed")
    run_with_provider(vault, run, FakeLLMProvider(), NOW)
    verify_run_citations(vault, run, NOW)
    master = vault / "deliverables" / run / "master.md"
    reviewed = master.read_bytes()
    attest.attest(vault, run, "Reviewer", NOW)
    result = export_run(vault, run, "2026-09-21T00:00:00+00:00")
    assert result.master.read_bytes() == reviewed
    assert result.attestation_state == "valid"
    assert result.is_draft
    assert not result.docx
    assert attest.latest_export_seal(vault, run) is None
