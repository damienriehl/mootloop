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


def _grounded_cited_draft(spec: TurnSpec, prompt: str) -> dict[str, Any]:
    """A grounded draft carrying the planted citation in its answer text, so the demo
    shows the citation lifecycle end-to-end. Mirrors the default draft's fact
    grounding (fabrication gate passes) and keeps one objection (panel has work)."""
    fact_ids = list(spec.prompt_context.get("fact_ids", []))
    is_rfa = str(spec.request_id or "").upper().startswith("RFA")
    return {
        "response_text": (
            f"Defendant responds to {spec.request_id or 'the request'} as stated in the "
            f"record. See {DEMO_CITATION}."
        ),
        "objections": [{"basis": "relevance", "text": "Overbroad as to time and scope."}],
        "candidate_citations": [],
        "fact_ids_used": fact_ids[:1] if fact_ids else [],
        "attorney_gate_items": [] if fact_ids else ["verify factual basis"],
        "rfa_disposition": "deny" if is_rfa else None,
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
        # Bolster AND restructure carry the citation so it survives into the
        # operative draft of every request, restructured or not.
        ("associate", "bolster"): _grounded_cited_draft,
        ("associate", "restructure"): _grounded_cited_draft,
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
    gate_ledger.write_ledger(vault, run_id)  # refresh after export/attest interplay
    return vault
