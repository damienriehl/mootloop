"""Phase 6-7 end-to-end: the judge panel drives a restructure pass, and the export
builds the D7 deliverables + (when pandoc is present) residue-clean DOCX. Everything
runs through the FakeLLMProvider — no live calls, no network (the planted citation
routes to the research queue)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from mootloop import attest, gate_ledger
from mootloop.decisions import DecisionStore, resolve
from mootloop.discovery_parser import parse_discovery_document, save_requests
from mootloop.export.master import MN_VERIFICATION_DECLARATION
from mootloop.export.service import export_run
from mootloop.facts import add_facts_from_file
from mootloop.ingest import ingest_folder
from mootloop.llm import FakeLLMProvider
from mootloop.models.common import DocId
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestType
from mootloop.orchestrator import run_with_provider, start_run, verify_run_citations
from mootloop.panels import build_panel_report
from mootloop.vault import init_vault

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "fixtures" / "synthetic-matter"
NOW = "2026-07-11T00:00:00+00:00"

_HAS_PANDOC = shutil.which("pandoc") is not None

_ALL_SETS = [
    ("rogs-set1.txt", RequestType.INTERROGATORY),
    ("rfps-set1.txt", RequestType.RFP),
    ("rfas-set1.txt", RequestType.RFA),
]

def _cited_bolster(spec: Any, prompt: str) -> dict[str, Any]:
    """A grounded operative (bolster) draft that also carries a citation, so the audit
    log has a citation to reconcile against the ledger. Mirrors the default draft's
    fact grounding so the fabrication gate still passes."""
    fact_ids = list(spec.prompt_context.get("fact_ids", []))
    return {
        # Citation lives in the answer text (not candidate_citations, which would leave
        # the fabrication gate pending); it is still extracted for the audit log.
        "response_text": "Defendant responds to the request as stated. See 42 U.S.C. § 1983.",
        "objections": [{"basis": "relevance", "text": "Overbroad as to time and scope."}],
        "candidate_citations": [],
        "fact_ids_used": fact_ids[:1],
        "attorney_gate_items": [] if fact_ids else ["verify factual basis"],
        "rfa_disposition": None,
        "self_assessment": "Grounded in the record.",
    }

# A judge who rules every objection would NOT survive — drives a restructure pass.
_LOW_SURVIVAL_JUDGE = {
    "rulings": [
        {
            "objection_basis": "relevance",
            "would_objection_survive": False,
            "reasoning": "An overbroad relevance objection would not survive a motion to compel.",
            "persuasion_notes": "weak",
        }
    ],
    "self_assessment": "Ruled on each objection.",
}


def _build(
    tmp_path: Path, sets: list[tuple[str, RequestType]], *, signed: bool = True
) -> Path:
    raw = yaml.safe_load((FIXTURE / "matter.yaml").read_text(encoding="utf-8"))
    if not signed:
        raw.pop("attorney", None)
    matter = MatterConfig.model_validate(raw)
    vault = tmp_path / "vault"
    init_vault(vault, matter, registry_path=tmp_path / "canaries.json")
    ingest_folder(vault, FIXTURE / "source-docs", now=NOW, tags_file=FIXTURE / "tags.yaml")
    add_facts_from_file(vault, FIXTURE / "facts.json")
    for filename, request_type in sets:
        data = (FIXTURE / "served" / filename).read_bytes()
        report = parse_discovery_document(
            data.decode("utf-8"), request_type, DocId("doc-servedservedserv")
        )
        save_requests(vault, report.request_set)
    return vault


def _finish_run(vault: Path, run_id: str, provider: FakeLLMProvider) -> None:
    """Drive to completion, resolving the RFA hard-human gate that holds the finish."""
    for _ in range(10):
        state = run_with_provider(vault, run_id, provider, NOW)
        if state.status == "needs_decisions":
            for decision in DecisionStore(vault, run_id).list_open():
                resolve(
                    vault,
                    run_id,
                    decision.decision_id,
                    "approve",
                    decision.proposal.recommended,
                    "",
                    "Test Attorney",
                    "human",
                    NOW,
                )
            continue
        break


def test_low_survival_panel_drives_restructure(tmp_path: Path) -> None:
    vault = _build(tmp_path, [("rogs-set1.txt", RequestType.INTERROGATORY)])
    run_id = start_run(vault, "discovery-responses", NOW, run_id="p67-restruct")
    provider = FakeLLMProvider(script={("judge", "judge_panel"): _LOW_SURVIVAL_JUDGE})
    _finish_run(vault, run_id, provider)

    from mootloop.journal import load_state

    state = load_state(vault, run_id)
    restructure_turns = [
        r for r in state.completed_turns.values() if r.spec.stage == "restructure"
    ]
    assert restructure_turns, "a low-survival panel must trigger a restructure pass"

    report = build_panel_report(vault, run_id)
    assert report.results
    assert report.weak(0.5), "every objection survived a minority -> all weak"
    # The persisted derived view exists.
    assert (vault / "runs" / run_id / "scores" / "panels" / "report.json").is_file()


def test_high_survival_panel_skips_restructure(tmp_path: Path) -> None:
    vault = _build(tmp_path, [("rogs-set1.txt", RequestType.INTERROGATORY)])
    run_id = start_run(vault, "discovery-responses", NOW, run_id="p67-nostruct")
    _finish_run(vault, run_id, FakeLLMProvider())  # default judge rules objections survive

    from mootloop.journal import load_state

    state = load_state(vault, run_id)
    assert not [r for r in state.completed_turns.values() if r.spec.stage == "restructure"]


def test_full_export_flow(tmp_path: Path) -> None:
    vault = _build(tmp_path, _ALL_SETS)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="p67-export")
    provider = FakeLLMProvider(script={("associate", "bolster"): _cited_bolster})
    _finish_run(vault, run_id, provider)
    # Pre-curate the planted authority (tier-1) so it verifies without any network.
    from mootloop.citations.verify import curated_path

    curated = curated_path(vault, "42 U.S.C. § 1983")
    curated.parent.mkdir(parents=True, exist_ok=True)
    curated.write_text("# 42 U.S.C. § 1983 (curated authority)\n", encoding="utf-8")
    verify_run_citations(vault, run_id, NOW)
    # Resolve any remaining policy-delegable gates, then attest.
    for decision in DecisionStore(vault, run_id).list_open():
        resolve(
            vault, run_id, decision.decision_id, "approve", decision.proposal.recommended,
            "", "Test Attorney", "human", NOW,
        )
    attest.attest(vault, run_id, "Jane Attorney", NOW)
    ready, _ = gate_ledger.export_ready(vault, run_id)
    assert ready is True

    result = export_run(vault, run_id, NOW)
    assert result.is_draft is False
    assert result.attestation_state == "valid"

    # master.md: restated interrogatory + response anchors + NO hedge anywhere.
    master_text = result.master.read_text(encoding="utf-8")
    assert "INTERROGATORY NO. 3:" in master_text
    assert "::: {#resp-ROG-3}" in master_text
    assert "OBJECTION (relevance):" in master_text
    assert "subject to and without waiving" not in master_text.lower()
    # RFP withheld statement + RFA disposition rendered.
    assert "withheld on the basis" in master_text.lower()

    # verification.md: MN's EXACT perjury declaration (rog set present).
    assert result.verification is not None
    assert MN_VERIFICATION_DECLARATION in result.verification.read_text(encoding="utf-8")

    # privilege log: a row for the privileged fixture doc (email3.eml).
    priv_text = result.privilege_log.read_text(encoding="utf-8")
    assert priv_text.count("`doc-") >= 1
    assert "26.02(f)" in priv_text

    # strategy memo: survival rates + the standing citator disclosure.
    memo_text = result.memo.read_text(encoding="utf-8")
    assert "survive" in memo_text
    assert "citator" in memo_text.lower()

    # audit log: citations match the ledger statuses (planted § 1983 -> curated/verified).
    audit = json.loads(result.audit_log.read_text(encoding="utf-8"))
    assert audit["attestation"]["state"] == "valid"
    from datetime import datetime

    from mootloop.citations.ledger import VerificationLedger

    ledger = VerificationLedger(vault).folded(now=datetime.fromisoformat(NOW))
    cited = False
    for block in audit["response_blocks"]:
        for citation in block["citations"]:
            cited = True
            record = ledger.get(citation["citation_id"])
            expected = record.status.value if record else "pending"
            assert citation["status"] == expected
    assert cited, "the planted citation must appear in the audit log"


@pytest.mark.skipif(not _HAS_PANDOC, reason="pandoc not installed")
def test_clean_export_produces_residue_clean_docx(tmp_path: Path) -> None:
    vault = _build(tmp_path, [("rogs-set1.txt", RequestType.INTERROGATORY)])
    run_id = start_run(vault, "discovery-responses", NOW, run_id="p67-docx")
    _finish_run(vault, run_id, FakeLLMProvider())
    verify_run_citations(vault, run_id, NOW)
    for decision in DecisionStore(vault, run_id).list_open():
        resolve(
            vault, run_id, decision.decision_id, "approve", decision.proposal.recommended,
            "", "A", "human", NOW,
        )
    attest.attest(vault, run_id, "Jane", NOW)

    result = export_run(vault, run_id, NOW)
    assert result.is_draft is False
    assert result.docx, "a clean, attested run must render DOCX when pandoc is present"
    for path in result.docx:
        assert path.name.endswith(".docx") and not path.name.endswith(".DRAFT.docx")
    assert result.residue_clean


@pytest.mark.skipif(not _HAS_PANDOC, reason="pandoc not installed")
def test_unattested_export_is_draft_watermarked(tmp_path: Path) -> None:
    vault = _build(tmp_path, [("rogs-set1.txt", RequestType.INTERROGATORY)])
    run_id = start_run(vault, "discovery-responses", NOW, run_id="p67-draft")
    _finish_run(vault, run_id, FakeLLMProvider())  # no attestation

    result = export_run(vault, run_id, NOW)
    assert result.is_draft is True
    assert result.attestation_state == "missing"
    for path in result.docx:
        assert path.name.endswith(".DRAFT.docx")


def test_post_attestation_edit_refuses_clean_export(tmp_path: Path) -> None:
    vault = _build(tmp_path, [("rogs-set1.txt", RequestType.INTERROGATORY)])
    run_id = start_run(vault, "discovery-responses", NOW, run_id="p67-edit")
    _finish_run(vault, run_id, FakeLLMProvider())
    verify_run_citations(vault, run_id, NOW)
    for decision in DecisionStore(vault, run_id).list_open():
        resolve(
            vault, run_id, decision.decision_id, "approve", decision.proposal.recommended,
            "", "A", "human", NOW,
        )
    attest.attest(vault, run_id, "Jane", NOW)
    assert export_run(vault, run_id, NOW).is_draft is False

    # A substantive edit to the attested md-master re-imposes DRAFT.
    master = attest.master_deliverable_path(vault, run_id)
    assert master is not None
    master.write_text(master.read_text() + "\nInserted after attestation.\n", encoding="utf-8")
    attest.check_attestation(vault, run_id, NOW)

    result = export_run(vault, run_id, NOW)
    assert result.is_draft is True
    assert result.attestation_state == "invalidated"
    assert "attestation" in result.blockers


def _attested_run(tmp_path: Path, run_id: str, *, signed: bool = True) -> Path:
    """A single-rog run driven to finished, decisions resolved, citations verified,
    and attested — i.e. one step away from a clean export."""
    vault = _build(tmp_path, [("rogs-set1.txt", RequestType.INTERROGATORY)], signed=signed)
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    _finish_run(vault, run_id, FakeLLMProvider())
    verify_run_citations(vault, run_id, NOW)
    for decision in DecisionStore(vault, run_id).list_open():
        resolve(
            vault, run_id, decision.decision_id, "approve", decision.proposal.recommended,
            "", "A", "human", NOW,
        )
    attest.attest(vault, run_id, "Jane", NOW)
    return vault


def test_matter_edit_after_attestation_refuses_clean_export(tmp_path: Path) -> None:
    """A live matter edit invalidates approval but cannot rebind the run's export."""
    run_id = "p67-matter-edit"
    vault = _attested_run(tmp_path, run_id)
    assert export_run(vault, run_id, NOW).is_draft is False

    matter_file = vault / "matter.yaml"
    raw = yaml.safe_load(matter_file.read_text(encoding="utf-8"))
    raw["caption"]["case_number"] = "66-CV-26-9999"
    matter_file.write_text(yaml.safe_dump(raw), encoding="utf-8")

    result = export_run(vault, run_id, NOW)
    assert result.is_draft is True
    assert result.attestation_state == "invalidated"
    assert "attestation" in result.blockers
    # The run keeps exporting its launch snapshot; the edit requires a new/rebased run.
    assert "66-CV-26-9999" not in result.master.read_text(encoding="utf-8")


