"""The gate ledger (plan D12/H8): the single machine-readable source of truth for
"what blocks export".

``runs/<run-id>/gate-ledger.json`` is a *derived* view, regenerated on demand by
folding the journal (per-turn degeneracy/completeness/fabrication/rubric gates and the
run-level citation gate), the attorney-gate decisions, and the attestation manifest.
Phase 7's export reads ``export_ready`` and refuses unless it is true.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mootloop import attest
from mootloop.decisions import DecisionStore
from mootloop.journal import load_state, read_events
from mootloop.models.events import GateEvaluated
from mootloop.orchestrator import load_request_units
from mootloop.vault import atomic_write_text, safe_vault_path

# Per-request turn gates, in report order, plus the run-level gates. Degeneracy and
# completeness are *recorded* but non-fatal (a degenerate turn is discarded, never
# completed; completeness is a per-loop quality signal) — only fabrication and rubric
# block export among the turn gates (plan D12).
TURN_GATES: tuple[str, ...] = ("degeneracy", "completeness", "fabrication", "rubric")
BLOCKING_TURN_GATES: tuple[str, ...] = ("fabrication", "rubric")
RUN_GATES: tuple[str, ...] = ("citations", "decisions", "attestation")

# The recorded citation gate uses the singular name.
_CITATION_GATE_NAME = "citation"

_SEVERITY = {"pass": 0, "pending": 1, "fail": 2}
_BLOCKING = {"fail", "pending"}


def _worse(a: str, b: str) -> str:
    return a if _SEVERITY.get(a, 1) >= _SEVERITY.get(b, 1) else b


def _turn_gate_status(vault_root: Path | str, run_id: str) -> tuple[dict[str, dict[str, str]], str]:
    """Fold per-request turn-gate statuses and the run-level citation status from the
    journal. Absent gates default to ``pending`` (fail closed)."""
    state = load_state(vault_root, run_id)
    per_request: dict[str, dict[str, str]] = {}
    citation_status = "pending"  # never verified -> blocks (plan H8, fail closed)
    for event in read_events(vault_root, run_id):
        if not isinstance(event, GateEvaluated):
            continue
        gate = event.result.gate
        status = event.result.status
        if gate == _CITATION_GATE_NAME:
            # The latest recorded citation gate wins (Phase 4 runs it as one step).
            citation_status = status
            continue
        record = state.completed_turns.get(event.turn_id)
        if record is None or record.spec.request_id is None:
            continue
        rid = str(record.spec.request_id)
        bucket = per_request.setdefault(rid, {})
        bucket[gate] = _worse(bucket.get(gate, "pass"), status) if gate in bucket else status
    return per_request, citation_status


@dataclass(frozen=True)
class GateLedgerDoc:
    """The derived gate-ledger view (written to ``gate-ledger.json``)."""

    run_id: str
    export_ready: bool
    blockers: list[str]
    overall: dict[str, str]
    gates: dict[str, dict[str, str]]

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "export_ready": self.export_ready,
            "blockers": self.blockers,
            "overall": self.overall,
            "gates": self.gates,
        }


def build_ledger(vault_root: Path | str, run_id: str) -> GateLedgerDoc:
    """Assemble the gate-ledger document (pure; no writes)."""
    per_request, citation_status = _turn_gate_status(vault_root, run_id)
    units = load_request_units(vault_root)
    open_decisions = DecisionStore(vault_root, run_id).list_open()
    open_by_request: dict[str, int] = {}
    request_less_open = 0
    for decision in open_decisions:
        if decision.request_id is None:
            request_less_open += 1
        else:
            rid = str(decision.request_id)
            open_by_request[rid] = open_by_request.get(rid, 0) + 1

    att = attest.attestation_state(vault_root, run_id)
    attestation_status = {"valid": "pass", "invalidated": "fail", "missing": "pending"}[att.status]
    decisions_status = "pass" if not open_decisions else "fail"

    gates: dict[str, dict[str, str]] = {}
    for unit in units:
        rid = str(unit.request_id)
        recorded = per_request.get(rid, {})
        row = {gate: recorded.get(gate, "pending") for gate in TURN_GATES}
        row["citations"] = citation_status
        row["decisions"] = (
            "fail" if (open_by_request.get(rid, 0) or request_less_open) else "pass"
        )
        row["attestation"] = attestation_status
        gates[rid] = row

    ready, blockers = _export_ready(gates, decisions_status, attestation_status, citation_status)
    return GateLedgerDoc(
        run_id=run_id,
        export_ready=ready,
        blockers=blockers,
        overall={
            "citations": citation_status,
            "decisions": decisions_status,
            "attestation": attestation_status,
        },
        gates=gates,
    )


def _export_ready(
    gates: dict[str, dict[str, str]],
    decisions_status: str,
    attestation_status: str,
    citation_status: str,
) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if decisions_status in _BLOCKING:
        blockers.append("decisions")
    if attestation_status in _BLOCKING:
        blockers.append("attestation")
    if citation_status in _BLOCKING:
        blockers.append("citations")
    for row in gates.values():
        for gate in BLOCKING_TURN_GATES:
            if row.get(gate, "pending") in _BLOCKING and gate not in blockers:
                blockers.append(gate)
    return (not blockers), blockers


def export_ready(vault_root: Path | str, run_id: str) -> tuple[bool, list[str]]:
    """(ready, blockers) — the export predicate the ledger exists to answer."""
    doc = build_ledger(vault_root, run_id)
    return doc.export_ready, doc.blockers


def write_ledger(vault_root: Path | str, run_id: str) -> Path:
    """Regenerate ``runs/<run-id>/gate-ledger.json`` and return its path."""
    doc = build_ledger(vault_root, run_id)
    path = safe_vault_path(vault_root, "runs", run_id, "gate-ledger.json")
    atomic_write_text(path, json.dumps(doc.to_dict(), indent=2, sort_keys=True) + "\n")
    return path
