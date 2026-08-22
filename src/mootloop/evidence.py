"""Generate content-free trace trees and immutable numbered run evidence packs."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from mootloop.context import load_run_context
from mootloop.errors import OrchestratorError
from mootloop.journal import journal_path, read_events
from mootloop.models.common import canonical_json_sha256
from mootloop.models.evidence import (
    EvidenceCommitment,
    RunEvidencePack,
    TraceNode,
    TraceTree,
)
from mootloop.persistence import sha256_file
from mootloop.vault import RunLock, atomic_write_text, safe_vault_path

TRACE_SUBPATH = ("evidence", "trace-tree.json")
PACKS_SUBPATH = ("evidence", "packs")


def trace_tree_path(vault_root: Path | str, run_id: str) -> Path:
    return safe_vault_path(vault_root, "runs", run_id, *TRACE_SUBPATH)


def evidence_pack_path(vault_root: Path | str, run_id: str, sequence: int) -> Path:
    if not 1 <= sequence <= 999:
        raise OrchestratorError("evidence-pack sequence must be between 1 and 999")
    name = f"EP-mootloop-{run_id}-{sequence:03d}.json"
    return safe_vault_path(vault_root, "runs", run_id, *PACKS_SUBPATH, name)


def _build_trace_tree(vault_root: Path | str, run_id: str, now: str) -> TraceTree:
    context = load_run_context(vault_root, run_id)
    events = read_events(vault_root, run_id)
    if not events:
        raise OrchestratorError(f"run {run_id!r} has no journal events")
    journal = journal_path(vault_root, run_id)
    raw_lines = [line for line in journal.read_bytes().splitlines(keepends=True) if line.strip()]
    if len(raw_lines) != len(events) or any(not line.endswith(b"\n") for line in raw_lines):
        raise OrchestratorError(f"run {run_id!r} journal bytes do not match parsed events")
    journal_rel = f"runs/{run_id}/journal.jsonl"
    root_id = f"trace:{run_id}:run"
    nodes = [
        TraceNode(
            node_id=root_id,
            kind="run",
            sequence=0,
            source_path=journal_rel,
            source_sha256=sha256_file(journal),
        )
    ]
    for sequence, (event, raw_line) in enumerate(zip(events, raw_lines, strict=True), start=1):
        nodes.append(
            TraceNode(
                node_id=f"trace:{run_id}:event:{sequence:06d}",
                parent_id=root_id,
                kind=event.kind,
                sequence=sequence,
                source_path=f"{journal_rel}#line={sequence}",
                source_sha256=hashlib.sha256(raw_line).hexdigest(),
            )
        )
    payload = {
        "schema_version": "1.0",
        "source_matter_id": str(context.manifest.matter_id),
        "run_id": run_id,
        "generated_at": now,
        "nodes": [node.model_dump(mode="json") for node in nodes],
    }
    return TraceTree.model_validate(
        {**payload, "tree_sha256": canonical_json_sha256(payload)}
    )


def load_trace_tree(vault_root: Path | str, run_id: str) -> TraceTree:
    context = load_run_context(vault_root, run_id)
    path = trace_tree_path(vault_root, run_id)
    if not path.is_file():
        raise OrchestratorError(
            f"run {run_id!r} has no trace tree; build an evidence pack first"
        )
    try:
        tree = TraceTree.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        raise OrchestratorError(f"run {run_id!r} has an invalid trace tree") from exc
    if tree.tree_sha256 != tree.expected_tree_sha256():
        raise OrchestratorError(f"run {run_id!r} trace-tree commitment is invalid")
    if tree.run_id != run_id or tree.source_matter_id != context.manifest.matter_id:
        raise OrchestratorError(f"run {run_id!r} trace tree belongs to another run or matter")
    return tree


_FIXED_COMMITMENTS = (
    ("journal", "runs/{run_id}/journal.jsonl"),
    ("context_manifest", "runs/{run_id}/context/manifest.json"),
    ("decisions", "runs/{run_id}/decisions/decisions.jsonl"),
    ("attestations", "runs/{run_id}/attestations.jsonl"),
    ("export_seals", "runs/{run_id}/export-seals.jsonl"),
    ("citation_ledger", "law/verifications.jsonl"),
    ("proposition_ledger", "law/proposition-verifications.jsonl"),
    ("access_audit", "audit/access.jsonl"),
)


def _commitments(vault_root: Path | str, run_id: str) -> tuple[EvidenceCommitment, ...]:
    commitments: list[EvidenceCommitment] = []
    for kind, template in _FIXED_COMMITMENTS:
        rel = template.format(run_id=run_id)
        path = safe_vault_path(vault_root, *rel.split("/"))
        if not path.is_file():
            continue
        commitments.append(
            EvidenceCommitment(
                kind=kind,
                path=rel,
                sha256=sha256_file(path),
                size_bytes=path.stat().st_size,
            )
        )
    return tuple(commitments)


def _next_sequence(vault_root: Path | str, run_id: str) -> int:
    sequences = [pack.sequence for pack in list_evidence_packs(vault_root, run_id)]
    sequence = max(sequences, default=0) + 1
    if sequence > 999:
        raise OrchestratorError(f"run {run_id!r} exhausted its evidence-pack sequence")
    return sequence


def build_evidence_pack(
    vault_root: Path | str,
    run_id: str,
    now: str,
    *,
    generated_by: str,
    channel: str,
) -> RunEvidencePack:
    """Persist the exact trace view and next numbered evidence commitment under a run lock."""
    if not generated_by.strip() or channel not in {"api", "cli"}:
        raise OrchestratorError("evidence generation requires a trusted actor and channel")
    with RunLock(vault_root, run_id):
        context = load_run_context(vault_root, run_id)
        tree = _build_trace_tree(vault_root, run_id, now)
        atomic_write_text(
            trace_tree_path(vault_root, run_id), tree.model_dump_json(indent=2) + "\n"
        )
        sequence = _next_sequence(vault_root, run_id)
        payload = {
            "schema_version": "1.0",
            "evidence_pack_id": f"EP-mootloop-{run_id}-{sequence:03d}",
            "source_matter_id": str(context.manifest.matter_id),
            "run_id": run_id,
            "sequence": sequence,
            "created_at": now,
            "generated_by": generated_by.strip(),
            "channel": channel,
            "trace_tree_sha256": tree.tree_sha256,
            "trace_tree": tree.model_dump(mode="json"),
            "commitments": [
                item.model_dump(mode="json") for item in _commitments(vault_root, run_id)
            ],
        }
        pack = RunEvidencePack.model_validate(
            {**payload, "pack_sha256": canonical_json_sha256(payload)}
        )
        atomic_write_text(
            evidence_pack_path(vault_root, run_id, sequence),
            pack.model_dump_json(indent=2) + "\n",
        )
        return pack


def list_evidence_packs(vault_root: Path | str, run_id: str) -> list[RunEvidencePack]:
    load_run_context(vault_root, run_id)
    root = safe_vault_path(vault_root, "runs", run_id, *PACKS_SUBPATH)
    if not root.is_dir():
        return []
    packs: list[RunEvidencePack] = []
    for discovered in sorted(root.glob("EP-mootloop-*.json")):
        path = safe_vault_path(
            vault_root,
            "runs",
            run_id,
            *PACKS_SUBPATH,
            discovered.name,
        )
        try:
            pack = RunEvidencePack.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError) as exc:
            raise OrchestratorError(f"invalid evidence pack {path.name!r}") from exc
        expected_name = f"{pack.evidence_pack_id}.json"
        if (
            path.name != expected_name
            or pack.run_id != run_id
            or pack.pack_sha256 != pack.expected_pack_sha256()
        ):
            raise OrchestratorError(f"evidence pack {path.name!r} failed its commitment")
        packs.append(pack)
    return packs
