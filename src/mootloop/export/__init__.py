"""Deliverable-export package (plan Phase 7 / D7 / D8).

Builds the court-formatted master, the client verification page (rogs), the privilege
log, the strategy memo, and the AI-use audit log from a run's *operative* drafts and
its immutable ledgers — then renders DOCX via pandoc with a court reference-doc and a
residue scan (see ``docx_render``/``residue``). Every derived surface is written under
``deliverables/<run-id>/`` via ``safe_vault_path``; clean export is gated on
``gate_ledger.export_ready`` (the shared-core enforcement, plan D3 M12).
"""

from __future__ import annotations

from pathlib import Path

from mootloop.models.requests import RequestSet
from mootloop.vault import safe_vault_path


def deliverables_dir(vault_root: Path | str, run_id: str) -> Path:
    """The per-run deliverable directory (``deliverables/<run-id>/``)."""
    return safe_vault_path(vault_root, "deliverables", run_id)


def load_request_sets(vault_root: Path | str) -> list[RequestSet]:
    """Every parsed served-request set, ordered by (set number, request type)."""
    requests_dir = safe_vault_path(vault_root, "requests")
    out: list[RequestSet] = []
    if requests_dir.is_dir():
        for path in sorted(requests_dir.glob("*.json")):
            out.append(RequestSet.model_validate_json(path.read_text(encoding="utf-8")))
    out.sort(key=lambda s: (s.set_number, s.request_type.value))
    return out
