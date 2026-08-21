"""Attestation canonicalization/invalidation, the gate ledger fold, and the STATE
marker mapping (plan D9/H8/Phase 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from mootloop import attest, gate_ledger, orchestrator
from mootloop.decisions import DecisionStore, open_by_taxonomy, resolve
from mootloop.discovery_parser import save_requests
from mootloop.errors import AttestationBlockedError, ExportNotReadyError, OrchestratorError
from mootloop.export.link import LinkSigner, mint_link
from mootloop.export.service import export_run
from mootloop.facts import FactStore
from mootloop.llm import FakeLLMProvider
from mootloop.models.common import DocId
from mootloop.models.gates import GateFail
from mootloop.models.matter import Attorney
from mootloop.models.requests import RequestItem, RequestSet, RequestType, make_request_id
from mootloop.orchestrator import run_with_provider, start_run, verify_run_citations
from mootloop.vault import init_vault, load_matter
from mootloop.web import audit as access_audit
from tests.conftest import make_matter

NOW = "2026-07-11T00:00:00+00:00"
LATER = "2026-07-12T00:00:00+00:00"


def _vault(tmp_path: Path, request_type: RequestType) -> Path:
    vault = tmp_path / "vault"
    matter = make_matter().model_copy(update={"attorney": Attorney(name="Jane")})
    init_vault(vault, matter, registry_path=tmp_path / "canaries.json")
    item = RequestItem(
        request_id=make_request_id(request_type, 1),
        set_number=1,
        number=1,
        text="Request 1 text.",
        source_doc=DocId("doc-servedservedserv"),
    )
    save_requests(
        vault, RequestSet(request_type=request_type, set_number=1, title="Set 1", items=[item])
    )
    FactStore(vault).add_fact("The contract price was $148,500.", confidence=1.0)
    return vault


def _finished_and_resolved(tmp_path: Path, run_id: str) -> Path:
    """A ROG run driven to finished, all decisions resolved, citations verified."""
    vault = _vault(tmp_path, RequestType.INTERROGATORY)
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    matter = load_matter(vault)
    for decision in [
        *open_by_taxonomy(vault, run_id, matter, "hard-human"),
        *open_by_taxonomy(vault, run_id, matter, "policy-delegable"),
    ]:
        resolve(
            vault,
            run_id,
            decision.decision_id,
            "approve",
            decision.proposal.recommended,
            "",
            "Atty",
            "human",
            NOW,
        )
    verify_run_citations(vault, run_id, NOW)
    return vault


def test_state_marker_mapping() -> None:
    assert orchestrator.state_marker("running") == "working"
    assert orchestrator.state_marker("needs_decisions") == "ask-pending"
    assert orchestrator.state_marker("checkpoint") == "ask-pending"
    assert orchestrator.state_marker("needs_attention") == "blocked"
    assert orchestrator.state_marker("capped") == "blocked"
    assert orchestrator.state_marker("finished") == "done"


def test_attest_blocked_while_decisions_open(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.RFA)
    run_id = "att-blocked"
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    # Hard-human RFA gate is open -> attestation refused.
    with pytest.raises(AttestationBlockedError):
        attest.attest(vault, run_id, "Jane", NOW)


def test_whitespace_only_edit_does_not_invalidate(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-ws")
    attest.attest(vault, "att-ws", "Jane", NOW)
    master = attest.master_deliverable_path(vault, "att-ws")
    assert master is not None
    # Append trailing whitespace + a blank line — canonicalization strips both.
    master.write_text(master.read_text() + "   \n\n", encoding="utf-8")
    assert attest.check_attestation(vault, "att-ws", LATER).status == "valid"
    assert gate_ledger.export_ready(vault, "att-ws")[0] is True


def test_content_edit_invalidates_and_blocks_export(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-edit")
    attest.attest(vault, "att-edit", "Jane", NOW)
    assert gate_ledger.export_ready(vault, "att-edit")[0] is True

    master = attest.master_deliverable_path(vault, "att-edit")
    assert master is not None
    master.write_text(master.read_text() + "\nInjected substantive clause.\n", encoding="utf-8")

    check = attest.check_attestation(vault, "att-edit", LATER)
    assert check.status == "invalidated"
    ready, blockers = gate_ledger.export_ready(vault, "att-edit")
    assert ready is False
    assert "attestation" in blockers


def test_matter_edit_invalidates_and_re_attestation_requires_new_run(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-matter-edit")
    attest.attest(vault, "att-matter-edit", "Jane", NOW)
    matter_path = vault / "matter.yaml"
    matter = yaml.safe_load(matter_path.read_text(encoding="utf-8"))
    matter["caption"]["case_number"] = "changed-after-launch"
    matter_path.write_text(yaml.safe_dump(matter), encoding="utf-8")

    check = attest.check_attestation(vault, "att-matter-edit", LATER)

    assert check.status == "invalidated"
    assert "changed after launch" in (check.reason or "")
    with pytest.raises(AttestationBlockedError, match="start a new run"):
        attest.attest(vault, "att-matter-edit", "Jane", LATER)


def test_check_attestation_missing_before_attest(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-missing")
    assert attest.check_attestation(vault, "att-missing", NOW).status == "missing"


def test_attest_fails_closed_before_append_when_manifest_is_tampered(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-context-tamper")
    manifest = vault / "runs" / "att-context-tamper" / "context" / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")

    with pytest.raises(OrchestratorError, match="context manifest.*(tampered|digest)"):
        attest.attest(vault, "att-context-tamper", "Jane", NOW)

    assert not (vault / "runs" / "att-context-tamper" / "attestations.jsonl").exists()


def test_legacy_attestation_is_reported_as_incompatible_not_content_drift(
    tmp_path: Path,
) -> None:
    vault = _finished_and_resolved(tmp_path, "att-legacy")
    path = vault / "runs" / "att-legacy" / "attestations.jsonl"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "attestation_id": "att-att-legacy-0000",
                "run_id": "att-legacy",
                "master_sha256": "legacy-md-master-only-digest",
                "ledger_head_sha256": attest.current_ledger_head_sha256(vault),
                "reviewer": "Jane",
                "attested_at": NOW,
                "valid": True,
                "reason": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    check = attest.check_attestation(vault, "att-legacy", LATER)

    assert check.status == "invalidated"
    assert check.reason == "legacy attestation hash scope is incompatible; re-attestation required"
    assert "changed after attestation" not in check.reason
    invalidation = attest.latest_attestation(vault, "att-legacy")
    assert invalidation is not None
    assert invalidation.valid is False
    assert invalidation.hash_scope == attest.MASTER_HASH_SCOPE
    assert invalidation.reason == check.reason


def test_new_attestation_persists_explicit_hash_scope(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-scoped")

    record = attest.attest(vault, "att-scoped", "Jane", NOW)

    assert record.hash_scope == attest.MASTER_HASH_SCOPE
    persisted = json.loads(
        (vault / "runs" / "att-scoped" / "attestations.jsonl").read_text(encoding="utf-8")
    )
    assert persisted["hash_scope"] == attest.MASTER_HASH_SCOPE
    assert attest.attestation_state(vault, "att-scoped").status == "valid"


def test_invalidation_validity_bit_is_bound_by_commitment(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-invalid-tamper")
    attest.attest(vault, "att-invalid-tamper", "Jane", NOW)
    master = attest.master_deliverable_path(vault, "att-invalid-tamper")
    assert master is not None
    master.write_text("changed\n", encoding="utf-8")
    attest.check_attestation(vault, "att-invalid-tamper", LATER)

    path = vault / "runs" / "att-invalid-tamper" / "attestations.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[-1])
    forged["valid"] = True
    lines[-1] = json.dumps(forged, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    check = attest.attestation_state(vault, "att-invalid-tamper")
    assert check.status == "invalidated"
    assert check.reason == "attestation commitment digest changed"


def test_attestation_binds_journal_decisions_facts_and_access_audit(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-complete")
    access_audit.append(
        vault,
        actor="jane@example.com",
        action="view",
        matter_id="2026-01-01-acme-test",
        resource="run/att-complete",
        ts=NOW,
    )

    record = attest.attest(vault, "att-complete", "Jane", NOW)

    assert record.journal_sha256 == attest.current_journal_sha256(vault, "att-complete")
    assert record.decisions_sha256 == attest.current_decisions_sha256(vault, "att-complete")
    assert record.fact_state_sha256 == attest.current_fact_state_sha256(vault, "att-complete")
    assert record.access_audit_head_sha256 == attest.current_access_audit_head_sha256(vault)
    assert record.commitment_sha256 == record.expected_commitment_sha256()


def test_citation_ledger_drift_invalidates_attestation(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-citation-drift")
    attest.attest(vault, "att-citation-drift", "Jane", NOW)
    ledger = vault / "law" / "verifications.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("ab") as handle:
        handle.write(b"{}\n")

    check = attest.attestation_state(vault, "att-citation-drift")

    assert check.status == "invalidated"
    assert check.reason == "citation ledger changed after attestation"


@pytest.mark.parametrize(
    ("relative_path", "reason"),
    [
        (("runs", "att-bound-log", "journal.jsonl"), "journal"),
        (("runs", "att-bound-log", "decisions", "decisions.jsonl"), "decision"),
    ],
)
def test_bound_log_byte_change_invalidates(
    tmp_path: Path, relative_path: tuple[str, ...], reason: str
) -> None:
    vault = _finished_and_resolved(tmp_path, "att-bound-log")
    attest.attest(vault, "att-bound-log", "Jane", NOW)

    path = vault.joinpath(*relative_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")

    check = attest.attestation_state(vault, "att-bound-log")
    assert check.status == "invalidated"
    assert reason in (check.reason or "")


def test_access_audit_may_append_but_attested_prefix_may_not_change(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-audit")
    access_audit.append(
        vault,
        actor="jane@example.com",
        action="view",
        matter_id="2026-01-01-acme-test",
        resource="run/att-audit",
        ts=NOW,
    )
    attest.attest(vault, "att-audit", "Jane", NOW)

    access_audit.append(
        vault,
        actor="jane@example.com",
        action="download",
        matter_id="2026-01-01-acme-test",
        resource="deliverables/att-audit/master.md",
        ts=LATER,
    )
    assert attest.attestation_state(vault, "att-audit").status == "valid"

    path = access_audit.audit_path(vault)
    path.write_text(
        path.read_text(encoding="utf-8").replace('"action":"view"', '"action":"edit"', 1),
        encoding="utf-8",
    )
    check = attest.attestation_state(vault, "att-audit")
    assert check.status == "invalidated"
    assert "access audit" in (check.reason or "")


def test_clean_export_is_sealed_and_single_byte_mutation_invalidates(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-export-set")
    attest.attest(vault, "att-export-set", "Jane", NOW)

    result = export_run(vault, "att-export-set", NOW)
    seal = attest.latest_export_seal(vault, "att-export-set")

    assert result.is_draft is False
    assert seal is not None
    assert seal.attestation_id == attest.latest_attestation(vault, "att-export-set").attestation_id
    assert seal.artifacts
    assert seal.export_set_sha256 == seal.expected_export_set_sha256()
    assert attest.sealed_export_state(vault, "att-export-set").status == "valid"
    signer = LinkSigner("unit-test-signing-key-0123456789abcdef")
    mint_link(
        vault,
        str(load_matter(vault).matter_id),
        "att-export-set",
        "audit-log.json",
        NOW,
        signer,
    )

    result.audit_log.write_bytes(result.audit_log.read_bytes() + b"x")
    check = attest.sealed_export_state(vault, "att-export-set")
    assert check.status == "invalidated"
    assert check.reason == "sealed export set changed"
    with pytest.raises(ExportNotReadyError):
        mint_link(
            vault,
            str(load_matter(vault).matter_id),
            "att-export-set",
            "audit-log.json",
            NOW,
            signer,
        )


def test_clean_export_requires_persisted_seal(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-requires-seal")
    attest.attest(vault, "att-requires-seal", "Jane", NOW)

    check = attest.sealed_export_state(vault, "att-requires-seal")

    assert check.status == "invalidated"
    assert check.reason == "clean export has no export seal"


def test_review_integrity_status_reports_exact_records_without_writes(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-status")
    record = attest.attest(vault, "att-status", "Jane", NOW)
    attestation_path = vault / "runs" / "att-status" / "attestations.jsonl"
    before = attestation_path.read_bytes()

    pre_export = attest.review_integrity_status(vault, "att-status")
    assert pre_export.attestation_status == "valid"
    assert pre_export.export_seal_status == "invalidated"
    assert pre_export.latest_attestation == record
    assert pre_export.latest_export_seal is None
    assert attestation_path.read_bytes() == before

    export_run(vault, "att-status", NOW)
    post_export = attest.review_integrity_status(vault, "att-status")
    assert post_export.attestation_status == "valid"
    assert post_export.export_seal_status == "valid"
    assert post_export.latest_export_seal == attest.latest_export_seal(vault, "att-status")


def test_reexport_preserves_attested_master_and_replaces_current_seal(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-reexport")
    attest.attest(vault, "att-reexport", "Jane", NOW)
    first = export_run(vault, "att-reexport", NOW)
    reviewed_master = first.master.read_bytes()

    second = export_run(vault, "att-reexport", LATER)
    seal = attest.latest_export_seal(vault, "att-reexport")

    assert second.is_draft is False
    assert second.master.read_bytes() == reviewed_master
    assert seal is not None
    assert seal.sealed_at == NOW
    assert attest.sealed_export_state(vault, "att-reexport").status == "valid"

    (vault / "runs" / "att-reexport" / "export-seals.jsonl").unlink()
    missing = attest.sealed_export_state(vault, "att-reexport")
    assert missing.status == "invalidated"
    assert missing.reason == "clean export has no export seal"


def test_relative_vault_path_can_be_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _finished_and_resolved(tmp_path, "att-relative")
    attest.attest(vault, "att-relative", "Jane", NOW)
    monkeypatch.chdir(tmp_path)

    result = export_run(Path(vault.name), "att-relative", NOW)

    assert result.is_draft is False
    assert attest.sealed_export_state(Path(vault.name), "att-relative").status == "valid"


def test_reexport_repairs_torn_export_seal_tail(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "att-torn-seal")
    attest.attest(vault, "att-torn-seal", "Jane", NOW)
    export_run(vault, "att-torn-seal", NOW)
    path = vault / "runs" / "att-torn-seal" / "export-seals.jsonl"
    with path.open("ab") as handle:
        handle.write(b'{"schema_version":"1.0"')

    attest.attest(vault, "att-torn-seal", "Jane", LATER)
    export_run(vault, "att-torn-seal", LATER)

    assert path.read_bytes().endswith(b"\n")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    assert attest.sealed_export_state(vault, "att-torn-seal").status == "valid"


def test_residue_failure_removes_clean_outputs_without_sealing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _finished_and_resolved(tmp_path, "att-residue-fail")
    attest.attest(vault, "att-residue-fail", "Jane", NOW)
    monkeypatch.setattr("mootloop.export.docx_render.pandoc_available", lambda: True)

    def render(source: Path, output: Path, reference: Path, *, draft: bool) -> None:
        del source, reference, draft
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"not-clean")

    monkeypatch.setattr("mootloop.export.docx_render.render_docx", render)
    monkeypatch.setattr(
        "mootloop.export.residue.scan_docx",
        lambda path: GateFail(gate="residue", findings=[]),
    )

    result = export_run(vault, "att-residue-fail", NOW)

    assert result.is_draft is True
    assert result.export_ready is False
    assert any(blocker.startswith("residue:") for blocker in result.blockers)
    assert not list((vault / "deliverables" / "att-residue-fail" / "docx").glob("*.docx"))
    assert attest.latest_export_seal(vault, "att-residue-fail") is None


def test_gate_ledger_folds_decisions_and_attestation(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.INTERROGATORY)
    run_id = "gl-fold"
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    verify_run_citations(vault, run_id, NOW)

    # Open delegable decisions -> export blocked on decisions + attestation.
    ready, blockers = gate_ledger.export_ready(vault, run_id)
    assert ready is False
    assert "decisions" in blockers
    assert "attestation" in blockers

    for decision in DecisionStore(vault, run_id).list_open():
        resolve(
            vault,
            run_id,
            decision.decision_id,
            "approve",
            decision.proposal.recommended,
            "",
            "Atty",
            "human",
            NOW,
        )
    # Decisions clear; attestation still pending.
    ready, blockers = gate_ledger.export_ready(vault, run_id)
    assert "decisions" not in blockers
    assert blockers == ["attestation"]

    attest.attest(vault, run_id, "Jane", NOW)
    assert gate_ledger.export_ready(vault, run_id) == (True, [])


def test_gate_ledger_json_written(tmp_path: Path) -> None:
    vault = _finished_and_resolved(tmp_path, "gl-write")
    path = gate_ledger.write_ledger(vault, "gl-write")
    assert path.is_file()
    assert path.name == "gate-ledger.json"