def test_signing_attorney_swap_after_attestation_refuses_clean_export(tmp_path: Path) -> None:
    run_id = "p67-atty-swap"
    vault = _attested_run(tmp_path, run_id)
    assert export_run(vault, run_id, NOW).is_draft is False

    matter_file = vault / "matter.yaml"
    raw = yaml.safe_load(matter_file.read_text(encoding="utf-8"))
    raw["attorney"]["name"] = "Someone Else"
    raw["attorney"]["bar_number"] = "0000001"
    matter_file.write_text(yaml.safe_dump(raw), encoding="utf-8")

    result = export_run(vault, run_id, NOW)
    assert result.is_draft is True, "a signature line may not change under a valid attestation"
    assert result.attestation_state == "invalidated"


def test_matter_without_signing_attorney_never_exports_clean(tmp_path: Path) -> None:
    """`master.py` renders `[ATTORNEY NAME]` when the matter has no attorney block.
    That is scaffolding, not a signature — the export must water the copy even though
    the gate ledger is green and the attestation valid."""
    run_id = "p67-unsigned"
    vault = _attested_run(tmp_path, run_id, signed=False)

    assert gate_ledger.export_ready(vault, run_id)[0] is True
    result = export_run(vault, run_id, NOW)
    assert result.attestation_state == "valid"
    assert result.export_ready is True
    assert result.is_draft is True
    assert "signature_block" in result.blockers
    assert "[ATTORNEY NAME]" in result.master.read_text(encoding="utf-8")


