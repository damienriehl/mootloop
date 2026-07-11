"""Degeneracy gate (plan D12 canonical name) — deterministic, every turn.

Catches turns that parsed as valid JSON but did no real work: empty drafts,
objections with no basis, unresolved placeholder markers, or a draft that neither
grounds in a fact nor flags an attorney-gate item. Non-draft outputs (critiques,
judge rulings) are structurally constrained by their schema, so the gate only
requires a non-empty self-assessment there.

Returns a `GateResult`; it never raises for a mere failure.
"""

from __future__ import annotations

from mootloop.models.gates import GateFail, GateFinding, GatePass, GateResult
from mootloop.models.run import CritiqueOutput, DraftOutput, JudgeOutput, RubricScoreOutput

GATE_NAME = "degeneracy"

# Case-insensitive markers that betray an unfinished draft.
_PLACEHOLDERS: tuple[str, ...] = ("[todo", "[insert", "lorem")

# The condemned "subject to and without waiving" hedge (Liguria Foods; plan D7) —
# answering while reserving objections is a deterministic failure, never served.
_HEDGE = "subject to and without waiving"


def _placeholder_findings(text: str, locator: str) -> list[GateFinding]:
    lowered = text.lower()
    return [
        GateFinding(code="placeholder", message=f"unresolved marker {marker!r}", locator=locator)
        for marker in _PLACEHOLDERS
        if marker in lowered
    ]


def _check_draft(draft: DraftOutput) -> list[GateFinding]:
    findings: list[GateFinding] = []
    if not draft.response_text.strip():
        findings.append(GateFinding(code="empty_response", message="response_text is empty"))
    for idx, objection in enumerate(draft.objections):
        if not objection.basis.strip():
            findings.append(
                GateFinding(
                    code="objection_no_basis",
                    message="objection has an empty basis",
                    locator=f"objections[{idx}]",
                )
            )
    findings.extend(_placeholder_findings(draft.response_text, "response_text"))
    for idx, objection in enumerate(draft.objections):
        findings.extend(_placeholder_findings(objection.text, f"objections[{idx}].text"))
    if _HEDGE in draft.response_text.lower():
        findings.append(
            GateFinding(
                code="hedge_subject_to",
                message='response hedges "subject to and without waiving" (Liguria Foods)',
                locator="response_text",
            )
        )
    grounded = bool(draft.fact_ids_used) or bool(draft.attorney_gate_items)
    if not grounded:
        findings.append(
            GateFinding(
                code="ungrounded",
                message="draft cites no fact_id and raises no attorney_gate_item",
            )
        )
    return findings


def evaluate(
    output: DraftOutput | CritiqueOutput | JudgeOutput | RubricScoreOutput,
) -> GateResult:
    """Evaluate the degeneracy gate against a validated turn output."""
    if isinstance(output, DraftOutput):
        findings = _check_draft(output)
    else:
        findings = []
        if not output.self_assessment.strip():
            findings.append(
                GateFinding(code="empty_self_assessment", message="self_assessment is empty")
            )
    if findings:
        return GateFail(gate=GATE_NAME, findings=findings)
    return GatePass(gate=GATE_NAME)
