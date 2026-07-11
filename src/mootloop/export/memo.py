"""Strategy memo (plan Phase 7 / D12 derived view).

Per request: objection-strategy summary, panel survival rates, opposing-counsel attack
findings (from ``oc_attack`` turns), open risks (unconfirmed citations with their
research-queue ids, low-survival objections), a run spend summary, and the standing
citator disclosure line (imported from ``citations.verify``).

Attorney work product — never served; the memo may reference internal reasoning.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mootloop.citations.extract import extract_citations
from mootloop.citations.ledger import DEFAULT_MAX_CACHE_AGE_DAYS, ResearchQueue, VerificationLedger
from mootloop.citations.verify import CITATOR_DISCLOSURE, _research_request_id
from mootloop.export import deliverables_dir
from mootloop.journal import load_state
from mootloop.models.citations import VerificationStatus
from mootloop.models.run import CritiqueOutput
from mootloop.orchestrator import operative_drafts
from mootloop.panels import build_panel_report
from mootloop.vault import atomic_write_text


def _oc_findings(vault_root: Path | str, run_id: str) -> dict[str, list[str]]:
    """request_id -> the opposing-counsel critiques raised in its ``oc_attack`` turns."""
    state = load_state(vault_root, run_id)
    out: dict[str, list[str]] = {}
    for record in state.completed_turns.values():
        if record.spec.stage != "oc_attack" or record.spec.request_id is None:
            continue
        critique = CritiqueOutput.model_validate(record.output)
        out.setdefault(str(record.spec.request_id), []).extend(critique.critiques)
    return out


def build_strategy_memo(vault_root: Path | str, run_id: str, now: str) -> Path:
    """Write ``deliverables/<run-id>/strategy-memo.md`` and return its path."""
    state = load_state(vault_root, run_id)
    drafts = operative_drafts(vault_root, run_id)
    panel = build_panel_report(vault_root, run_id)
    oc_findings = _oc_findings(vault_root, run_id)
    ledger = VerificationLedger(vault_root).folded(
        now=datetime.fromisoformat(now), max_cache_age_days=DEFAULT_MAX_CACHE_AGE_DAYS
    )
    open_research = {r.request_id for r in ResearchQueue(vault_root).open_requests()}

    lines: list[str] = [
        f"# Strategy Memo — run `{run_id}`",
        "",
        f"_Attorney work product · generated {now}_",
        "",
    ]

    for request, draft in drafts:
        rid = str(request.request_id)
        lines.append(f"## {rid}")
        lines.append("")
        objections = draft.objections if draft else []
        if objections:
            lines.append("**Objection strategy:** " + "; ".join(
                f"{o.basis}" for o in objections
            ))
        else:
            lines.append("**Objection strategy:** no objections asserted — full answer.")

        results = panel.for_request(rid)
        if results:
            lines.append("")
            lines.append("**Panel survival rates:**")
            for r in results:
                lines.append(
                    f"- objection {r.objection_index} ({r.objection_basis}): "
                    f"{r.survive_votes}/{r.total_votes} survive ({r.survival_rate:.0%})"
                )

        findings = oc_findings.get(rid, [])
        if findings:
            lines.append("")
            lines.append("**Opposing-counsel attack findings:**")
            for finding in findings:
                lines.append(f"- {finding}")

        risks: list[str] = []
        weak = [r for r in results if r.total_votes > 0 and r.survival_rate < 0.5]
        for r in weak:
            risks.append(
                f"objection {r.objection_index} ({r.objection_basis}) is weak "
                f"({r.survive_votes}/{r.total_votes} survive)"
            )
        if draft is not None:
            texts = [draft.response_text, *draft.candidate_citations]
            for text in texts:
                for citation in extract_citations(text):
                    record = ledger.get(citation.citation_id)
                    status = record.status if record else VerificationStatus.PENDING
                    if status is VerificationStatus.VERIFIED:
                        continue
                    queue_id = _research_request_id(citation.normalized)
                    queued = " (queued: " + queue_id + ")" if queue_id in open_research else ""
                    risks.append(
                        f"citation {citation.raw_text!r} is {status.value}{queued}"
                    )
        if risks:
            lines.append("")
            lines.append("**Open risks:**")
            for risk in risks:
                lines.append(f"- {risk}")
        lines.append("")

    lines.append("## Spend summary")
    lines.append("")
    total_tokens = (
        state.total_input_tokens
        + state.total_cache_read
        + state.total_cache_write
        + state.total_output_tokens
    )
    lines.append(f"- Completed turns: {len(state.completed_turns)}")
    lines.append(f"- Total tokens: {total_tokens}")
    lines.append(f"- Notional spend: ${state.total_spend_usd:.4f} (plan quota, not billed)")
    lines.append("")
    lines.append(f"> {CITATOR_DISCLOSURE}")
    lines.append("")

    path = deliverables_dir(vault_root, run_id) / "strategy-memo.md"
    atomic_write_text(path, "\n".join(lines).rstrip("\n") + "\n")
    return path
