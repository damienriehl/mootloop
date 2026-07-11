"""Phase 3 end-to-end: rubric-gated convergence, the 3-judge median final gate, and
the graceful budget-cap checkpoint with resume-after-raise.

Everything runs through the FakeLLMProvider — no live calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mootloop.discovery_parser import parse_discovery_document, save_requests
from mootloop.facts import add_facts_from_file
from mootloop.ingest import ingest_folder
from mootloop.journal import read_events
from mootloop.llm import FakeLLMProvider
from mootloop.models.common import DocId
from mootloop.models.events import GateEvaluated
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestType
from mootloop.models.run import PersonaName, TurnSpec
from mootloop.orchestrator import (
    load_request_units,
    plan_next,
    raise_cap,
    run_with_provider,
    start_run,
    status_summary,
)
from mootloop.vault import init_vault

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "fixtures" / "synthetic-matter"
NOW = "2026-07-11T00:00:00+00:00"

_SETS = [
    ("rogs-set1.txt", RequestType.INTERROGATORY),
    ("rfps-set1.txt", RequestType.RFP),
    ("rfas-set1.txt", RequestType.RFA),
]


def _matter(update: dict[str, Any] | None = None) -> MatterConfig:
    raw = yaml.safe_load((FIXTURE / "matter.yaml").read_text(encoding="utf-8"))
    if update:
        raw.update(update)
    return MatterConfig.model_validate(raw)


def _build_vault(tmp_path: Path, matter: MatterConfig) -> Path:
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


def _partner_by_request(spec: TurnSpec, prompt: str) -> dict[str, Any]:
    """Approve (converge early) on RFAs; keep revising everything else to the cap."""
    approve = str(spec.request_id).startswith("RFA")
    return {
        "verdict": "approve" if approve else "revise",
        "critiques": [] if approve else ["tighten the objection"],
        "instructions": [] if approve else ["add request-specific particularity"],
        "self_assessment": "reviewed",
    }


def test_convergence_skips_slots_and_records_median_gate(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path, _matter())
    run_id = start_run(vault, "discovery-responses", NOW, run_id="ph3-conv")
    provider = FakeLLMProvider(script={("partner", "partner_loop"): _partner_by_request})
    state = run_with_provider(vault, run_id, provider, NOW)
    assert state.status == "finished"

    units = load_request_units(vault)
    # Group each request's partner-loop associate *redrafts* (round >= 2 slots).
    redrafts: dict[str, int] = {str(u.request_id): 0 for u in units}
    rubric_loops: dict[str, int] = {str(u.request_id): 0 for u in units}
    for record in state.completed_turns.values():
        rid = str(record.spec.request_id)
        if record.spec.stage == "partner_loop" and record.spec.persona == PersonaName.ASSOCIATE:
            redrafts[rid] += 1
        if record.spec.stage == "partner_loop" and record.spec.persona == PersonaName.RUBRIC_JUDGE:
            rubric_loops[rid] += 1

    rfa = [str(u.request_id) for u in units if str(u.request_id).startswith("RFA")]
    other = [str(u.request_id) for u in units if not str(u.request_id).startswith("RFA")]
    # RFAs converged early on the round-1 partner approval: no round-2 redraft slot.
    assert all(redrafts[r] == 0 for r in rfa)
    assert all(rubric_loops[r] == 1 for r in rfa)
    # The rest ran the partner loop to the cap: one redraft + two in-loop rubric turns.
    assert all(redrafts[r] == 1 for r in other)
    assert all(rubric_loops[r] == 2 for r in other)

    # The decorrelated 3-judge final gate is aggregated and journaled per request.
    events = read_events(vault, run_id)
    rubric_gates = [
        e for e in events if isinstance(e, GateEvaluated) and e.result.gate == "rubric"
    ]
    assert len(rubric_gates) == len(units)
    assert all(g.result.status == "pass" for g in rubric_gates)  # default scores clear 0.75

    # Spend is metered from real tier models (personas=Opus under the moderate tier).
    assert status_summary(vault, run_id)["spend_usd"] > 0


def test_low_cap_checkpoints_then_resumes_after_raise(tmp_path: Path) -> None:
    matter = _matter({"budget": {"tier": "moderate", "hard_cap_usd": 0.05}})
    vault = _build_vault(tmp_path, matter)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="ph3-cap")

    state = run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    assert state.status == "capped"

    gaps = vault / "deliverables" / f"gaps-{run_id}.md"
    assert gaps.is_file()
    report = gaps.read_text(encoding="utf-8")
    assert "unfinished" in report
    assert "raise-cap" in report

    # Over-cap: nothing more is schedulable until the cap is raised.
    assert plan_next(vault, run_id) == []

    # Raise the cap generously and resume — the run now finishes cleanly.
    raise_cap(vault, run_id, 10_000.0)
    resumed = run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    assert resumed.status == "finished"

    units = load_request_units(vault)
    events = read_events(vault, run_id)
    rubric_gates = [
        e for e in events if isinstance(e, GateEvaluated) and e.result.gate == "rubric"
    ]
    assert len(rubric_gates) == len(units)
