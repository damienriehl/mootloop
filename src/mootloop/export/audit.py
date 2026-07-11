"""AI-use audit log (plan Phase 7 / H8): the per-passage attribution export.

Derived STRICTLY from the journal, the verification ledger, the decisions log, and the
attestation manifest — never from anything an LLM asserted (a persona can never claim
"verified"; status is read from the immutable cache). Per response block: contributing
turn ids + personas + models, each citation's verification status/source/verified_at,
the attorney decisions applied, the rubric-gate scores, and the attestation state —
plus run metadata (rubric version, task, config digest) and the citator disclosure.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from mootloop import attest
from mootloop.citations.extract import extract_citations
from mootloop.citations.ledger import DEFAULT_MAX_CACHE_AGE_DAYS, VerificationLedger
from mootloop.citations.verify import CITATOR_DISCLOSURE
from mootloop.decisions import DecisionStore
from mootloop.export import deliverables_dir
from mootloop.journal import load_state, read_events
from mootloop.models.citations import VerificationStatus
from mootloop.models.events import GateEvaluated, RunStarted
from mootloop.models.run import DraftOutput
from mootloop.orchestrator import operative_drafts
from mootloop.vault import atomic_write_text


def _config_digest(vault_root: Path | str, run_id: str) -> str | None:
    for event in read_events(vault_root, run_id):
        if isinstance(event, RunStarted):
            return event.config_digest
    return None


def _rubric_scores_by_request(vault_root: Path | str, run_id: str) -> dict[str, dict[str, str]]:
    """request_id -> {status, detail} for the final rubric gate (journal-sourced)."""
    state = load_state(vault_root, run_id)
    out: dict[str, dict[str, str]] = {}
    for event in read_events(vault_root, run_id):
        if not isinstance(event, GateEvaluated) or event.result.gate != "rubric":
            continue
        record = state.completed_turns.get(event.turn_id)
        if record is None or record.spec.request_id is None:
            continue
        detail = "; ".join(f"{f.code}:{f.message}" for f in event.result.findings)
        out[str(record.spec.request_id)] = {"status": event.result.status, "detail": detail}
    return out


def _decisions_by_request(vault_root: Path | str, run_id: str) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for decision in DecisionStore(vault_root, run_id).list_all():
        if decision.request_id is None or decision.resolution is None:
            continue
        out.setdefault(str(decision.request_id), []).append(
            {
                "decision_id": decision.decision_id,
                "kind": decision.kind.value,
                "action": decision.resolution.action,
                "chosen_key": decision.resolution.chosen_key or "",
                "decided_by": decision.resolution.decided_by,
                "source": decision.resolution.source,
            }
        )
    return out


def build_audit_log(vault_root: Path | str, run_id: str, now: str) -> Path:
    """Write ``deliverables/<run-id>/audit-log.json`` and return its path."""
    state = load_state(vault_root, run_id)
    ledger = VerificationLedger(vault_root).folded(
        now=datetime.fromisoformat(now), max_cache_age_days=DEFAULT_MAX_CACHE_AGE_DAYS
    )
    rubric_scores = _rubric_scores_by_request(vault_root, run_id)
    decisions_by_request = _decisions_by_request(vault_root, run_id)
    attestation = attest.attestation_state(vault_root, run_id)

    blocks: list[dict[str, object]] = []
    for request, draft in operative_drafts(vault_root, run_id):
        rid = str(request.request_id)
        contributing = [
            {
                "turn_id": record.spec.turn_id,
                "persona": record.spec.persona.value,
                "model": record.spec.model or "seat",
                "stage": record.spec.stage,
            }
            for record in state.completed_turns.values()
            if record.spec.request_id is not None and str(record.spec.request_id) == rid
        ]
        contributing.sort(key=lambda c: c["turn_id"])

        citations: list[dict[str, object]] = []
        if isinstance(draft, DraftOutput):
            seen: set[str] = set()
            for text in [draft.response_text, *draft.candidate_citations]:
                for citation in extract_citations(text):
                    if citation.citation_id in seen:
                        continue
                    seen.add(citation.citation_id)
                    record = ledger.get(citation.citation_id)
                    citations.append(
                        {
                            "citation_id": citation.citation_id,
                            "raw_text": citation.raw_text,
                            "status": (
                                record.status if record else VerificationStatus.PENDING
                            ).value,
                            "source": record.source if record else None,
                            "verified_at": record.verified_at if record else None,
                        }
                    )

        blocks.append(
            {
                "request_id": rid,
                "contributing_turns": contributing,
                "citations": citations,
                "decisions_applied": decisions_by_request.get(rid, []),
                "rubric": rubric_scores.get(rid, {"status": "pending", "detail": ""}),
                "attestation": attestation.status,
            }
        )

    audit = {
        "run_id": run_id,
        "generated_at": now,
        "task": state.task,
        "rubric_version": state.rubric_version,
        "config_digest": _config_digest(vault_root, run_id),
        "attestation": {
            "state": attestation.status,
            "reason": attestation.reason,
        },
        "citator_disclosure": CITATOR_DISCLOSURE,
        "response_blocks": blocks,
    }

    path = deliverables_dir(vault_root, run_id) / "audit-log.json"
    atomic_write_text(path, json.dumps(audit, indent=2) + "\n")
    return path
