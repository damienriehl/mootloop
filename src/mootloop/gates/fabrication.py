"""Fabrication gate (plan D12 canonical name) — deterministic, every draft/bolster
turn. Distinct from the citation gate: this one anchors *facts*, not law.

Three checks (plan Phase 4):

- **(a) fact existence** — every ``fact_id`` a draft claims to use must exist.
- **(b) provenance-required assertions** — a sentence carrying a quoted span, a
  dollar amount, or a specific date must have that span/amount/date appear in a cited
  fact's statement/provenance quote or the normalized corpus text. Unsupported =
  fabrication.
- **(c) grounding floor** — a draft with zero ``fact_ids`` *and* zero
  ``attorney_gate_items`` fails (never auto-pass on empty facts).

Candidate citations are unverified at draft time, so a draft that cites anything
returns `GatePending` (the citation gate clears it later). Findings are recorded on
the turn and block at the export gate; the gate never raises for a mere failure.
"""

from __future__ import annotations

import re
from pathlib import Path

from mootloop.models.corpus import Manifest
from mootloop.models.facts import Fact
from mootloop.models.gates import GateFail, GateFinding, GatePass, GatePending, GateResult
from mootloop.models.run import DraftOutput

GATE_NAME = "fabrication"

_MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},?\s+\d{4}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
)
_QUOTE_RE = re.compile(r"[\"“]([^\"”]{6,})[\"”]")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _no_space(text: str) -> str:
    return re.sub(r"\s+", "", text)


def build_corpus_text(vault_root: Path | str) -> str:
    """Concatenate every normalized corpus document's text (fabrication provenance)."""
    manifest = Manifest.load(vault_root)
    parts: list[str] = []
    for doc in manifest.docs:
        if doc.normalized_path is None:
            continue
        path = Path(vault_root) / doc.normalized_path
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def check(draft: DraftOutput, facts: list[Fact], corpus_text: str) -> GateResult:
    """Fabrication gate result for one draft (recorded, non-fatal at turn time)."""
    findings: list[GateFinding] = []
    by_id = {str(f.fact_id): f for f in facts}

    for fid in draft.fact_ids_used:
        if fid not in by_id:
            findings.append(
                GateFinding(
                    code="unknown_fact",
                    message=f"cites unknown fact_id {fid!r}",
                    locator=fid,
                )
            )

    if not draft.fact_ids_used and not draft.attorney_gate_items:
        findings.append(
            GateFinding(
                code="ungrounded",
                message="draft cites no fact_id and raises no attorney_gate_item",
            )
        )

    cited = [by_id[fid] for fid in draft.fact_ids_used if fid in by_id]
    supported_parts = [corpus_text]
    for fact in cited:
        supported_parts.append(fact.statement)
        supported_parts.extend(prov.quote for prov in fact.provenance)
    supported = _squash(" ".join(supported_parts))
    supported_nospace = _no_space(supported)

    response = draft.response_text
    for amount in _MONEY_RE.findall(response):
        if _no_space(amount) not in supported_nospace:
            findings.append(
                GateFinding(
                    code="unsupported_amount",
                    message=f"dollar amount {amount.strip()!r} traces to no cited fact/corpus",
                    locator="response_text",
                )
            )
    for match in _DATE_RE.finditer(response):
        date = _squash(match.group(0))
        if _no_space(date) not in supported_nospace:
            findings.append(
                GateFinding(
                    code="unsupported_date",
                    message=f"date {date!r} traces to no cited fact or corpus text",
                    locator="response_text",
                )
            )
    for match in _QUOTE_RE.finditer(response):
        span = _squash(match.group(1))
        if _no_space(span) not in supported_nospace:
            findings.append(
                GateFinding(
                    code="unsupported_quote",
                    message=f"quoted span {span!r} appears in no cited fact or corpus text",
                    locator="response_text",
                )
            )

    if findings:
        return GateFail(gate=GATE_NAME, findings=findings)
    if draft.candidate_citations:
        return GatePending(
            gate=GATE_NAME,
            findings=[
                GateFinding(
                    code="citations_pending",
                    message="citations pending verification",
                )
            ],
        )
    return GatePass(gate=GATE_NAME)
