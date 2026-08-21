"""Export orchestration (plan Phase 7 / D3 M12): the one shared code path both the
``mootloop export`` CLI and the ``moot-export`` skill drive.

Always builds the markdown deliverables (master, per-set masters, verification page,
privilege log, strategy memo, audit log). Renders a DOCX per served set — as a DRAFT
(draft reference-doc + ``.DRAFT.docx`` suffix) unless the run is attested AND
``gate_ledger.export_ready`` is true AND the matter carries a signing attorney, in
which case the clean template is used and a residue scan must pass. A raw call cannot
produce an un-attested clean export: the watermark/attestation/residue enforcement
lives here, not in the CLI.

Each render also unlinks the *counterpart* file for its set (the ``.docx`` when
writing a ``.DRAFT.docx`` and vice versa). Without that, a clean DOCX from an earlier
export outlives the state that justified it: the run drifts, the next export
correctly waters the copy — and the stale clean file sits in ``deliverables/`` ready
to be served the moment the gate ledger goes green again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mootloop import attest, gate_ledger
from mootloop.context import load_run_context, load_run_corpus
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
from mootloop.vault import RunLock


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


def _retire_clean_docx(docx_dir: Path) -> list[Path]:
    """Delete every clean (non-DRAFT) DOCX under ``docx_dir``; return what was removed.

    Called whenever an export resolves to DRAFT. A clean DOCX is only ever justified by
    the state at the moment it was rendered; once the export waters the copy, any clean
    file left from an earlier export is stale work product that ``list_deliverables``
    would happily serve the next time the gate ledger goes green.
    """
    if not docx_dir.is_dir():
        return []
    removed: list[Path] = []
    for path in sorted(docx_dir.glob("*.docx")):
        if ".draft." in path.name.lower():
            continue
        path.unlink()
        removed.append(path)
    return removed


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
    with RunLock(vault_root, run_id):
        return _export_run_locked(
            vault_root,
            run_id,
            now,
            force_draft=force_draft,
            reference_doc=reference_doc,
        )


def _export_run_locked(
    vault_root: Path | str,
    run_id: str,
    now: str,
    *,
    force_draft: bool,
    reference_doc: str,
) -> ExportResult:
    """Generate and seal one export while the run lock prevents competing writers."""
    run_context = load_run_context(vault_root, run_id)
    load_run_corpus(vault_root, run_context)
    if attest.sealed_export_state(vault_root, run_id).status == "valid":
        existing = _existing_sealed_result(vault_root, run_id)
        if not force_draft:
            return existing
        return _render_draft_from_sealed(existing, reference_doc)
    prebuild_attestation = attest.attestation_state(vault_root, run_id)
    attested_master = attest.master_deliverable_path(
        vault_root, run_id, run_context=run_context
    )
    master = (
        attested_master
        if prebuild_attestation.status == "valid" and attested_master is not None
        else build_court_master(vault_root, run_id, now, run_context=run_context)
    )
    verification = build_verification_page(vault_root, run_id, now, run_context=run_context)
    privilege = build_privilege_log(vault_root, run_id, run_context=run_context)
    memo = build_strategy_memo(vault_root, run_id, now)
    audit = build_audit_log(vault_root, run_id, now)
    set_masters = build_set_masters(vault_root, run_id, now, run_context=run_context)

    ready, blockers = gate_ledger.export_ready(vault_root, run_id)
    attestation = attest.attestation_state(vault_root, run_id)
    # A matter with no attorney block renders a `[ATTORNEY NAME]` placeholder into the
    # signature line — scaffolding, never a servable signature. Water the copy rather
    # than emit an unsigned filing (the residue scan is only the backstop, and it does
    # not run at all when pandoc is absent).
    signed = run_context.manifest.matter_config.attorney is not None
    if not signed:
        blockers = [*blockers, "signature_block"]
    # Clean export requires a valid attestation AND a green gate ledger AND a signing
    # attorney; anything else (or an explicit --force-draft) waters the copy (plan
    # Phase 7). `qualifies_clean` is the state-only half: it is what decides whether an
    # earlier clean DOCX is still justified, which `--force-draft` must not disturb.
    qualifies_clean = attestation.status == "valid" and ready and signed
    is_draft = force_draft or not qualifies_clean

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

    docx_dir = deliverables_dir(vault_root, run_id) / "docx"
    # Retire stale clean copies BEFORE the pandoc check: a missing pandoc must not
    # leave a previously-rendered clean DOCX standing behind a now-DRAFT export.
    if not qualifies_clean:
        _retire_clean_docx(docx_dir)

    if not docx_render.pandoc_available():
        result.docx_skipped_reason = "pandoc not installed"
        if qualifies_clean:
            attest.record_export_seal_locked(vault_root, run_id, now)
        return result

    reference = reference_doc_path(reference_doc, draft=is_draft)
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

    if qualifies_clean and not result.residue_clean:
        _retire_clean_docx(docx_dir)
        result.is_draft = True
        result.export_ready = False
        return result

    if qualifies_clean:
        attest.record_export_seal_locked(vault_root, run_id, now)
    return result


def _existing_sealed_result(vault_root: Path | str, run_id: str) -> ExportResult:
    """Return the intact published generation without rewriting its sealed bytes."""
    root = deliverables_dir(vault_root, run_id)
    master = attest.master_deliverable_path(vault_root, run_id)
    if master is None:
        raise RuntimeError("sealed export is missing its master deliverable")
    verification_path = root / "verification.md"
    return ExportResult(
        run_id=run_id,
        master=master,
        verification=verification_path if verification_path.is_file() else None,
        privilege_log=root / "privilege-log.md",
        memo=root / "strategy-memo.md",
        audit_log=root / "audit-log.json",
        set_masters=sorted((root / "sets").glob("*.md")),
        docx=sorted(
            path
            for path in (root / "docx").glob("*.docx")
            if ".draft." not in path.name.lower()
        ),
        is_draft=False,
        export_ready=True,
        attestation_state="valid",
    )


def _render_draft_from_sealed(result: ExportResult, reference_doc: str) -> ExportResult:
    """Render optional DRAFT copies without changing the current sealed generation."""
    result.is_draft = True
    result.docx = []
    if not docx_render.pandoc_available():
        result.docx_skipped_reason = "pandoc not installed"
        return result
    reference = reference_doc_path(reference_doc, draft=True)
    docx_dir = result.master.parent / "docx"
    for source in result.set_masters:
        label = source.stem
        out_path = docx_dir / f"{label}.DRAFT.docx"
        docx_render.render_docx(source, out_path, reference, draft=True)
        scan = residue.scan_docx(out_path)
        result.residue_results.append((label, scan))
        result.docx.append(out_path)
    return result
