"""Export orchestration (plan Phase 7 / D3 M12): the one shared code path both the
``mootloop export`` CLI and the ``moot-export`` skill drive.

Always builds the markdown deliverables (master, per-set masters, verification page,
privilege log, strategy memo, audit log). Renders a DOCX per served set — as a DRAFT
(draft reference-doc + ``.DRAFT.docx`` suffix) unless the run is attested AND
``gate_ledger.export_ready`` is true, in which case the clean template is used and a
residue scan must pass. A raw call cannot produce an un-attested clean export: the
watermark/attestation/residue enforcement lives here, not in the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mootloop import attest, gate_ledger
from mootloop.export import deliverables_dir, docx_render, residue
from mootloop.export.audit import build_audit_log
from mootloop.export.master import (
    build_court_master,
    build_set_masters,
    build_verification_page,
)
from mootloop.export.memo import build_strategy_memo
from mootloop.export.privilege_log import build_privilege_log
from mootloop.models.gates import GateResult
from mootloop.resources import DEFAULT_REFERENCE_DOC, reference_doc_path


@dataclass
class ExportResult:
    """What an export produced, and what (if anything) blocked a clean export."""

    run_id: str
    master: Path
    verification: Path | None
    privilege_log: Path
    memo: Path
    audit_log: Path
    set_masters: list[Path] = field(default_factory=list)
    docx: list[Path] = field(default_factory=list)
    is_draft: bool = True
    export_ready: bool = False
    blockers: list[str] = field(default_factory=list)
    attestation_state: str = "missing"
    docx_skipped_reason: str | None = None
    residue_results: list[tuple[str, GateResult]] = field(default_factory=list)

    @property
    def residue_clean(self) -> bool:
        return all(result.status == "pass" for _, result in self.residue_results)


def export_run(
    vault_root: Path | str,
    run_id: str,
    now: str,
    *,
    force_draft: bool = False,
    reference_doc: str = DEFAULT_REFERENCE_DOC,
) -> ExportResult:
    """Build every deliverable and render per-set DOCX (draft or clean). The markdown
    deliverables are always produced; the DOCX watermark/clean decision is enforced
    here (plan D3 M12)."""
    master = build_court_master(vault_root, run_id, now)
    verification = build_verification_page(vault_root, run_id, now)
    privilege = build_privilege_log(vault_root, run_id)
    memo = build_strategy_memo(vault_root, run_id, now)
    audit = build_audit_log(vault_root, run_id, now)
    set_masters = build_set_masters(vault_root, run_id, now)

    ready, blockers = gate_ledger.export_ready(vault_root, run_id)
    attestation = attest.attestation_state(vault_root, run_id)
    # Clean export requires a valid attestation AND a green gate ledger; anything else
    # (or an explicit --force-draft) waters the copy (plan Phase 7).
    is_draft = force_draft or attestation.status != "valid" or not ready

    result = ExportResult(
        run_id=run_id,
        master=master,
        verification=verification,
        privilege_log=privilege,
        memo=memo,
        audit_log=audit,
        set_masters=[p for _, p in set_masters],
        is_draft=is_draft,
        export_ready=ready,
        blockers=blockers,
        attestation_state=attestation.status,
    )

    if not docx_render.pandoc_available():
        result.docx_skipped_reason = "pandoc not installed"
        return result

    reference = reference_doc_path(reference_doc, draft=is_draft)
    docx_dir = deliverables_dir(vault_root, run_id) / "docx"
    for label, source in set_masters:
        suffix = ".DRAFT.docx" if is_draft else ".docx"
        out_path = docx_dir / f"{label}{suffix}"
        docx_render.render_docx(source, out_path, reference, draft=is_draft)
        scan = residue.scan_docx(out_path)
        result.residue_results.append((label, scan))
        if scan.status != "pass" and not is_draft:
            # A clean export must be residue-clean: drop the offending file.
            out_path.unlink(missing_ok=True)
            result.blockers.append(f"residue:{label}")
            continue
        result.docx.append(out_path)

    return result
