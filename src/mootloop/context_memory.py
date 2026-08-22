"""Human-approved matter context.md plus its exact machine provenance sidecar."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from mootloop.errors import OrchestratorError
from mootloop.models.common import MatterId
from mootloop.models.context import ContextContribution, MatterContextMemory
from mootloop.vault import atomic_write_text, load_matter, safe_vault_path

MAX_CONTEXT_BYTES = 256 * 1024


def context_markdown_path(vault_root: Path | str) -> Path:
    return safe_vault_path(vault_root, "context.md")


def context_sidecar_path(vault_root: Path | str) -> Path:
    return safe_vault_path(vault_root, "context.json")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def set_context_memory(
    vault_root: Path | str,
    text: str,
    *,
    approved_by: str,
    approved_at: str,
) -> MatterContextMemory:
    """Publish context.md first and its approving sidecar last; mismatch fails closed."""
    if not text.strip():
        raise OrchestratorError("context.md cannot be empty")
    normalized = text if text.endswith("\n") else text + "\n"
    if len(normalized.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise OrchestratorError("context.md exceeds the 256 KiB limit")
    if not approved_by.strip():
        raise OrchestratorError("context approval requires a trusted human actor")
    matter = load_matter(vault_root)
    metadata = MatterContextMemory(
        source_matter_id=MatterId(matter.matter_id),
        content_sha256=_digest(normalized),
        approved_by=approved_by.strip(),
        approved_at=approved_at,
    )
    atomic_write_text(context_markdown_path(vault_root), normalized)
    atomic_write_text(
        context_sidecar_path(vault_root), metadata.model_dump_json(indent=2) + "\n"
    )
    return metadata


def load_context_memory(
    vault_root: Path | str,
) -> tuple[str, MatterContextMemory] | None:
    markdown_path = context_markdown_path(vault_root)
    sidecar_path = context_sidecar_path(vault_root)
    if not markdown_path.exists() and not sidecar_path.exists():
        return None
    if not markdown_path.is_file() or not sidecar_path.is_file():
        raise OrchestratorError("context.md and context.json must both exist")
    try:
        raw = markdown_path.read_bytes()
        if len(raw) > MAX_CONTEXT_BYTES:
            raise OrchestratorError("context.md exceeds the 256 KiB limit")
        text = raw.decode("utf-8")
        metadata = MatterContextMemory.model_validate_json(
            sidecar_path.read_text(encoding="utf-8")
        )
    except OrchestratorError:
        raise
    except (OSError, UnicodeError, ValidationError) as exc:
        raise OrchestratorError("context.md provenance is unreadable or invalid") from exc
    matter = load_matter(vault_root)
    if metadata.source_matter_id != matter.matter_id:
        raise OrchestratorError("context.md provenance belongs to a different matter")
    if metadata.content_sha256 != hashlib.sha256(raw).hexdigest():
        raise OrchestratorError("context.md changed after human approval")
    return text, metadata


def context_memory_contribution(vault_root: Path | str) -> ContextContribution | None:
    loaded = load_context_memory(vault_root)
    if loaded is None:
        return None
    text, metadata = loaded
    return ContextContribution(
        contribution_id=f"context-memory-{metadata.content_sha256[:16]}",
        kind="context_note",
        text=text,
        sha256=metadata.content_sha256,
        provenance_locator="context.md",
        source_matter_id=metadata.source_matter_id,
        permission="privileged",
        approval_state="approved",
        sharing_scope="matter",
    )