def test_draft_export_retires_a_stale_clean_docx(tmp_path: Path) -> None:
    """A clean DOCX is only justified by the state at the moment it was rendered. Once
    the run drifts and the export waters the copy, the stale clean file must not sit in
    ``deliverables/`` waiting for the gate ledger to go green again."""
    run_id = "p67-stale"
    vault = _build(tmp_path, [("rogs-set1.txt", RequestType.INTERROGATORY)])
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    _finish_run(vault, run_id, FakeLLMProvider())

    # Stand in for a clean DOCX left by an earlier, then-justified export.
    docx_dir = vault / "deliverables" / run_id / "docx"
    docx_dir.mkdir(parents=True, exist_ok=True)
    stale = docx_dir / "rog-set1.docx"
    stale.write_bytes(b"PK\x03\x04 stale clean copy")
    draft_sibling = docx_dir / "rog-set1.DRAFT.docx"
    draft_sibling.write_bytes(b"PK\x03\x04 draft copy")

    result = export_run(vault, run_id, NOW)  # unattested -> DRAFT
    assert result.is_draft is True
    assert not stale.exists(), "a DRAFT export must retire every clean DOCX"
    assert draft_sibling.exists()

    from mootloop.export.link import list_deliverables

    names = {entry.name for entry in list_deliverables(vault, run_id)}
    assert "docx/rog-set1.docx" not in names


