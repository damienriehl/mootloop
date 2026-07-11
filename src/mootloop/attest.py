"""Attestation service (plan D9/H8/D11): the ``attest`` verb and its check.

``attest`` refuses unless every attorney-gate decision is resolved and the md-master
deliverable exists; it then records the canonicalized master hash + the citation-
ledger head hash, append-only, in ``runs/<run-id>/attestations.jsonl``.
``check_attestation`` recomputes those hashes and, on a mismatch, appends an
invalidation record (re-imposing DRAFT). Export reads attestation state; it never
sets it — the gate ledger is the single export gate.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from mootloop.citations.ledger import LEDGER_PATH
from mootloop.decisions import DecisionStore
from mootloop.errors import AttestationBlockedError, OrchestratorError
from mootloop.journal import load_state
from mootloop.models.attestations import Attestation, AttestationCheckStatus
from mootloop.tasks import get_binding
from mootloop.vault import RunLock, safe_vault_path

ATTESTATIONS_JSONL = ("attestations.jsonl",)


# --- canonicalization + hashing ---------------------------------------------


def canonicalize(text: str) -> str:
    """Normalize line endings (CRLF/CR -> LF) and strip trailing whitespace per line,
    then collapse a trailing blank run to a single newline. A whitespace-only edit is
    therefore a no-op; a content edit is not (plan D9)."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).rstrip("\n") + "\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def master_deliverable_path(vault_root: Path | str, run_id: str) -> Path | None:
    """The run's md-master deliverable path, or None if the run/task is unknown."""
    state = load_state(vault_root, run_id)
    if state.task is None:
        return None
    binding = get_binding(state.task)
    if not binding.config.deliverables:
        return None
    return safe_vault_path(vault_root, "deliverables", binding.config.deliverables[0])


def current_master_sha256(vault_root: Path | str, run_id: str) -> str | None:
    """The canonicalized md-master hash, or None if the deliverable is not written."""
    path = master_deliverable_path(vault_root, run_id)
    if path is None or not path.is_file():
        return None
    return _sha256(canonicalize(path.read_text(encoding="utf-8")))


def current_ledger_head_sha256(vault_root: Path | str) -> str:
    """The hash of the citation ledger's last line (``sha256("")`` if empty/absent)."""
    path = safe_vault_path(vault_root, *LEDGER_PATH)
    head = ""
    if path.is_file():
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            head = lines[-1]
    return _sha256(head)


# --- store ------------------------------------------------------------------


def _attestations_path(vault_root: Path | str, run_id: str) -> Path:
    return safe_vault_path(vault_root, "runs", run_id, *ATTESTATIONS_JSONL)


def _records(vault_root: Path | str, run_id: str) -> list[Attestation]:
    path = _attestations_path(vault_root, run_id)
    if not path.is_file():
        return []
    out: list[Attestation] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(Attestation.model_validate_json(line))
    return out


def _append(vault_root: Path | str, run_id: str, record: Attestation) -> None:
    path = _attestations_path(vault_root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def latest_attestation(vault_root: Path | str, run_id: str) -> Attestation | None:
    records = _records(vault_root, run_id)
    return records[-1] if records else None


# --- attest + check ---------------------------------------------------------


def attest(vault_root: Path | str, run_id: str, reviewer: str, now: str) -> Attestation:
    """Record an attestation. Refuses while any attorney-gate decision is open or the
    md-master deliverable does not exist (plan D9/H8)."""
    with RunLock(vault_root, run_id):
        open_decisions = DecisionStore(vault_root, run_id).list_open()
        if open_decisions:
            ids = ", ".join(d.decision_id for d in open_decisions)
            raise AttestationBlockedError(
                f"cannot attest run {run_id!r}: {len(open_decisions)} open decision(s): {ids}"
            )
        master = current_master_sha256(vault_root, run_id)
        if master is None:
            raise AttestationBlockedError(
                f"cannot attest run {run_id!r}: the md-master deliverable does not exist"
            )
        seq = len(_records(vault_root, run_id))
        record = Attestation(
            attestation_id=f"att-{run_id}-{seq:04d}",
            run_id=run_id,
            master_sha256=master,
            ledger_head_sha256=current_ledger_head_sha256(vault_root),
            reviewer=reviewer,
            attested_at=now,
            valid=True,
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
    latest = latest_attestation(vault_root, run_id)
    if latest is None:
        return AttestationCheck("missing")
    if not latest.valid:
        return AttestationCheck("invalidated", latest.reason)
    master = current_master_sha256(vault_root, run_id)
    if master != latest.master_sha256:
        return AttestationCheck("invalidated", "md-master changed after attestation")
    if current_ledger_head_sha256(vault_root) != latest.ledger_head_sha256:
        return AttestationCheck("invalidated", "citation ledger changed after attestation")
    return AttestationCheck("valid")


def check_attestation(vault_root: Path | str, run_id: str, now: str) -> AttestationCheck:
    """Like ``attestation_state``, but records an invalidation event when a previously-
    valid attestation no longer matches (append-only; re-imposes DRAFT, plan D9)."""
    check = attestation_state(vault_root, run_id)
    latest = latest_attestation(vault_root, run_id)
    if check.status == "invalidated" and latest is not None and latest.valid:
        with RunLock(vault_root, run_id):
            seq = len(_records(vault_root, run_id))
            _append(
                vault_root,
                run_id,
                Attestation(
                    attestation_id=f"att-{run_id}-{seq:04d}",
                    run_id=run_id,
                    master_sha256=current_master_sha256(vault_root, run_id) or "",
                    ledger_head_sha256=current_ledger_head_sha256(vault_root),
                    reviewer="system",
                    attested_at=now,
                    valid=False,
                    reason=check.reason,
                ),
            )
    return check


def require_run(vault_root: Path | str, run_id: str) -> None:
    """Guard: the run must exist (has a RunStarted event)."""
    if load_state(vault_root, run_id).task is None:
        raise OrchestratorError(f"run {run_id!r} has no RunStarted event")
