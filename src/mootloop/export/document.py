"""Complete, run-scoped document work product from the operative reviewed drafts."""

from __future__ import annotations

from pathlib import Path

from mootloop.context import RunContext, load_run_context
from mootloop.errors import ExportError
from mootloop.models.document_task import DocumentUnit
from mootloop.vault import atomic_write_text, safe_vault_path


def build_document_master(
    vault_root: Path | str,
    run_id: str,
    now: str,
    *,
    run_context: RunContext | None = None,
) -> Path:
    from mootloop.orchestrator import operative_drafts

    context = run_context or load_run_context(vault_root, run_id)
    if context.binding.config.input_family != "document":
        raise ExportError("document assembly requires a document task")
    lines = [
        f"# {context.manifest.task} — draft work product",
        "",
        "**DRAFT — attorney review required. Not approved for filing or reliance.**",
        "",
        f"_Generated {now} · run `{run_id}`_",
        "",
    ]
    for unit, draft in operative_drafts(vault_root, run_id):
        assert isinstance(unit, DocumentUnit)
        lines.extend([f"::: {{#resp-{unit.unit_id}}}", f"## {unit.title}", ""])
        lines.extend([draft.response_text if draft else "_no document drafted_", "", ":::", ""])
    path = safe_vault_path(vault_root, "deliverables", run_id, "master.md")
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")
    return path
