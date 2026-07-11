"""Bake the public demo vault: the full pipeline on the SYNTHETIC matter only.

`build_demo_vault` drives every stage of the agentic arc — ingest, facts, served-set
parsing, the six-persona loop (with a scripted judge panel that triggers the
restructure pass on a subset of requests), attorney-gate decisions, citation
verification, attestation, and export — entirely through the `FakeLLMProvider`.
Zero LLM calls, zero network, deterministic (fixed timestamps + run id).

This module is the demo tier's ONLY writer. The read-only API (`mootloop.web.app`)
never imports it (invariant-tested).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mootloop import attest as attest_service
from mootloop import gate_ledger, panels
from mootloop.citations.verify import curated_path
from mootloop.decisions import DecisionStore, resolve
from mootloop.discovery_parser import parse_discovery_document, save_requests
from mootloop.errors import MootloopError
from mootloop.export.service import ExportResult, export_run
from mootloop.facts import add_facts_from_file
from mootloop.ingest import content_doc_id, ingest_folder
from mootloop.llm import FakeLLMProvider, ScriptEntry, ScriptKey
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestType
from mootloop.models.run import TurnSpec
from mootloop.orchestrator import run_with_provider, start_run, verify_run_citations
from mootloop.resources import REPO_ROOT
from mootloop.vault import init_vault

FIXTURE_DIR = REPO_ROOT / "fixtures" / "synthetic-matter"

# Deterministic bake inputs: one fixed timestamp, one fixed run id.
DEMO_NOW = "2026-07-11T00:00:00+00:00"
DEMO_RUN_ID = "demo-discovery-responses"
DEMO_ATTORNEY = "Demo Attorney"

# The planted authority the demo's bolster/restructure drafts cite; pre-curated so
# verification passes with zero network (the free stack routes it to research
# otherwise — federal statutes are not verifiable offline).
DEMO_CITATION = "42 U.S.C. § 1983"

# Requests whose judge panel rules the objection weak → the restructure pass fires.
RESTRUCTURE_REQUEST_IDS = frozenset({"ROG-1", "RFP-2", "RFA-3"})

_SERVED_SETS: tuple[tuple[str, RequestType], ...] = (
    ("rogs-set1.txt", RequestType.INTERROGATORY),
    ("rfps-set1.txt", RequestType.RFP),
    ("rfas-set1.txt", RequestType.RFA),
)

_MAX_DRIVE_PASSES = 10


def _demo_draft(spec: TurnSpec, prompt: str) -> dict[str, Any]:
    """A grounded, completeness-PASSING draft for one request, carrying the planted
    citation in its answer text so the demo shows the citation lifecycle end-to-end.

    The answer text is shaped per request family so every deterministic *presence*
    criterion of the LOCKED rubric (``gates/completeness.py``) is satisfied:

    - ROG (MN Rule 33): restates the interrogatory before answering
      (``mn-rog-restatement``).
    - RFP (Heller / Rule 34(b)): states whether responsive materials are withheld
      (``rfp-withheld-statement``).
    - RFA (Rule 36): takes a recognized disposition — here ``deny`` — with the
      disposition word in the answer text (``rfa-disposition``); no lack-of-knowledge
      answer, so no reasonable-inquiry recital is required.

    Common to every family: a request-specific objection (basis + a >12-char
    specificity string) with no boilerplate general objection and no
    "subject to and without waiving" hedge. Fact grounding mirrors the default draft
    so the fabrication gate passes, and one objection is kept so the judge panel has
    work to rule on.
    """
    fact_ids = list(spec.prompt_context.get("fact_ids", []))
    request_id = str(spec.request_id or "the request")
    family = request_id.split("-")[0].upper()
    number = request_id.split("-")[-1] if "-" in request_id else ""
    objection = {
        "basis": "relevance",
        "text": "Overbroad as to time and scope; not relevant to any claim or defense.",
    }

    if family == "RFA":
        response_text = (
            f"Denied. Defendant denies the matter asserted in {request_id}; the denial "
            f"fairly meets the substance of the request. See {DEMO_CITATION}."
        )
        rfa_disposition: str | None = "deny"
    elif family == "RFP":
        response_text = (
            f"Subject to the objection stated below, Defendant will produce the "
            f"responsive, non-privileged documents in its possession, custody, or "
            f"control. Responsive documents are withheld only to the extent they fall "
            f"outside the relevant time period; nothing else is being withheld. "
            f"See {DEMO_CITATION}."
        )
        rfa_disposition = None
    else:  # ROG / interrogatory — restate before answering (MN Rule 33)
        response_text = (
            f"Interrogatory No. {number}: restated in full above. Subject to the "
            f"objection stated below, Defendant answers by identifying the individuals "
            f"and events reflected in the record. See {DEMO_CITATION}."
        )
        rfa_disposition = None

    return {
        "response_text": response_text,
        "objections": [objection],
        "candidate_citations": [],
        "fact_ids_used": fact_ids[:1] if fact_ids else [],
        "attorney_gate_items": [] if fact_ids else ["verify factual basis"],
        "rfa_disposition": rfa_disposition,
        "self_assessment": "Grounded in the cited fact and the record.",
    }


def _split_survival_judge(spec: TurnSpec, prompt: str) -> dict[str, Any]:
    """Judge panel script: objections on the restructure subset are ruled weak (the
    costed restructure pass fires); everything else survives."""
    weak = str(spec.request_id or "") in RESTRUCTURE_REQUEST_IDS
    reasoning = (
        "An overbroad relevance objection would not survive a motion to compel."
        if weak
        else "The relevance objection is properly grounded on this request."
    )
    return {
        "rulings": [
            {
                "objection_basis": "relevance",
                "would_objection_survive": not weak,
                "reasoning": reasoning,
                "persuasion_notes": "Weak as drafted." if weak else "Defensible objection.",
            }
        ],
        "self_assessment": "Ruled on each objection.",
    }


def _demo_script() -> dict[ScriptKey, ScriptEntry]:
    return {
        ("judge", "judge_panel"): _split_survival_judge,
        # Every Associate draft turn (initial draft, any partner-loop redraft, bolster,
        # and restructure) uses the completeness-PASSING draft, so the gate ledger shows
        # completeness=pass for every request. Each draft carries the citation so it
        # survives into the operative draft of every request, restructured or not.
        ("associate", "associate_draft"): _demo_draft,
        ("associate", "partner_loop"): _demo_draft,
        ("associate", "bolster"): _demo_draft,
        ("associate", "restructure"): _demo_draft,
    }


def _resolve_open_decisions(vault: Path, run_id: str) -> int:
    """Approve every open decision at its recommendation (decided by the demo
    attorney, source human). Returns how many were resolved."""
    open_decisions = DecisionStore(vault, run_id).list_open()
    for decision in open_decisions:
        resolve(
            vault,
            run_id,
            decision.decision_id,
            "approve",
            decision.proposal.recommended,
            "Demo resolution: approved at the recommendation.",
            DEMO_ATTORNEY,
            "human",
            DEMO_NOW,
        )
    return len(open_decisions)


def build_demo_vault(dest_dir: Path | str) -> Path:
    """Build the complete, attested, export-ready demo vault at ``dest_dir``.

    Deterministic and offline: fixtures in, FakeLLMProvider turns, fixed
    timestamps, pre-curated authority. Raises `MootloopError` if the run does not
    reach ``finished``.
    """
    dest = Path(dest_dir)
    matter = MatterConfig.model_validate(
        yaml.safe_load((FIXTURE_DIR / "matter.yaml").read_text(encoding="utf-8"))
    )
    vault = init_vault(dest, matter, registry_path=dest / "canaries.json")

    # Corpus + facts + served sets — the same arc a real matter follows.
    ingest_folder(
        vault, FIXTURE_DIR / "source-docs", now=DEMO_NOW, tags_file=FIXTURE_DIR / "tags.yaml"
    )
    add_facts_from_file(vault, FIXTURE_DIR / "facts.json")
    for filename, request_type in _SERVED_SETS:
        data = (FIXTURE_DIR / "served" / filename).read_bytes()
        report = parse_discovery_document(
            data.decode("utf-8"), request_type, content_doc_id(data)
        )
        save_requests(vault, report.request_set)

    # Drive the run to completion, resolving hard-human gates as they block it.
    run_id = start_run(vault, "discovery-responses", DEMO_NOW, run_id=DEMO_RUN_ID)
    provider = FakeLLMProvider(script=_demo_script())
    for _ in range(_MAX_DRIVE_PASSES):
        state = run_with_provider(vault, run_id, provider, DEMO_NOW)
        if state.status == "needs_decisions":
            _resolve_open_decisions(vault, run_id)
            continue
        break
    else:  # pragma: no cover - defensive; the scripted run finishes in a few passes
        raise MootloopError(f"demo run {run_id!r} did not settle in {_MAX_DRIVE_PASSES} passes")
    if state.status != "finished":
        raise MootloopError(f"demo run {run_id!r} ended {state.status!r}, expected finished")

    # Citation lane: pre-curate the planted authority, then verify (no network).
    curated = curated_path(vault, DEMO_CITATION)
    curated.parent.mkdir(parents=True, exist_ok=True)
    curated.write_text(f"# {DEMO_CITATION} (curated authority)\n", encoding="utf-8")
    verify_run_citations(vault, run_id, DEMO_NOW)

    # Any remaining policy-delegable gates, then the attorney attests.
    _resolve_open_decisions(vault, run_id)
    attest_service.attest(vault, run_id, DEMO_ATTORNEY, DEMO_NOW)

    # Derived views the API serves directly, then the export itself.
    panels.build_panel_report(vault, run_id)
    gate_ledger.write_ledger(vault, run_id)
    result: ExportResult = export_run(vault, run_id, DEMO_NOW)
    if result.is_draft or not result.export_ready:
        raise MootloopError(
            f"demo export not clean: draft={result.is_draft} blockers={result.blockers}"
        )
    if result.docx_skipped_reason is None and (not result.docx or not result.residue_clean):
        raise MootloopError(
            f"demo DOCX export failed residue: blockers={result.blockers}"
        )
    gate_ledger.write_ledger(vault, run_id)  # refresh after export/attest interplay
    return vault
