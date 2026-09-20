"""Completeness gate (plan D7) — the deterministic *presence* criteria of the locked
rubric, checked in code and never sent to an LLM judge.

Each rubric criterion of ``kind="present"`` maps to a pure checker over a
`DraftOutput` (+ the request family + the served request text). The gate records a
`GateResult`; ``coverage`` returns the fraction of applicable presence criteria that
pass, which is one of the convergence signals (a draft that stopped improving but is
not yet *complete* must not be allowed to converge).

Presence findings are recorded, not fatal: unlike the degeneracy gate they never
discard a turn — an incomplete draft simply cannot converge, and the export gate
(Phase 7) is where a hard block lives.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import partial

from mootloop.errors import TaskConfigError
from mootloop.gates import has_hedge
from mootloop.models.gates import GateFail, GateFinding, GatePass, GateResult
from mootloop.models.rubric import Rubric
from mootloop.models.run import DraftOutput

GATE_NAME = "completeness"

_SPECIFICITY_MIN = 12  # an objection's text must carry a request-specific string
_BOILERPLATE = "overly broad and unduly burdensome"
_BOILERPLATE_MIN = 80  # boilerplate is only OK when specific reasons are appended
_TOKEN_RE = re.compile(r"[a-z0-9]{4,}")


def _significant_tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _disposition_present(draft: DraftOutput, req_text: str) -> bool:
    return bool(draft.response_text.strip())


def _objection_basis_specificity(draft: DraftOutput, req_text: str) -> bool:
    for obj in draft.objections:
        if not obj.basis.strip():
            return False
        if len(obj.text.strip()) < _SPECIFICITY_MIN:
            return False
    return True


def _no_boilerplate_general_objection(draft: DraftOutput, req_text: str) -> bool:
    for obj in draft.objections:
        text = obj.text.lower()
        if _BOILERPLATE in text and len(obj.text.strip()) < _BOILERPLATE_MIN:
            return False
    return True


def _no_hedge_subject_to(draft: DraftOutput, req_text: str) -> bool:
    return not has_hedge(draft.response_text, *(o.text for o in draft.objections))


def _rfp_withheld_statement(draft: DraftOutput, req_text: str) -> bool:
    text = draft.response_text.lower()
    return bool(re.search(r"withh(eld|olding)", text)) or "nothing is being withheld" in text


_RFA_DISPOSITIONS = ("admit", "deny", "denied", "qualif", "lack")


def _rfa_disposition(draft: DraftOutput, req_text: str) -> bool:
    text = draft.response_text.lower()
    return any(token in text for token in _RFA_DISPOSITIONS)


def _rfa_reasonable_inquiry(draft: DraftOutput, req_text: str) -> bool:
    text = draft.response_text.lower()
    lack_of_knowledge = "lack" in text or "cannot truthfully admit or deny" in text
    if not lack_of_knowledge:
        return True  # only required for a lack-of-knowledge disposition
    return "reasonable inquiry" in text


def _mn_rog_restatement(draft: DraftOutput, req_text: str) -> bool:
    text = draft.response_text.lower()
    if "interrogatory no" in text:
        return True
    request_tokens = _significant_tokens(req_text)
    if not request_tokens:
        return True
    overlap = request_tokens & _significant_tokens(draft.response_text)
    return len(overlap) / len(request_tokens) >= 0.4


_CHECKS: dict[str, Callable[[DraftOutput, str], bool]] = {
    "disposition-present": _disposition_present,
    "objection-basis-specificity": _objection_basis_specificity,
    "no-boilerplate-general-objection": _no_boilerplate_general_objection,
    "no-hedge-subject-to": _no_hedge_subject_to,
    "rfp-withheld-statement": _rfp_withheld_statement,
    "rfa-disposition": _rfa_disposition,
    "rfa-reasonable-inquiry": _rfa_reasonable_inquiry,
    "mn-rog-restatement": _mn_rog_restatement,
}


_DOCUMENT_SECTIONS = (
    "parties",
    "jurisdiction",
    "allegations",
    "claims",
    "relief",
    "grounds",
    "record",
    "argument",
    "limitations",
    "issues",
    "preservation",
    "standard of review",
    "position",
    "questions",
    "answers",
    "concessions",
    "closing",
    "recommendation",
    "risks",
    "options",
    "next steps",
    "secondary deliverable",
)


def _section_present(name: str, draft: DraftOutput, req_text: str) -> bool:
    match = re.search(r"(?im)^#{1,6}\s+" + re.escape(name) + r"\s*:?\s*$", draft.response_text)
    if match is None:
        return False
    body = re.split(r"(?m)^#{1,6}\s", draft.response_text[match.end() :], maxsplit=1)[0]
    return len(body.strip()) >= 12


_CHECKS.update(
    {
        f"document-section-{name.replace(' ', '-')}": partial(_section_present, name)
        for name in _DOCUMENT_SECTIONS
    }
)


def validate_presence_criteria(rubric: Rubric) -> None:
    unknown = [
        criterion.id
        for criterion in rubric.criteria
        if criterion.kind == "present" and criterion.id not in _CHECKS
    ]
    if unknown:
        raise TaskConfigError(f"unknown deterministic presence criteria: {', '.join(unknown)}")


def _applicable(rubric: Rubric, code: str) -> list[tuple[str, Callable[[DraftOutput, str], bool]]]:
    validate_presence_criteria(rubric)
    return [(criterion.id, _CHECKS[criterion.id]) for criterion in rubric.presence_criteria(code)]


def evaluate(draft: DraftOutput, rubric: Rubric, code: str, req_text: str) -> GateResult:
    """Presence-criteria gate result for one draft (recorded, non-fatal)."""
    findings = [
        GateFinding(code=cid, message=f"presence criterion {cid!r} not met", locator=cid)
        for cid, check in _applicable(rubric, code)
        if not check(draft, req_text)
    ]
    if findings:
        return GateFail(gate=GATE_NAME, findings=findings)
    return GatePass(gate=GATE_NAME)


def coverage(draft: DraftOutput, rubric: Rubric, code: str, req_text: str) -> float:
    """Fraction of applicable presence criteria this draft satisfies, in [0, 1]."""
    checks = _applicable(rubric, code)
    if not checks:
        return 1.0
    passing = sum(1 for _cid, check in checks if check(draft, req_text))
    return passing / len(checks)
