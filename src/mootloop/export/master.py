"""Court-formatted master + client verification page (plan D7).

`build_court_master` reads the run's *operative* (post-restructure) drafts, the
matter, and the served-request sets and emits ``deliverables/<run-id>/master.md``:
a MN discovery-response document — caption block, a per-set document title, each
interrogatory RESTATED before its answer (MN Rule 33), per-request response blocks
keeping ``::: {#resp-ID}`` anchors, objections with specificity, RFA dispositions
(with the reasonable-inquiry recital on lack-of-knowledge), RFP withheld-statements,
the attorney signature block, and a certificate-of-service stub.

`build_verification_page` emits the separate ``verification.md`` for rog sets with
MN's EXACT perjury declaration (unsigned — the client signs on paper).

No general/boilerplate objections and never the "subject to and without waiving"
hedge (plan D7): the degeneracy gate blocks the latter upstream, and this renderer
never emits it.
"""

from __future__ import annotations

from pathlib import Path

from mootloop.decisions import DecisionStore
from mootloop.export import deliverables_dir, load_request_sets
from mootloop.models.decisions import DecisionKind
from mootloop.models.matter import Attorney, MatterConfig
from mootloop.models.requests import (
    RequestItem,
    RequestSet,
    RequestType,
    marker_label,
)
from mootloop.models.run import DraftOutput
from mootloop.orchestrator import operative_drafts
from mootloop.vault import atomic_write_text, load_matter

# The EXACT MN interrogatory perjury declaration (Minn. R. Civ. P. 33; plan D7).
MN_VERIFICATION_DECLARATION = (
    "I declare under penalty of perjury that everything I have stated in this "
    "document is true and correct."
)

# Heller v. City of Dallas / Rule 34(b): each production response must say whether
# responsive materials are withheld on the basis of an objection (plan D7).
_WITHHELD_YES = (
    "Responsive materials are being withheld on the basis of the foregoing objection(s)."
)
_WITHHELD_NO = "No responsive materials are being withheld on the basis of any objection."

# Rule 36(a): the reasonable-inquiry recital a lack-of-knowledge answer must carry.
_REASONABLE_INQUIRY = (
    "Responding party has made reasonable inquiry and the information known or "
    "readily obtainable is insufficient to enable it to admit or deny."
)

_RFA_DISPOSITION_LABEL = {
    "admit": "Admitted.",
    "deny": "Denied.",
    "qualify": "Admitted in part and denied in part.",
    "lack_of_knowledge": "Responding party can neither admit nor deny.",
}

_PLACEHOLDER_ATTORNEY = Attorney(name="[ATTORNEY NAME]")


def _side_labels(matter: MatterConfig) -> tuple[str, str]:
    """(our-side, opposing-side) title-cased (``Defendant`` / ``Plaintiff``)."""
    ours = matter.our_side.capitalize()
    theirs = "Plaintiff" if matter.our_side == "defendant" else "Defendant"
    return ours, theirs


def _caption_block(matter: MatterConfig) -> list[str]:
    cap = matter.caption
    plaintiffs = [p.name for p in matter.parties if p.role == "plaintiff"]
    defendants = [p.name for p in matter.parties if p.role == "defendant"]
    lines = [
        f"STATE OF {matter.jurisdiction.state.upper()}",
        f"COUNTY OF {cap.county.upper()}",
        "",
        cap.court_name,
        "",
        f"{', '.join(plaintiffs) or '[Plaintiff]'},",
        "",
        "> Plaintiff,",
        "",
        "v.",
        "",
        f"{', '.join(defendants) or '[Defendant]'},",
        "",
        "> Defendant.",
        "",
        f"Case No. {cap.case_number}",
    ]
    if cap.judge_name:
        lines.append(f"Judge: {cap.judge_name}")
    return lines


def _document_title(matter: MatterConfig, request_set: RequestSet) -> str:
    ours, _ = _side_labels(matter)
    return f"{ours.upper()}'S RESPONSES AND OBJECTIONS TO {request_set.title.upper()}"


def _objection_lines(draft: DraftOutput) -> list[str]:
    lines: list[str] = []
    for objection in draft.objections:
        lines.append(f"OBJECTION ({objection.basis}): {objection.text}")
    return lines


def _rfa_disposition(draft: DraftOutput, resolved: str | None) -> tuple[str, str]:
    """(display text, disposition key) for an RFA response."""
    disposition = resolved or draft.rfa_disposition or "deny"
    return _RFA_DISPOSITION_LABEL.get(disposition, "Denied."), disposition