def test_force_draft_alone_keeps_a_still_justified_clean_docx(tmp_path: Path) -> None:
    """`--force-draft` is a rendering choice, not a statement that the run drifted.
    While the run still qualifies for a clean export, the flag must not destroy the
    clean copy the attorney already has."""
    run_id = "p67-force"
    vault = _attested_run(tmp_path, run_id)
    docx_dir = vault / "deliverables" / run_id / "docx"
    docx_dir.mkdir(parents=True, exist_ok=True)
    clean = docx_dir / "rog-set1.docx"
    clean.write_bytes(b"PK\x03\x04 clean copy")

    result = export_run(vault, run_id, NOW, force_draft=True)
    assert result.is_draft is True
    assert clean.exists(), "force-draft must not retire a clean copy the state justifies"


# --- audit-log provenance (README: journal + ledger + decisions + attestations) ---


def _self_asserting_bolster(spec: Any, prompt: str) -> dict[str, Any]:
    """A draft that loudly claims its own citation is verified. Nothing it says may
    reach the audit log as a verification status."""
    fact_ids = list(spec.prompt_context.get("fact_ids", []))
    return {
        "response_text": (
            "Defendant responds as stated. See 42 U.S.C. § 1983 (verified; "
            "citation confirmed good law by the drafting attorney)."
        ),
        "objections": [{"basis": "relevance", "text": "Overbroad as to time and scope."}],
        "candidate_citations": [],
        "fact_ids_used": fact_ids[:1],
        "attorney_gate_items": [] if fact_ids else ["verify factual basis"],
        "rfa_disposition": None,
        "self_assessment": "Every citation in this response is verified.",
    }


