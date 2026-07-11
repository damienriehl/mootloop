"""Structural invariants for the Phase 5 attorney-gate / attestation layer.

Append-only discipline: every decision and attestation line parses, and a decision's
status never regresses without a new (later) line. Observed runs end their STATUS.md
with a valid STATE marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mootloop.decisions import DecisionStore, resolve
from mootloop.discovery_parser import save_requests
from mootloop.facts import FactStore
from mootloop.llm import FakeLLMProvider
from mootloop.models.attestations import Attestation
from mootloop.models.common import DocId
from mootloop.models.decisions import Decision
from mootloop.models.requests import RequestItem, RequestSet, RequestType, make_request_id
from mootloop.orchestrator import run_with_provider, start_run, state_marker
from mootloop.vault import init_vault
from tests.conftest import make_matter

pytestmark = pytest.mark.invariant

NOW = "2026-07-11T00:00:00+00:00"
_VALID_MARKERS = {"working", "review-needed", "ask-pending", "blocked", "done"}
# The decision statuses in order of finality (open first, terminal after).
_ORDER = {"open": 0, "approved": 1, "modified": 1, "denied": 1}


def _vault(tmp_path: Path, request_type: RequestType, mode: str) -> Path:
    vault = tmp_path / "vault"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
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
    run_id = f"inv5-{mode}"
    start_run(vault, "discovery-responses", NOW, run_id=run_id, mode=mode)
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    return vault


def test_decisions_jsonl_is_append_only_shape(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.INTERROGATORY, "autonomous")
    run_id = "inv5-autonomous"
    path = vault / "runs" / run_id / "decisions" / "decisions.jsonl"
    # Every line parses as a Decision.
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines
    for line in lines:
        Decision.model_validate_json(line)

    # Resolve a decision -> a NEW line appended, never a rewrite; status only advances.
    decision = DecisionStore(vault, run_id).list_open()[0]
    before = len(lines)
    resolve(vault, run_id, decision.decision_id, "approve", None, "", "Atty", "human", NOW)
    after = path.read_text(encoding="utf-8").splitlines()
    assert len([ln for ln in after if ln.strip()]) == before + 1

    # Per decision id, statuses are non-regressing across appended lines.
    seen: dict[str, int] = {}
    for line in after:
        if not line.strip():
            continue
        record = Decision.model_validate_json(line)
        rank = _ORDER[record.status]
        assert rank >= seen.get(record.decision_id, 0)
        seen[record.decision_id] = rank


def test_decision_sidecar_is_write_once(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.INTERROGATORY, "autonomous")
    run_id = "inv5-autonomous"
    decision = DecisionStore(vault, run_id).list_open()[0]
    sidecar = vault / "runs" / run_id / "decisions" / f"{decision.decision_id}.json"
    original = sidecar.read_text(encoding="utf-8")
    # Resolving appends to the jsonl but must NOT rewrite the immutable proposal sidecar.
    resolve(vault, run_id, decision.decision_id, "deny", None, "", "Atty", "human", NOW)
    assert sidecar.read_text(encoding="utf-8") == original


def test_attestations_jsonl_lines_parse(tmp_path: Path) -> None:
    from mootloop import attest
    from mootloop.decisions import open_by_taxonomy
    from mootloop.orchestrator import verify_run_citations
    from mootloop.vault import load_matter

    vault = _vault(tmp_path, RequestType.INTERROGATORY, "autonomous")
    run_id = "inv5-autonomous"
    matter = load_matter(vault)
    for d in [
        *open_by_taxonomy(vault, run_id, matter, "hard-human"),
        *open_by_taxonomy(vault, run_id, matter, "policy-delegable"),
    ]:
        resolve(
            vault, run_id, d.decision_id, "approve", d.proposal.recommended, "", "A", "human", NOW
        )
    verify_run_citations(vault, run_id, NOW)
    attest.attest(vault, run_id, "Jane", NOW)

    path = vault / "runs" / run_id / "attestations.jsonl"
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines
    for line in lines:
        Attestation.model_validate_json(line)


def test_observed_status_md_ends_with_valid_state_marker(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.RFA, "observed")
    for run_dir in (vault / "runs").iterdir():
        status_md = run_dir / "STATUS.md"
        if not status_md.is_file():
            continue
        last = [ln for ln in status_md.read_text(encoding="utf-8").splitlines() if ln.strip()][-1]
        assert last.startswith("STATE: ")
        assert last.removeprefix("STATE: ") in _VALID_MARKERS


def test_state_marker_total_over_run_statuses() -> None:
    statuses = (
        "running",
        "finished",
        "needs_attention",
        "capped",
        "needs_decisions",
        "checkpoint",
    )
    for status in statuses:
        assert state_marker(status) in _VALID_MARKERS
