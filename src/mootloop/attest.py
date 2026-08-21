"""Attorney review commitments and linked clean-export seals (plan D9/H8/D11).

The human ``attest`` act binds the exact court master, citation ledger, run journal,
decisions, launch fact state, access-audit prefix, reviewer, and time in an append-only
v2 commitment. Clean export then appends a seal for the exact artifact set linked to
that commitment. Any committed evidence or sealed artifact drift re-imposes DRAFT.
Legacy records remain readable but cannot satisfy the current integrity contract.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mootloop.citations.ledger import LEDGER_PATH
from mootloop.context import RunContext, load_run_context, load_run_corpus
from mootloop.decisions import DecisionStore
from mootloop.errors import AttestationBlockedError, AuditWriteError, OrchestratorError
from mootloop.export import deliverables_dir
from mootloop.journal import journal_path, load_state
from mootloop.models.attestations import (
    ArtifactDigest,
    Attestation,
    AttestationCheckStatus,
    ExportSeal,
    ReviewIntegrityStatus,
)
from mootloop.models.common import RunId, canonical_json_sha256
from mootloop.models.rubric import sha256_hex
from mootloop.persistence import append_fsync_line, complete_jsonl_lines
from mootloop.vault import RunLock, load_matter, safe_vault_path
from mootloop.web import audit as access_audit

ATTESTATIONS_JSONL = ("attestations.jsonl",)
EXPORT_SEALS_JSONL = ("export-seals.jsonl",)
MASTER_HASH_SCOPE = "run-review-state:v2"
LEGACY_HASH_SCOPE_REASON = "legacy attestation hash scope is incompatible; re-attestation required"


# --- canonicalization + hashing ---------------------------------------------


def canonicalize(text: str) -> str:
    """Normalize line endings (CRLF/CR -> LF) and strip trailing whitespace per line,
    then collapse a trailing blank run to a single newline. A whitespace-only edit is
    therefore a no-op; a content edit is not (plan D9)."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).rstrip("\n") + "\n"


def master_deliverable_path(
    vault_root: Path | str, run_id: str, *, run_context: RunContext | None = None
) -> Path | None:
    """The court-formatted master reviewed by the attorney, if the run exists."""
    if load_state(vault_root, run_id).task is None:
        return None
    if run_context is None:
        load_run_context(vault_root, run_id)
    return deliverables_dir(vault_root, run_id) / "master.md"


def matter_sha256(
    vault_root: Path | str, run_id: str, *, run_context: RunContext | None = None
) -> str:
    """Digest the launch-snapshotted matter chrome that export actually renders."""
    context = run_context or load_run_context(vault_root, run_id)
    return sha256_hex(context.manifest.matter_config.model_dump_json())


def _live_matter_matches_launch(
    vault_root: Path | str, run_id: str, *, run_context: RunContext | None = None
) -> bool:
    context = run_context or load_run_context(vault_root, run_id)
    return load_matter(vault_root) == context.manifest.matter_config


def current_master_sha256(
    vault_root: Path | str, run_id: str, *, run_context: RunContext | None = None
) -> str | None:
    """The attested content hash — the canonicalized md-master bound to the matter
    chrome — or None if the deliverable is not written (see the module docstring for
    why the matter is part of it)."""
    context = run_context or load_run_context(vault_root, run_id)
    path = master_deliverable_path(vault_root, run_id, run_context=context)
    if path is None or not path.is_file():
        return None
    canonical = canonicalize(_read_regular_file(path).decode("utf-8"))
    return sha256_hex(
        f"{canonical}\x1f{matter_sha256(vault_root, run_id, run_context=context)}"
    )


def current_ledger_head_sha256(vault_root: Path | str) -> str:
    """The exact-byte digest of the append-only citation verification ledger."""
    path = safe_vault_path(vault_root, *LEDGER_PATH)
    if not path.is_file():
        return hashlib.sha256(b"").hexdigest()
    digest = hashlib.sha256()
    _hash_regular_file_into(path, digest)
    return digest.hexdigest()


