"""Phase 4 end-to-end: fabrication gate on every draft, citation verification as an
explicit step, and the export citation gate blocking anything unverified.

Everything runs through the FakeLLMProvider + ``httpx.MockTransport`` — no live network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import yaml

from mootloop.citations.extract import extract_citations
from mootloop.citations.ledger import ResearchQueue
from mootloop.discovery_parser import parse_discovery_document, save_requests
from mootloop.facts import add_facts_from_file
from mootloop.ingest import ingest_folder
from mootloop.journal import read_events
from mootloop.llm import FakeLLMProvider
from mootloop.models.citations import AuthorityType
from mootloop.models.common import DocId
from mootloop.models.events import GateEvaluated
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestType
from mootloop.orchestrator import (
    citation_export_gate,
    run_with_provider,
    start_run,
    verify_run_citations,
)
from mootloop.vault import init_vault

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "fixtures" / "synthetic-matter"
NOW = "2026-07-11T00:00:00+00:00"

FAKE_CASE = "Nordwind v. Cassini, 512 N.W.2d 999 (Minn. 1994)"
MN_STATUTE = "Minn. Stat. § 336.2-207"
MN_RULE = "Minn. R. Civ. P. 33.01"
FED_STATUTE = "42 U.S.C. § 1983"

_SETS = [
    ("rogs-set1.txt", RequestType.INTERROGATORY),
    ("rfps-set1.txt", RequestType.RFP),
    ("rfas-set1.txt", RequestType.RFA),
]


def _case_norm() -> str:
    return next(
        c.normalized
        for c in extract_citations(FAKE_CASE)
        if c.authority_type == AuthorityType.CASE
    )


def _build_vault(tmp_path: Path) -> Path:
    matter = MatterConfig.model_validate(
        yaml.safe_load((FIXTURE / "matter.yaml").read_text(encoding="utf-8"))
    )
    vault = tmp_path / "vault"
    init_vault(vault, matter, registry_path=tmp_path / "canaries.json")
    ingest_folder(vault, FIXTURE / "source-docs", now=NOW, tags_file=FIXTURE / "tags.yaml")
    add_facts_from_file(vault, FIXTURE / "facts.json")
    for filename, request_type in _SETS:
        data = (FIXTURE / "served" / filename).read_bytes()
        report = parse_discovery_document(
            data.decode("utf-8"), request_type, DocId("doc-servedservedserv")
        )
        save_requests(vault, report.request_set)
    return vault


def _drafter(citations: list[str], amount: str | None = None):
    def draft(spec, prompt: str) -> dict[str, Any]:
        fact_ids = list(spec.prompt_context.get("fact_ids", []))
        text = f"Response to {spec.request_id}. See {' '.join(citations)}."
        if amount is not None:
            text += f" Plaintiff seeks exactly {amount} in damages."
        return {
            "response_text": text,
            "objections": [{"basis": "relevance", "text": "Overbroad as to time period."}],
            "candidate_citations": list(citations),
            "fact_ids_used": fact_ids[:1] if fact_ids else [],
            "attorney_gate_items": [] if fact_ids else ["verify factual basis"],
            "self_assessment": "Grounded in the cited fact.",
        }

    return draft


def _script(citations: list[str], amount: str | None = None):
    drafter = _drafter(citations, amount)
    return {
        ("associate", "associate_draft"): drafter,
        ("associate", "partner_loop"): drafter,
        ("associate", "bolster"): drafter,
    }


def _combined_transport(cl_status: int, calls: list[httpx.Request] | None = None):
    case_norm = _case_norm()

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        host = request.url.host
        if host == "www.courtlistener.com":
            return httpx.Response(
                200,
                json=[
                    {
                        "citation": case_norm,
                        "normalized_citations": [case_norm],
                        "status": cl_status,
                        "clusters": [{"absolute_url": "/opinion/1/x/"}] if cl_status == 200 else [],
                    }
                ],
            )
        if host == "www.revisor.mn.gov":
            return httpx.Response(200, text="Minnesota 336.2-207 and Rule 33.01 text")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _fabrication_events(vault: Path, run_id: str) -> list[GateEvaluated]:
    return [
        e
        for e in read_events(vault, run_id)
        if isinstance(e, GateEvaluated) and e.result.gate == "fabrication"
    ]


def test_planted_fake_citation_blocks_export_gate(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="ph4-fake")
    provider = FakeLLMProvider(script=_script([FAKE_CASE, MN_STATUTE, MN_RULE]))
    assert run_with_provider(vault, run_id, provider, NOW).status == "finished"

    # The fabrication gate ran on every draft/bolster turn (candidate cites -> pending).
    assert _fabrication_events(vault, run_id)

    # CourtListener 404 for the fake case -> unconfirmed; MN cites verify clean.
    summary = verify_run_citations(vault, run_id, NOW, transport=_combined_transport(404))
    statuses = {o.citation.normalized: o.status.value for o in summary.outcomes}
    assert statuses[_case_norm()] == "unconfirmed"
    assert statuses[MN_STATUTE] == "verified"

    gate = citation_export_gate(vault, run_id, NOW)
    assert gate.status == "fail"
    assert any("Nordwind" in f.message for f in gate.findings)


def test_planted_unsupported_amount_fails_fabrication(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="ph4-amount")
    provider = FakeLLMProvider(script=_script([MN_STATUTE], amount="$999,999"))
    run_with_provider(vault, run_id, provider, NOW)

    fab = _fabrication_events(vault, run_id)
    fails = [e for e in fab if e.result.status == "fail"]
    assert fails, "expected a fabrication GateFail for the unsupported amount"
    assert any(
        f.code == "unsupported_amount" for e in fails for f in e.result.findings
    )


def test_research_fulfill_reverifies_and_clears_gate(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="ph4-research")
    provider = FakeLLMProvider(script=_script([FED_STATUTE, MN_STATUTE]))
    run_with_provider(vault, run_id, provider, NOW)

    verify_run_citations(vault, run_id, NOW, transport=_combined_transport(200))
    assert citation_export_gate(vault, run_id, NOW).status == "pending"

    open_requests = ResearchQueue(vault).open_requests()
    fed_request = next(r for r in open_requests if r.normalized == FED_STATUTE)
    from mootloop.citations.verify import fulfill_research_request

    authority = tmp_path / "usc-1983.md"
    authority.write_text("# 42 U.S.C. 1983 — curated authority\n", encoding="utf-8")
    fulfill_research_request(vault, fed_request.request_id, file=authority, now=NOW)

    # Re-verify: the fulfilled cite is now curated-verified, so the gate clears.
    verify_run_citations(vault, run_id, NOW, transport=_combined_transport(200))
    assert citation_export_gate(vault, run_id, NOW).status == "pass"


def test_verification_cache_prevents_duplicate_http(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="ph4-cache")
    provider = FakeLLMProvider(script=_script([FAKE_CASE, MN_STATUTE, MN_RULE]))
    run_with_provider(vault, run_id, provider, NOW)

    calls: list[httpx.Request] = []
    verify_run_citations(vault, run_id, NOW, transport=_combined_transport(200, calls))
    assert calls, "first pass must hit the network"

    calls.clear()
    verify_run_citations(vault, run_id, NOW, transport=_combined_transport(200, calls))
    assert calls == [], "second pass must be served entirely from the ledger cache"
