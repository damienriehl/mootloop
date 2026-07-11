"""Privilege log (plan D7): the functional-standard per-document log.

Rows come from the corpus manifest's privileged documents plus any resolved
``privilege_call`` attorney-gate decisions. Fields follow the D7 default set (doc id,
date, author, recipients, type, privilege-safe description, privilege asserted, basis).
Descriptions are drawn from `CorpusDoc` metadata and kept privilege-safe — document
*content* is never quoted (plan C2/H6).
"""

from __future__ import annotations

from pathlib import Path

from mootloop.decisions import DecisionStore
from mootloop.export import deliverables_dir
from mootloop.models.corpus import CorpusDoc, Manifest
from mootloop.models.decisions import DecisionKind
from mootloop.vault import atomic_write_text

_NOT_RECORDED = "not recorded"


def _doc_type(doc: CorpusDoc) -> str:
    return doc.role.value if doc.role is not None else doc.media_type


def _description(doc: CorpusDoc) -> str:
    """A privilege-safe description from metadata only (role + document kind) — never
    the document's content."""
    role = doc.role.value if doc.role is not None else "document"
    return f"{role.replace('-', ' ')} withheld as privileged"


def _row(doc_id: str, date: str, author: str, recipients: str, doc_type: str,
         description: str, asserted: str, basis: str) -> str:
    cells = [doc_id, date, author, recipients, doc_type, description, asserted, basis]
    return "| " + " | ".join(cells) + " |"


def build_privilege_log(vault_root: Path | str, run_id: str) -> Path:
    """Write ``deliverables/<run-id>/privilege-log.md`` and return its path."""
    manifest = Manifest.load(vault_root)
    privileged = [d for d in manifest.docs if d.privileged is True]
    resolved_calls = {
        str(d.request_id): d
        for d in DecisionStore(vault_root, run_id).list_all()
        if d.kind is DecisionKind.PRIVILEGE_CALL and d.resolution is not None
    }

    lines = [
        "# Privilege Log",
        "",
        "Withheld or redacted materials, logged per the functional standard of "
        "Minn. R. Civ. P. 26.02(f) / Fed. R. Civ. P. 26(b)(5)(A). Descriptions are "
        "drawn from document metadata and do not disclose privileged content.",
        "",
        _row(
            "Doc ID", "Date", "Author", "Recipient(s)", "Type",
            "Description", "Privilege Asserted", "Basis",
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    if not privileged:
        lines.append(
            "| _none_ | | | | | No documents marked privileged in the manifest. | | |"
        )
    for doc in privileged:
        lines.append(
            _row(
                f"`{doc.doc_id}`",
                doc.ingested_at.split("T", 1)[0],
                _NOT_RECORDED,
                _NOT_RECORDED,
                _doc_type(doc),
                _description(doc),
                "Attorney-Client / Work Product",
                "Minn. R. Civ. P. 26.02(f)",
            )
        )

    if resolved_calls:
        lines.append("")
        lines.append("## Privilege calls recorded this run")
        lines.append("")
        for request_id, decision in sorted(resolved_calls.items()):
            assert decision.resolution is not None
            lines.append(
                f"- `{request_id}` — {decision.resolution.action} "
                f"({decision.resolution.chosen_key or 'n/a'}) by "
                f"{decision.resolution.decided_by}"
            )

    path = deliverables_dir(vault_root, run_id) / "privilege-log.md"
    atomic_write_text(path, "\n".join(lines).rstrip("\n") + "\n")
    return path