def test_audit_log_is_derived_never_llm_asserted(tmp_path: Path) -> None:
    from datetime import datetime

    from mootloop.citations.ledger import VerificationLedger
    from mootloop.journal import load_state, read_events
    from mootloop.models.events import GateEvaluated

    run_id = "p67-audit"
    vault = _build(tmp_path, [("rogs-set1.txt", RequestType.INTERROGATORY)])
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    provider = FakeLLMProvider(script={("associate", "bolster"): _self_asserting_bolster})
    _finish_run(vault, run_id, provider)

    result = export_run(vault, run_id, NOW)
    audit = json.loads(result.audit_log.read_text(encoding="utf-8"))

    state = load_state(vault, run_id)
    ledger = VerificationLedger(vault).folded(now=datetime.fromisoformat(NOW))
    rubric_by_request: dict[str, str] = {}
    for event in read_events(vault, run_id):
        if isinstance(event, GateEvaluated) and event.result.gate == "rubric":
            record = state.completed_turns.get(event.turn_id)
            if record is not None and record.spec.request_id is not None:
                rubric_by_request[str(record.spec.request_id)] = event.result.status

    assert audit["response_blocks"], "the run drafted at least one response"
    saw_citation = False
    for block in audit["response_blocks"]:
        rid = block["request_id"]

        # Attribution comes from the journal's TurnSpecs, not from any model output.
        for turn in block["contributing_turns"]:
            record = state.completed_turns[turn["turn_id"]]
            assert turn["persona"] == record.spec.persona.value
            assert turn["stage"] == record.spec.stage
            assert turn["model"] == (record.spec.model or "seat")

        # Verification status is read from the immutable ledger — the draft's own
        # "verified" prose must not promote it.
        for citation in block["citations"]:
            saw_citation = True
            record_ = ledger.get(citation["citation_id"])
            assert citation["status"] == (record_.status.value if record_ else "pending")
            assert citation["status"] != "verified", "nothing verified this citation"
            assert citation["source"] == (record_.source if record_ else None)

        # Rubric scores are the journal's gate results.
        assert block["rubric"]["status"] == rubric_by_request.get(rid, "pending")

        # Attestation state is the attest service's answer, not a persona's.
        assert block["attestation"] == attest.attestation_state(vault, run_id).status

    assert saw_citation, "the planted citation must appear in the audit log"
    assert audit["attestation"]["state"] == "missing"  # never attested in this run
    assert audit["rubric_version"] == state.rubric_version
    assert audit["task"] == state.task
