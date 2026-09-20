"""Request identity and attorney intent must survive court-document assembly."""

import zipfile
from pathlib import Path

import pytest

from mootloop import attest, gate_ledger
from mootloop.context import _load_request_sets, _materialize, load_run_context
from mootloop.decisions import DecisionStore, resolve
from mootloop.discovery_parser import save_requests
from mootloop.errors import ExportError, OrchestratorError
from mootloop.export import service
from mootloop.export.master import _response_block, build_court_master, build_set_masters
from mootloop.llm import FakeLLMProvider
from mootloop.models.common import DecisionId, DocId, RequestId
from mootloop.models.decisions import (
    Decision,
    DecisionKind,
    DecisionProposal,
    DecisionResolution,
)
from mootloop.models.requests import RequestItem, RequestSet, RequestType
from mootloop.models.run import DraftOutput, Objection
from mootloop.orchestrator import run_with_provider, start_run
from mootloop.output_review import decision_revision_requirements
from tests.conftest import make_matter
from tests.unit.test_attest import _finished_and_resolved
from tests.unit.test_orchestrator_planning import NOW, _build_single_request_vault


def _set(number: int, request_number: int = 1) -> RequestSet:
    return RequestSet(
        request_type=RequestType.INTERROGATORY,
        set_number=number,
        title=f"Interrogatories set {number}",
        items=[
            RequestItem(
                request_id=RequestId(f"ROG-{request_number}"),
                set_number=number,
                number=request_number,
                text=f"Identify the witness for event {number}.",
                source_doc=DocId("doc-synthetic"),
            )
        ],
    )


