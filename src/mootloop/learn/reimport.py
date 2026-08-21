"""Exact edited-DOCX ingestion into durable, review-only learning proposals."""

from __future__ import annotations

from pathlib import Path

from mootloop.context import load_run_context
from mootloop.errors import LearningImportError
from mootloop.learn.diff import baseline_anchors
from mootloop.learn.docx import parse_docx_edits_bytes, read_docx_source
from mootloop.learn.routing import LearningStore
from mootloop.learn.tagging import propose_anchored_learning
from mootloop.models.common import LearningImportId, RunId, canonical_json_sha256
from mootloop.models.learnings import (
    LearningImportBundle,
    LearningImportRecord,
    LearningImportResult,
    LearningProposal,
)
from mootloop.vault import RunLock


def import_docx_learning(
    vault_root: Path | str,
    run_id: str,
    source_path: Path | str,
    *,
    imported_at: str,
    source_name: str | None = None,
) -> LearningImportResult:
    """Recover one edited DOCX and publish deterministic, review-only proposals."""
    source = read_docx_source(Path(source_path))
    return import_docx_learning_bytes(
        vault_root,
        run_id,
        source,
        imported_at=imported_at,
        source_name=source_name or Path(source_path).name,
    )


def import_docx_learning_bytes(
    vault_root: Path | str,
    run_id: str,
    source: bytes,
    *,
    imported_at: str,
    source_name: str,
) -> LearningImportResult:
    """Import caller-owned bytes, retaining the exact confidential source in-vault."""
    context = load_run_context(vault_root, run_id)
    expected = tuple(f"resp-{unit.request_id}" for unit in context.units)
    recovery = parse_docx_edits_bytes(source, expected_anchors=expected)
    if not source_name or Path(source_name).name != source_name or any(
        char in source_name for char in "\r\n\x00"
    ):
        raise LearningImportError("source_name must be a plain filename")
    import_digest = canonical_json_sha256(
        {
            "matter": str(context.manifest.matter_id),
            "run": run_id,
            "source": recovery.source_sha256,
        }
    )
    import_id = LearningImportId(f"learning-import-{import_digest[:16]}")
    with RunLock(vault_root, run_id):
        store = LearningStore(vault_root)
        existing = store.load_bundle(str(import_id))
        if existing is not None:
            record = existing.import_record
            if (
                record.import_id != import_id
                or record.source_matter_id != context.manifest.matter_id
                or record.run_id != RunId(run_id)
                or record.source_sha256 != recovery.source_sha256
            ):
                raise LearningImportError("learning import identity conflicts with stored source")
            store.publish_source(str(import_id), source, recovery.source_sha256)
            published = existing
        else:
            blockers = list(recovery.blockers)
            baseline = baseline_anchors(vault_root, run_id)
            for expected_anchor in expected:
                if expected_anchor not in baseline:
                    blockers.append(f"baseline anchor {expected_anchor!r} is missing")
            auto_routable = recovery.auto_routable and not blockers
            record = LearningImportRecord(
                import_id=import_id,
                source_matter_id=context.manifest.matter_id,
                run_id=RunId(run_id),
                source_name=source_name,
                source_sha256=recovery.source_sha256,
                imported_at=imported_at,
                auto_routable=auto_routable,
                blockers=tuple(blockers),
                anchors=recovery.anchors,
            )
            proposals: list[LearningProposal] = []
            if auto_routable:
                for recovered in recovery.anchors:
                    proposal = propose_anchored_learning(
                        recovered,
                        baseline_text=baseline[recovered.anchor_id],
                        import_id=import_id,
                        matter_id=context.manifest.matter_id,
                        run_id=RunId(run_id),
                        task=context.manifest.task,
                        created_at=imported_at,
                    )
                    if proposal is not None:
                        proposals.append(proposal)
            bundle = LearningImportBundle(import_record=record, proposals=proposals)
            store.publish_source(str(import_id), source, recovery.source_sha256)
            published = store.publish(bundle)
    views_by_id = {
        str(item.proposal_id): item for item in LearningStore(vault_root).list_all()
    }
    return LearningImportResult(
        import_record=published.import_record,
        proposals=[views_by_id[str(item.proposal_id)] for item in published.proposals],
    )