class _Digest(Protocol):
    def update(self, data: bytes) -> object: ...


def _stable_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _read_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise AttestationBlockedError(f"cannot commit unsafe evidence path: {path}") from exc
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise AttestationBlockedError(f"cannot commit unsafe evidence path: {path}")
        payload = handle.read()
        after = os.fstat(handle.fileno())
    if _stable_identity(before) != _stable_identity(after) or len(payload) != before.st_size:
        raise AttestationBlockedError(f"evidence changed while reading: {path}")
    return payload


def _hash_regular_file_into(
    path: Path, digest: _Digest, *, include_size: bool = False
) -> int:
    """Hash one stable regular file through a no-follow descriptor and return its size."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise AttestationBlockedError(f"cannot commit unsafe evidence path: {path}") from exc
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise AttestationBlockedError(f"cannot commit unsafe evidence path: {path}")
        if include_size:
            digest.update(before.st_size.to_bytes(8, "big"))
        total = 0
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(handle.fileno())
    if total != before.st_size or _stable_identity(before) != _stable_identity(after):
        raise AttestationBlockedError(f"evidence changed while hashing: {path}")
    return total


def _tree_sha256(root: Path, paths: list[Path]) -> str:
    """Digest exact bytes and relative identities for a bounded file set."""
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        _hash_regular_file_into(path, digest, include_size=True)
    return digest.hexdigest()


def current_journal_sha256(vault_root: Path | str, run_id: str) -> str:
    """Commit the exact journal and write-once turn bodies that supply its evidence."""
    run_root = safe_vault_path(vault_root, "runs", run_id)
    paths = [journal_path(vault_root, run_id)]
    turns = safe_vault_path(vault_root, "runs", run_id, "turns")
    if turns.is_dir():
        paths.extend(path for path in turns.rglob("*") if path.is_file() or path.is_symlink())
    return _tree_sha256(run_root, [path for path in paths if path.exists()])


def current_decisions_sha256(vault_root: Path | str, run_id: str) -> str:
    """Commit the decision log and every immutable proposal sidecar."""
    run_root = safe_vault_path(vault_root, "runs", run_id)
    root = safe_vault_path(vault_root, "runs", run_id, "decisions")
    paths = (
        [path for path in root.rglob("*") if path.is_file() or path.is_symlink()]
        if root.is_dir()
        else []
    )
    return _tree_sha256(run_root, paths)


def current_fact_state_sha256(
    vault_root: Path | str, run_id: str, *, run_context: RunContext | None = None
) -> str:
    """Commit the immutable launch fact state actually available to this run."""
    context = run_context or load_run_context(vault_root, run_id)
    return canonical_json_sha256([fact.model_dump(mode="json") for fact in context.manifest.facts])


def current_access_audit_head_sha256(vault_root: Path | str) -> str:
    """Return the verified access-audit chain head (genesis when empty)."""
    try:
        return access_audit.head_sha256(vault_root)
    except AuditWriteError as exc:
        raise AttestationBlockedError("cannot attest with a corrupt access-audit chain") from exc


# --- store ------------------------------------------------------------------


def _attestations_path(vault_root: Path | str, run_id: str) -> Path:
    return safe_vault_path(vault_root, "runs", run_id, *ATTESTATIONS_JSONL)


def _latest_attestation_and_count(
    vault_root: Path | str, run_id: str
) -> tuple[Attestation | None, int]:
    path = _attestations_path(vault_root, run_id)
    latest: Attestation | None = None
    count = 0
    for line in complete_jsonl_lines(path):
        latest = Attestation.model_validate_json(line)
        count += 1
    return latest, count


def _append(vault_root: Path | str, run_id: str, record: Attestation) -> None:
    append_fsync_line(_attestations_path(vault_root, run_id), record.model_dump_json())


def latest_attestation(vault_root: Path | str, run_id: str) -> Attestation | None:
    latest, _ = _latest_attestation_and_count(vault_root, run_id)
    return latest


def _export_seals_path(vault_root: Path | str, run_id: str) -> Path:
    return safe_vault_path(vault_root, "runs", run_id, *EXPORT_SEALS_JSONL)


def _latest_export_seal_and_count(
    vault_root: Path | str, run_id: str
) -> tuple[ExportSeal | None, int]:
    path = _export_seals_path(vault_root, run_id)
    latest: ExportSeal | None = None
    count = 0
    for line in complete_jsonl_lines(path):
        latest = ExportSeal.model_validate_json(line)
        count += 1
    return latest, count


def latest_export_seal(vault_root: Path | str, run_id: str) -> ExportSeal | None:
    latest, _ = _latest_export_seal_and_count(vault_root, run_id)
    return latest


def _export_artifacts(vault_root: Path | str, run_id: str) -> list[ArtifactDigest]:
    root = deliverables_dir(vault_root, run_id)
    resolved_vault = Path(vault_root).resolve()
    if not root.is_dir():
        return []
    artifacts: list[ArtifactDigest] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AttestationBlockedError(f"cannot seal unsafe export path: {path}")
        if path.is_dir():
            continue
        if ".draft." in path.name.lower():
            continue
        digest = hashlib.sha256()
        size = _hash_regular_file_into(path, digest)
        artifacts.append(
            ArtifactDigest(
                path=path.relative_to(resolved_vault).as_posix(),
                sha256=digest.hexdigest(),
                size_bytes=size,
            )
        )
    return artifacts


def record_export_seal(vault_root: Path | str, run_id: str, now: str) -> ExportSeal:
    """Append an exact export manifest linked to the current attorney commitment."""
    with RunLock(vault_root, run_id):
        return record_export_seal_locked(vault_root, run_id, now)


def record_export_seal_locked(vault_root: Path | str, run_id: str, now: str) -> ExportSeal:
    """Append a seal while the caller holds the run lock across artifact generation."""
    latest, _ = _latest_attestation_and_count(vault_root, run_id)
    check = _attestation_state(vault_root, run_id, latest)
    if check.status != "valid" or latest is None or latest.commitment_sha256 is None:
        raise AttestationBlockedError("cannot seal exports without a valid v2 attestation")
    artifacts = _export_artifacts(vault_root, run_id)
    if not artifacts:
        raise AttestationBlockedError("cannot seal an empty export set")
    _, seal_count = _latest_export_seal_and_count(vault_root, run_id)
    seal = ExportSeal(
        seal_id=f"seal-{run_id}-{seal_count:04d}",
        run_id=RunId(run_id),
        attestation_id=latest.attestation_id,
        attestation_commitment_sha256=latest.commitment_sha256,
        sealed_at=now,
        artifacts=artifacts,
        export_set_sha256="",
    )
    seal = seal.model_copy(update={"export_set_sha256": seal.expected_export_set_sha256()})
    append_fsync_line(_export_seals_path(vault_root, run_id), seal.model_dump_json())
    return seal


# --- attest + check ---------------------------------------------------------


def attest(vault_root: Path | str, run_id: str, reviewer: str, now: str) -> Attestation:
    """Record an attestation. Refuses while any attorney-gate decision is open or the
    md-master deliverable does not exist (plan D9/H8)."""
    with RunLock(vault_root, run_id):
        context = load_run_context(vault_root, run_id)
        load_run_corpus(vault_root, context)
        if load_matter(vault_root) != context.manifest.matter_config:
            raise AttestationBlockedError(
                f"cannot attest run {run_id!r}: matter.yaml changed after launch; start a new run"
            )
        open_decisions = DecisionStore(vault_root, run_id).list_open()
        if open_decisions:
            ids = ", ".join(d.decision_id for d in open_decisions)
            raise AttestationBlockedError(
                f"cannot attest run {run_id!r}: {len(open_decisions)} open decision(s): {ids}"
            )
        from mootloop.export.master import build_court_master

        build_court_master(vault_root, run_id, now, run_context=context)
        master = current_master_sha256(vault_root, run_id, run_context=context)
        if master is None:
            raise AttestationBlockedError(
                f"cannot attest run {run_id!r}: the md-master deliverable does not exist"
            )
        _, seq = _latest_attestation_and_count(vault_root, run_id)
        record = Attestation(
            attestation_id=f"att-{run_id}-{seq:04d}",
            run_id=RunId(run_id),
            hash_scope=MASTER_HASH_SCOPE,
            master_sha256=master,
            ledger_head_sha256=current_ledger_head_sha256(vault_root),
            journal_sha256=current_journal_sha256(vault_root, run_id),
            decisions_sha256=current_decisions_sha256(vault_root, run_id),
            fact_state_sha256=current_fact_state_sha256(
                vault_root, run_id, run_context=context
            ),
            access_audit_head_sha256=current_access_audit_head_sha256(vault_root),
            commitment_sha256="",
            reviewer=reviewer,
            attested_at=now,
            valid=True,
        )
        record = record.model_copy(
            update={"commitment_sha256": record.expected_commitment_sha256()}
        )
        _append(vault_root, run_id, record)
    return record


@dataclass(frozen=True)
class AttestationCheck:
    """The result of checking an attestation against the current bytes."""

    status: AttestationCheckStatus
    reason: str | None = None


def attestation_state(vault_root: Path | str, run_id: str) -> AttestationCheck:
    """Pure check (no writes): compare the latest *valid* attestation to current bytes.
    Returns ``missing`` (never attested / last record already invalid), ``invalidated``
    (a hash drifted), or ``valid``."""
    latest, _ = _latest_attestation_and_count(vault_root, run_id)
    return _attestation_state(vault_root, run_id, latest)


def sealed_export_state(vault_root: Path | str, run_id: str) -> AttestationCheck:
    """Validate the attorney commitment and require an intact linked export seal."""
    latest, _ = _latest_attestation_and_count(vault_root, run_id)
    return _attestation_state(vault_root, run_id, latest, require_export_seal=True)


def review_integrity_status(vault_root: Path | str, run_id: str) -> ReviewIntegrityStatus:
    """Return the exact current commitment and seal state without writing invalidations."""
    latest_attestation_record, _ = _latest_attestation_and_count(vault_root, run_id)
    latest_seal_record, _ = _latest_export_seal_and_count(vault_root, run_id)
    attorney = _attestation_state(vault_root, run_id, latest_attestation_record)
    sealed = (
        _export_seal_state(vault_root, run_id, latest_attestation_record, latest_seal_record)
        if attorney.status == "valid" and latest_attestation_record is not None
        else attorney
    )
    return ReviewIntegrityStatus(
        run_id=RunId(run_id),
        attestation_status=attorney.status,
        attestation_reason=attorney.reason,
        export_seal_status=sealed.status,
        export_seal_reason=sealed.reason,
        latest_attestation=latest_attestation_record,
        latest_export_seal=latest_seal_record,
    )


def _attestation_state(
    vault_root: Path | str,
    run_id: str,
    latest: Attestation | None,
    *,
    require_export_seal: bool = False,
) -> AttestationCheck:
    """Validate one already-loaded latest attestation against current evidence."""
    if latest is None:
        return AttestationCheck("missing")
    if not latest.valid:
        return AttestationCheck("invalidated", latest.reason)
    if latest.hash_scope != MASTER_HASH_SCOPE:
        return AttestationCheck("invalidated", LEGACY_HASH_SCOPE_REASON)
    if latest.commitment_sha256 != latest.expected_commitment_sha256():
        return AttestationCheck("invalidated", "attestation commitment digest changed")
    context = load_run_context(vault_root, run_id)
    if not _live_matter_matches_launch(vault_root, run_id, run_context=context):
        return AttestationCheck("invalidated", "matter.yaml changed after launch; start a new run")
    master = current_master_sha256(vault_root, run_id, run_context=context)
    if master != latest.master_sha256:
        return AttestationCheck("invalidated", "md-master or matter.yaml changed after attestation")
    if current_ledger_head_sha256(vault_root) != latest.ledger_head_sha256:
        return AttestationCheck("invalidated", "citation ledger changed after attestation")
    if current_journal_sha256(vault_root, run_id) != latest.journal_sha256:
        return AttestationCheck("invalidated", "journal evidence changed after attestation")
    if current_decisions_sha256(vault_root, run_id) != latest.decisions_sha256:
        return AttestationCheck("invalidated", "decision evidence changed after attestation")
    if (
        current_fact_state_sha256(vault_root, run_id, run_context=context)
        != latest.fact_state_sha256
    ):
        return AttestationCheck("invalidated", "launch fact state changed after attestation")
    if latest.access_audit_head_sha256 is None or not access_audit.contains_intact_head(
        vault_root, latest.access_audit_head_sha256
    ):
        return AttestationCheck("invalidated", "access audit changed before its attested head")
    if require_export_seal:
        return _export_seal_state(
            vault_root,
            run_id,
            latest,
            latest_export_seal(vault_root, run_id),
        )
    return AttestationCheck("valid")


def _export_seal_state(
    vault_root: Path | str,
    run_id: str,
    attestation: Attestation,
    seal: ExportSeal | None,
) -> AttestationCheck:
    """Validate only the exact export-set link after its attorney commitment is valid."""
    if seal is None:
        return AttestationCheck("invalidated", "clean export has no export seal")
    if (
        seal.attestation_id != attestation.attestation_id
        or seal.attestation_commitment_sha256 != attestation.commitment_sha256
        or seal.export_set_sha256 != seal.expected_export_set_sha256()
        or _export_artifacts(vault_root, run_id) != seal.artifacts
    ):
        return AttestationCheck("invalidated", "sealed export set changed")
    return AttestationCheck("valid")


def latest_seal_contains(vault_root: Path | str, run_id: str, name: str) -> bool:
    """Whether the latest seal claims one run-relative deliverable path."""
    seal = latest_export_seal(vault_root, run_id)
    expected = f"deliverables/{run_id}/{name}"
    return seal is not None and any(artifact.path == expected for artifact in seal.artifacts)


def check_attestation(vault_root: Path | str, run_id: str, now: str) -> AttestationCheck:
    """Like ``attestation_state``, but records an invalidation event when a previously-
    valid attestation no longer matches (append-only; re-imposes DRAFT, plan D9)."""
    with RunLock(vault_root, run_id):
        latest, seq = _latest_attestation_and_count(vault_root, run_id)
        check = _attestation_state(vault_root, run_id, latest)
        if check.status == "invalidated" and latest is not None and latest.valid:
            context = load_run_context(vault_root, run_id)
            invalidation = Attestation(
                attestation_id=f"att-{run_id}-{seq:04d}",
                run_id=RunId(run_id),
                hash_scope=MASTER_HASH_SCOPE,
                master_sha256=current_master_sha256(
                    vault_root, run_id, run_context=context
                )
                or "",
                ledger_head_sha256=current_ledger_head_sha256(vault_root),
                journal_sha256=current_journal_sha256(vault_root, run_id),
                decisions_sha256=current_decisions_sha256(vault_root, run_id),
                fact_state_sha256=current_fact_state_sha256(
                    vault_root, run_id, run_context=context
                ),
                access_audit_head_sha256=current_access_audit_head_sha256(vault_root),
                commitment_sha256="",
                reviewer="system",
                attested_at=now,
                valid=False,
                reason=check.reason,
            )
            invalidation = invalidation.model_copy(
                update={"commitment_sha256": invalidation.expected_commitment_sha256()}
            )
            _append(vault_root, run_id, invalidation)
    return check


def require_run(vault_root: Path | str, run_id: str) -> None:
    """Guard: the run must exist (has a RunStarted event)."""
    if load_state(vault_root, run_id).task is None:
        raise OrchestratorError(f"run {run_id!r} has no RunStarted event")