def test_duplicate_request_ids_across_served_sets_rejected_before_launch(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    save_requests(vault, _set(2))
    with pytest.raises(OrchestratorError, match="ambiguous request identity"):
        start_run(vault, "discovery-responses", NOW, run_id="ambiguous")
    assert not (vault / "runs/ambiguous/context/manifest.json").exists()


def test_disjoint_served_sets_preserve_opponent_numbering(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    save_requests(vault, _set(2, 7))
    sets, _ = _load_request_sets(vault)
    assert [str(item.request_id) for group in sets for item in group.items] == ["ROG-1", "ROG-7"]


def test_ambiguous_historical_manifest_fails_materialization(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    start_run(vault, "discovery-responses", NOW, run_id="historical")
    context = load_run_context(vault, "historical")
    manifest = context.manifest.model_copy(update={"request_sets": [_set(1), _set(2)]})
    with pytest.raises(OrchestratorError, match="ambiguous request identity"):
        _materialize(manifest)


def test_duplicate_export_set_labels_rejected_even_with_disjoint_numbers(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    (vault / "requests/duplicate.json").write_text(_set(1, 7).model_dump_json())
    with pytest.raises(OrchestratorError, match="ambiguous request identity"):
        _load_request_sets(vault)


def _render(draft: DraftOutput, kind: RequestType, chosen: str | None = None) -> str:
    item = RequestItem(
        request_id=RequestId("RFA-1" if kind is RequestType.RFA else "RFP-1"),
        number=1,
        text="Admit delivery and acceptance." if kind is RequestType.RFA else "Produce documents.",
        source_doc=DocId("doc-synthetic"),
    )
    return "\n".join(_response_block(make_matter(), item, draft, kind, chosen))


def test_qualified_admission_retains_actual_qualification() -> None:
    draft = DraftOutput(
        response_text="Admit delivery on May 1; deny signed acceptance.",
        rfa_disposition="qualify",
        self_assessment="Reviewed synthetic response.",
    )
    rendered = _render(draft, RequestType.RFA)
    assert draft.response_text in rendered
    assert "Admitted in part and denied in part." in rendered


def test_changed_rfa_choice_does_not_relabel_unrevised_narrative() -> None:
    draft = DraftOutput(
        response_text="Admit delivery on May 1; deny signed acceptance.",
        rfa_disposition="qualify",
        self_assessment="Reviewed synthetic response.",
    )
    rendered = _render(draft, RequestType.RFA, "admit")
    assert draft.response_text in rendered
    assert "**RESPONSE:** Admitted." not in rendered


def test_missing_rfa_disposition_never_invents_denial() -> None:
    draft = DraftOutput(response_text="Admit delivery.", self_assessment="Needs review.")
    rendered = _render(draft, RequestType.RFA)
    assert draft.response_text in rendered
    assert "Denied." not in rendered


@pytest.mark.parametrize("has_objection", [False, True])
def test_rfp_export_does_not_infer_withholding_from_objections(has_objection: bool) -> None:
    narrative = "All responsive documents will be produced; none are withheld."
    draft = DraftOutput(
        response_text=narrative,
        objections=[Objection(basis="relevance", text="Overbroad as to time.")]
        if has_objection
        else [],
        self_assessment="Reviewed synthetic response.",
    )
    rendered = _render(draft, RequestType.RFP)
    assert narrative in rendered
    assert "Responsive materials are being withheld" not in rendered
    assert "No responsive materials are being withheld on the basis" not in rendered


def test_waiving_drafted_objections_keeps_export_blocked_and_review_copy_available(
    tmp_path: Path,
) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="waiver")
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    decisions = DecisionStore(vault, run_id).list_open()
    posture = next(decision for decision in decisions if decision.kind == "objection_posture")
    resolve(vault, run_id, posture.decision_id, "modify", "waive", "", "Attorney", "human", NOW)

    ledger = gate_ledger.build_ledger(vault, run_id)
    assert ledger.overall["decisions"] == "fail"
    assert f"decision_revision:{posture.decision_id}" in ledger.blockers
    for path in [
        build_court_master(vault, run_id, NOW),
        *[path for _, path in build_set_masters(vault, run_id, NOW)],
    ]:
        rendered = path.read_text()
        assert "DRAFT — revised response required" in rendered
        assert "waive" in rendered
        assert "OBJECTION" in rendered  # The original remains visible for review.


@pytest.mark.parametrize(
    ("kind", "chosen", "blocked"),
    [
        (DecisionKind.OBJECTION_POSTURE, "assert", False),
        (DecisionKind.OBJECTION_POSTURE, "waive", True),
        (DecisionKind.OBJECTION_POSTURE, "narrow", True),
        (DecisionKind.PRIVILEGE_CALL, "withhold", True),
        (DecisionKind.PRIVILEGE_CALL, "produce", True),
        (DecisionKind.PRIVILEGE_CALL, "log_only", True),
        (DecisionKind.UNSUPPORTED_ASSERTION, "strike", True),
        (DecisionKind.UNSUPPORTED_ASSERTION, "obtain_support", True),
        (DecisionKind.UNSUPPORTED_ASSERTION, "attest_anyway", False),
        (DecisionKind.RFA_DISPOSITION, "qualify", False),
        (DecisionKind.RFA_DISPOSITION, "admit", True),
    ],
)
def test_resolution_cannot_substitute_for_a_required_rewrite(
    kind: DecisionKind,
    chosen: str,
    blocked: bool,
) -> None:
    decision = Decision(
        decision_id=DecisionId("dec-test-0001"),
        run_id="test",
        request_id=RequestId("RFA-1"),
        kind=kind,
        status="modified",
        proposal=DecisionProposal(
            summary="Synthetic choice", reasoning="Review", recommended=chosen
        ),
        resolution=DecisionResolution(
            action="modify",
            chosen_key=chosen,
            decided_by="Attorney",
            source="human",
            decided_at=NOW,
        ),
    )
    draft = DraftOutput(
        response_text="Admit delivery; deny signed acceptance.",
        objections=[Objection(basis="privilege", text="Protected communication.")],
        rfa_disposition="qualify",
        self_assessment="Synthetic review.",
    )
    requirements = decision_revision_requirements([decision], {"RFA-1": draft})
    assert bool(requirements) is blocked
    if blocked:
        assert "new run" in requirements[str(decision.decision_id)]


def test_waiver_without_objections_is_already_compatible() -> None:
    decision = Decision(
        decision_id=DecisionId("dec-test-0001"),
        run_id="test",
        kind=DecisionKind.OBJECTION_POSTURE,
        status="approved",
        proposal=DecisionProposal(
            summary="Objection posture for ROG requests",
            reasoning="Review",
            recommended="waive",
        ),
        resolution=DecisionResolution(
            action="approve",
            chosen_key="waive",
            decided_by="Attorney",
            source="human",
            decided_at=NOW,
        ),
    )
    draft = DraftOutput(
        response_text="The witness was present.", self_assessment="Synthetic review."
    )
    unrelated = draft.model_copy(
        update={"objections": [Objection(basis="privilege", text="Private")]}
    )
    assert not decision_revision_requirements([decision], {"ROG-1": draft, "RFP-1": unrelated})
    assert decision_revision_requirements([decision], {"ROG-1": None})


def test_existing_seal_cannot_bypass_new_decision_revision_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "load_run_context", lambda *_: object())
    monkeypatch.setattr(service, "load_run_corpus", lambda *_: None)
    monkeypatch.setattr(attest, "sealed_export_state", lambda *_: attest.AttestationCheck("valid"))
    monkeypatch.setattr(
        gate_ledger, "export_ready", lambda *_: (False, ["decision_revision:dec-old"])
    )
    existing = service.ExportResult(
        run_id="sealed",
        master=tmp_path / "master.md",
        verification=None,
        privilege_log=tmp_path / "privilege.md",
        memo=tmp_path / "memo.md",
        audit_log=tmp_path / "audit.json",
        is_draft=False,
        export_ready=True,
    )
    monkeypatch.setattr(service, "_existing_sealed_result", lambda *_: existing)
    with pytest.raises(ExportError, match="decision_revision:dec-old"):
        service._export_run_locked(
            tmp_path, "sealed", NOW, force_draft=False, reference_doc="unused"
        )


@pytest.mark.skipif(not service.docx_render.pandoc_available(), reason="requires pandoc")
def test_blocked_sealed_generation_allows_watermarked_review_copy_without_changing_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "sealed-review"
    vault = _finished_and_resolved(tmp_path, run_id)
    attest.attest(vault, run_id, "Jane", NOW)
    clean = service.export_run(vault, run_id, NOW)
    assert not clean.is_draft
    assert clean.docx
    seal = attest.latest_export_seal(vault, run_id)
    assert seal is not None
    assert attest.sealed_export_state(vault, run_id).status == "valid"
    preserved_paths = [
        clean.master,
        clean.privilege_log,
        clean.memo,
        clean.audit_log,
        *clean.set_masters,
        *clean.docx,
        vault / "runs" / run_id / "attestations.jsonl",
        vault / "runs" / run_id / "export-seals.jsonl",
    ]
    if clean.verification is not None:
        preserved_paths.append(clean.verification)
    before = {path: path.read_bytes() for path in preserved_paths}
    blockers = ["decisions", "decision_revision:dec-old"]
    monkeypatch.setattr(gate_ledger, "export_ready", lambda *_: (False, blockers))

    draft = service.export_run(vault, run_id, NOW, force_draft=True)

    assert draft.is_draft
    assert draft.export_ready is False
    assert draft.blockers == blockers
    assert draft.attestation_state == "valid"
    assert draft.docx_skipped_reason is None
    assert len(draft.docx) == len(clean.docx)
    for path in draft.docx:
        assert path.name.endswith(".DRAFT.docx")
        assert path not in clean.docx
        with zipfile.ZipFile(path) as archive:
            assert any(
                b"DRAFT" in archive.read(name)
                for name in archive.namelist()
                if name.startswith("word/header") and name.endswith(".xml")
            )
    assert {path: path.read_bytes() for path in preserved_paths} == before
    assert attest.latest_export_seal(vault, run_id) == seal
    assert attest.sealed_export_state(vault, run_id).status == "valid"