def _response_block(
    matter: MatterConfig,
    request: RequestItem,
    draft: DraftOutput | None,
    request_type: RequestType,
    resolved_rfa: str | None,
) -> list[str]:
    label = marker_label(request_type)
    lines: list[str] = [f"::: {{#resp-{request.request_id}}}"]
    # MN Rule 33: restate each interrogatory before its answer; do the same restatement
    # for RFP/RFA for a self-contained served document.
    lines.append(f"**{label} NO. {request.number}:** {request.text.strip()}")
    lines.append("")

    if draft is None:
        lines.append("**RESPONSE:** _no response drafted_")
        lines.append("")
        lines.append(":::")
        return lines

    if request_type is RequestType.RFA:
        display, disposition = _rfa_disposition(draft, resolved_rfa)
        lines.append(f"**RESPONSE:** {display}")
        if disposition == "lack_of_knowledge":
            lines.append("")
            lines.append(_REASONABLE_INQUIRY)
        for objection_line in _objection_lines(draft):
            lines.append("")
            lines.append(objection_line)
    else:
        for objection_line in _objection_lines(draft):
            lines.append(objection_line)
            lines.append("")
        lines.append(f"**RESPONSE:** {draft.response_text.strip()}")
        if request_type is RequestType.RFP:
            lines.append("")
            lines.append(_WITHHELD_YES if draft.objections else _WITHHELD_NO)
    lines.append("")
    lines.append(":::")
    return lines


def _signature_block(matter: MatterConfig) -> list[str]:
    attorney = matter.attorney or _PLACEHOLDER_ATTORNEY
    lines = [
        "## Signature",
        "",
        "Respectfully submitted,",
        "",
        f"{attorney.name}",
    ]
    if attorney.bar_number:
        lines.append(f"Attorney Registration No. {attorney.bar_number}")
    for field in (attorney.firm, attorney.address):
        if field:
            lines.append(field)
    contact = " · ".join(f for f in (attorney.phone, attorney.email) if f)
    if contact:
        lines.append(contact)
    ours, _ = _side_labels(matter)
    lines.append("")
    lines.append(f"Attorney for {ours}")
    return lines


def _certificate_of_service(matter: MatterConfig) -> list[str]:
    _, theirs = _side_labels(matter)
    return [
        "## Certificate of Service",
        "",
        "The undersigned certifies that a true and correct copy of the foregoing was "
        f"served upon counsel for {theirs} by the method agreed among the parties on "
        "the date set forth below.",
        "",
        "Dated: ____________________",
        "",
        "_______________________________",
    ]


def _resolved_rfa_dispositions(vault_root: Path | str, run_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for decision in DecisionStore(vault_root, run_id).list_all():
        if (
            decision.kind is DecisionKind.RFA_DISPOSITION
            and decision.request_id is not None
            and decision.resolution is not None
            and decision.resolution.chosen_key is not None
        ):
            out[str(decision.request_id)] = decision.resolution.chosen_key
    return out


def build_court_master(vault_root: Path | str, run_id: str, now: str) -> Path:
    """Assemble the court-formatted master and write ``deliverables/<run-id>/master.md``."""
    matter = load_matter(vault_root)
    request_sets = load_request_sets(vault_root)
    drafts: dict[str, DraftOutput | None] = {
        str(item.request_id): draft for item, draft in operative_drafts(vault_root, run_id)
    }
    resolved_rfa = _resolved_rfa_dispositions(vault_root, run_id)

    lines: list[str] = []
    lines.extend(_caption_block(matter))
    lines.append("")
    lines.append(f"_Generated {now} · run `{run_id}`_")
    lines.append("")

    for request_set in request_sets:
        lines.append(f"# {_document_title(matter, request_set)}")
        lines.append("")
        top_level = [item for item in request_set.items if item.subpart is None]
        top_level.sort(key=lambda i: i.number)
        for item in top_level:
            draft = drafts.get(str(item.request_id))
            lines.extend(
                _response_block(
                    matter, item, draft, request_set.request_type, resolved_rfa.get(str(item.request_id))
                )
            )
            lines.append("")

    lines.extend(_signature_block(matter))
    lines.append("")
    lines.extend(_certificate_of_service(matter))
    lines.append("")

    path = deliverables_dir(vault_root, run_id) / "master.md"
    atomic_write_text(path, "\n".join(lines).rstrip("\n") + "\n")
    return path


def build_verification_page(vault_root: Path | str, run_id: str, now: str) -> Path | None:
    """Write the separate ``verification.md`` client-oath page for rog sets (plan D7).

    Returns None (and writes nothing) when the run served no interrogatory set — only
    interrogatories carry the Rule 33 client verification. Unsigned: the client signs
    on paper.
    """
    request_sets = load_request_sets(vault_root)
    rog_sets = [s for s in request_sets if s.request_type is RequestType.INTERROGATORY]
    if not rog_sets:
        return None
    matter = load_matter(vault_root)
    lines: list[str] = []
    lines.extend(_caption_block(matter))
    lines.append("")
    lines.append("# VERIFICATION")
    lines.append("")
    lines.append(
        "I am the responding party (or its authorized officer) in the above-captioned "
        "matter. I have read the foregoing responses to interrogatories and know their "
        "contents."
    )
    lines.append("")
    lines.append(MN_VERIFICATION_DECLARATION)
    lines.append("")
    lines.append(f"Executed on ____________________, in the County of {matter.caption.county}, "
                 f"State of {matter.jurisdiction.state}.")
    lines.append("")
    lines.append("_______________________________")
    lines.append("Signature of responding party")
    lines.append("")
    lines.append("_(Unsigned — the client signs this verification on paper.)_")
    lines.append("")
    path = deliverables_dir(vault_root, run_id) / "verification.md"
    atomic_write_text(path, "\n".join(lines).rstrip("\n") + "\n")
    return path
