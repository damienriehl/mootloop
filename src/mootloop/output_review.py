"""Conservative compatibility checks between attorney choices and operative drafts."""

from collections.abc import Iterable, Mapping

from mootloop.models.decisions import Decision, DecisionKind
from mootloop.models.requests import code_from_request_id
from mootloop.models.run import DraftOutput


def decision_revision_requirements(
    decisions: Iterable[Decision], drafts: Mapping[str, DraftOutput | None]
) -> dict[str, str]:
    """Return closed decisions whose selected outcome is not established by the draft.

    Choices requiring judgment about factual support or rewritten language cannot be
    implemented by deleting metadata or relabeling an unchanged answer. Those require
    a revised response and review in a new run; no in-place revision workflow exists.
    """
    required: dict[str, str] = {}
    for decision in decisions:
        if decision.status == "open":
            continue
        resolution = decision.resolution
        chosen = resolution.chosen_key if resolution else None
        applicable = _applicable_drafts(decision, drafts)
        compatible = False
        if (
            resolution is not None
            and resolution.action != "deny"
            and applicable
            and all(draft is not None for draft in applicable)
        ):
            if decision.kind is DecisionKind.OBJECTION_POSTURE:
                compatible = chosen == "assert" or (
                    chosen == "waive"
                    and all(draft is not None and not draft.objections for draft in applicable)
                )
            elif decision.kind is DecisionKind.UNSUPPORTED_ASSERTION:
                compatible = chosen == "attest_anyway"
            elif decision.kind is DecisionKind.RFA_DISPOSITION:
                compatible = chosen is not None and all(
                    draft is not None and draft.rfa_disposition == chosen for draft in applicable
                )
        if not compatible:
            if decision.kind is DecisionKind.PRIVILEGE_CALL:
                required[str(decision.decision_id)] = (
                    f"Decision {decision.decision_id} ({chosen or 'no selected outcome'}) "
                    "requires verified withholding/production and privilege-log review. "
                    "Clean export for privilege choices is not yet supported; use the draft "
                    "review copy. Starting a new run alone will not clear this blocker."
                )
            else:
                required[str(decision.decision_id)] = (
                    f"Decision {decision.decision_id} ({chosen or 'no selected outcome'}) requires "
                    "a revised response and attorney review in a new run; the operative draft "
                    "does not establish the selected outcome."
                )
    return required


def _applicable_drafts(
    decision: Decision, drafts: Mapping[str, DraftOutput | None]
) -> list[DraftOutput | None]:
    if decision.request_id is not None:
        return [drafts.get(str(decision.request_id))]
    if decision.kind is DecisionKind.OBJECTION_POSTURE:
        for code in ("rog", "rfp", "rfa"):
            if decision.proposal.summary == f"Objection posture for {code.upper()} requests":
                return [draft for rid, draft in drafts.items() if code_from_request_id(rid) == code]
    return list(drafts.values())
