"""Matter close: the declarative matter-scoped-store inventory and the fail-closed
`close_matter` service (plan FD-6 close-inventory gate).

`MATTER_SCOPED_STORES` is the single source of truth for *every* confidential store a
matter vault holds. Closing a matter walks this inventory, and a CI invariant
(``tests/invariants/test_close_inventory.py``) asserts that every concrete
`VersionedModel` is either backed by a store here or listed in `EXEMPT_MODELS` with a
reason — so a new matter-scoped model cannot be added without `close` learning about
it. The guarantee is enforced by that invariant, not by developer memory.

``source_matter_id`` (plan FD-6): everything under a matter vault is already bound to
its matter by the vault path it is written under (the vault subtree *is* the matter
boundary), so v1 does not retrofit a redundant field onto the existing in-vault models
— a destructive migration for no added safety. New records that live *off* the vault
(the `CloseRecord`) carry `source_matter_id` explicitly via `MatterProvenanced`. The
load-bearing guarantee is registration in this inventory, which the invariant enforces.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel

from mootloop.errors import CloseError, LockHeldError, MatterNotFoundError
from mootloop.models.attestations import Attestation
from mootloop.models.audit import GENESIS_PREV_HASH, AccessAuditEntry
from mootloop.models.citations import ResearchRequest, VerificationRecord
from mootloop.models.common import MatterId, VersionedModel
from mootloop.models.corpus import Manifest
from mootloop.models.decisions import Decision
from mootloop.models.facts import Fact
from mootloop.models.lifecycle import CloseRecord
from mootloop.models.matter import MatterConfig
from mootloop.models.matters import MatterSummary
from mootloop.models.panels import PanelReport
from mootloop.models.requests import RequestSet
from mootloop.models.task import TaskAdapterConfig
from mootloop.models.taskspec import TaskSpec
from mootloop.registry import MatterRegistry
from mootloop.vault import (
    RunLock,
    _is_within,
    _real,
    atomic_write_text,
    safe_vault_path,
    validate_id,
)


class MatterScopedStore(BaseModel):
    """One confidential store inside a matter vault (plan FD-6 inventory row).

    ``glob`` is the store's location relative to the vault root; ``model`` is the
    `VersionedModel` it persists, or ``None`` for stores backed by non-versioned bytes
    (journals, ledgers, rendered deliverables, corpus files, provider sessions).
    """

    name: str
    glob: str
    description: str
    model: type[VersionedModel] | None = None

    model_config = {
        "arbitrary_types_allowed": True,
        "frozen": True,
        "protected_namespaces": (),
    }


# The authoritative set `close` walks. Every confidential store lives here; the whole
# vault subtree is purged, and each row is resolved via `safe_vault_path` first to
# prove containment and to count what was removed.
MATTER_SCOPED_STORES: tuple[MatterScopedStore, ...] = (
    MatterScopedStore(
        name="matter-config",
        glob="matter.yaml",
        description="The vault's matter.yaml.",
        model=MatterConfig,
    ),
    MatterScopedStore(
        name="facts",
        glob="facts/facts.jsonl",
        description="Append-only fact repository.",
        model=Fact,
    ),
    MatterScopedStore(
        name="requests",
        glob="requests/*.json",
        description="Parsed served-discovery request sets.",
        model=RequestSet,
    ),
    MatterScopedStore(
        name="corpus-manifest",
        glob="corpus/manifest.json",
        description="Per-document corpus manifest.",
        model=Manifest,
    ),
    MatterScopedStore(
        name="corpus-files",
        glob="corpus/**/*",
        description="Ingested originals, normalized, and curated documents.",
        model=None,
    ),
    MatterScopedStore(
        name="citation-ledger",
        glob="law/verifications.jsonl",
        description="Citation-verification ledger.",
        model=VerificationRecord,
    ),
    MatterScopedStore(
        name="curated-law",
        glob="law/curated/*",
        description="Curated authority text pulled for verification.",
        model=None,
    ),
    MatterScopedStore(
        name="research-queue",
        glob="research-requests/queue.jsonl",
        description="Unverifiable-citation research queue.",
        model=ResearchRequest,
    ),
    MatterScopedStore(
        name="task-specs",
        glob="tasks/specs.jsonl",
        description="Begin-task on-ramp TaskSpec store.",
        model=TaskSpec,
    ),
    MatterScopedStore(
        name="access-audit",
        glob="audit/access.jsonl",
        description="Hash-chained access audit (a matter-anonymized tombstone is "
        "retained off-vault; the in-vault log itself is purged).",
        model=AccessAuditEntry,
    ),
    MatterScopedStore(
        name="run-journals",
        glob="runs/*/journal.jsonl",
        description="Per-run event journals (the run source of truth).",
        model=None,
    ),
    MatterScopedStore(
        name="run-attestations",
        glob="runs/*/attestations.jsonl",
        description="Per-run reviewer attestations.",
        model=Attestation,
    ),
    MatterScopedStore(
        name="run-decisions",
        glob="runs/*/decisions/decisions.jsonl",
        description="Per-run attorney-gate decisions.",
        model=Decision,
    ),
    MatterScopedStore(
        name="run-panel-reports",
        glob="runs/*/scores/panels/report.json",
        description="Per-run judge-panel reports.",
        model=PanelReport,
    ),
    MatterScopedStore(
        name="run-artifacts",
        glob="runs/**/*",
        description="Turns, gate ledgers, STATUS.md, provider sessions and settings.",
        model=None,
    ),
    MatterScopedStore(
        name="deliverables",
        glob="deliverables/**/*",
        description="Rendered work-product deliverables.",
        model=None,
    ),
    MatterScopedStore(
        name="learnings",
        glob="learnings/**/*",
        description="Per-matter learnings scratch.",
        model=None,
    ),
    MatterScopedStore(
        name="canary",
        glob=".canary",
        description="Seeded privacy canary token.",
        model=None,
    ),
)


# Concrete `VersionedModel`s that are deliberately NOT matter-scoped-purgeable, each
# with the reason the invariant records instead of demanding a store.
EXEMPT_MODELS: dict[type[VersionedModel], str] = {
    MatterSummary: (
        "Derived registry view built on the fly from matter.yaml; never persisted "
        "per-matter, so nothing to purge."
    ),
    TaskAdapterConfig: (
        "Repo config loaded from config/tasks/<task>.yaml; ships with the code, not "
        "matter data."
    ),
    CloseRecord: (
        "The close log itself — written to the matters-root level so it survives the "
        "purge it records; carries no confidential content (opaque id + counts + "
        "anonymized tombstone) and is MatterProvenanced."
    ),
}

# Whole modules of `VersionedModel`s that are consciously non-matter-scoped, with the
# reason. Used for cohesive categories (e.g. HTTP response envelopes) so a new sibling
# is covered without a per-class entry — but the category is still an explicit decision.
EXEMPT_MODULES: dict[str, str] = {
    "mootloop.web.api.models": (
        "HTTP response envelopes: serialized over the wire to build a page, never "
        "persisted to a matter vault, so nothing for close to purge."
    ),
}

# The set of models registered as matter-scoped (backed by an inventory store).
MATTER_SCOPED_MODELS: frozenset[type[VersionedModel]] = frozenset(
    store.model for store in MATTER_SCOPED_STORES if store.model is not None
)

# Where the durable, off-vault close artifacts live under the matters-root.
CLOSED_DIRNAME = ".closed"
TOMBSTONES_FILE = "access-tombstones.jsonl"


def concrete_versioned_models() -> set[type[VersionedModel]]:
    """Every concrete `VersionedModel` subclass defined under the ``mootloop`` package.

    Imports the whole models package so lazy imports cannot hide a subclass, then walks
    the subclass tree. Models defined outside ``mootloop`` (e.g. test dummies) are
    excluded — the invariant governs the product's models only.
    """
    import importlib
    import pkgutil

    import mootloop.models as models_pkg

    for info in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"{models_pkg.__name__}.{info.name}")

    seen: set[type[VersionedModel]] = set()

    def _walk(cls: type[VersionedModel]) -> None:
        for sub in cls.__subclasses__():
            if sub.__module__.startswith("mootloop"):
                seen.add(sub)
            _walk(sub)

    _walk(VersionedModel)
    return seen


def is_registered(model: type[VersionedModel]) -> bool:
    """True if ``model`` is a matter-scoped store or explicitly exempt (per model or
    per module)."""
    return (
        model in MATTER_SCOPED_MODELS
        or model in EXEMPT_MODELS
        or model.__module__ in EXEMPT_MODULES
    )


def unregistered_models() -> set[type[VersionedModel]]:
    """Concrete `VersionedModel`s that are neither registered nor exempt — the gate."""
    return {m for m in concrete_versioned_models() if not is_registered(m)}


# --- close service ----------------------------------------------------------

_CLOSE_LOCK_RUN_ID = "close"


def _audit_head(vault_root: Path) -> str:
    """The access-audit chain head (last ``entry_hash``, genesis if none). Read before
    purge so the retained tombstone links to the real pre-close chain."""
    path = safe_vault_path(vault_root, "audit", "access.jsonl")
    if not path.is_file():
        return GENESIS_PREV_HASH
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        if line.strip():
            return AccessAuditEntry.model_validate_json(line).entry_hash
    return GENESIS_PREV_HASH


def _inventory_counts(vault_root: Path) -> dict[str, int]:
    """Count existing files per inventory store, each resolved via `safe_vault_path`.

    A store whose glob has no wildcard is a single path; wildcard globs are expanded
    within the vault. Every candidate is containment-checked before it is counted.
    """
    root_real = _real(vault_root)
    counts: dict[str, int] = {}
    for store in MATTER_SCOPED_STORES:
        total = 0
        if any(ch in store.glob for ch in "*?["):
            for match in root_real.glob(store.glob):
                if match.is_file() and _is_within(_real(match), root_real):
                    total += 1
        else:
            candidate = safe_vault_path(root_real, *store.glob.split("/"))
            if candidate.is_file():
                total += 1
        counts[store.name] = total
    return counts


def _assert_idle(vault_root: Path) -> None:
    """Refuse if a live run holds the per-matter lock (idle-only, like backup).

    Acquiring and immediately releasing the lock proves no live process holds it; a
    stale/dead lock is taken over and released harmlessly.
    """
    try:
        with RunLock(vault_root, _CLOSE_LOCK_RUN_ID):
            pass
    except LockHeldError as exc:
        raise CloseError(f"cannot close: a live run holds the matter lock ({exc})") from exc


def _purge_vault(matters_root: Path, matter_id: str) -> Path:
    """Remove the whole matter vault subtree, resolved via `safe_vault_path`.

    The vault path is derived from realpath(matters-root) + the validated id, so a
    crafted id can never redirect the removal outside the root. Refuses to touch the
    root itself. Idempotent: a missing vault is a no-op.
    """
    root_real = _real(matters_root)
    vault = safe_vault_path(root_real, matter_id)
    if vault == root_real:
        raise CloseError("refusing to purge the matters-root itself")
    if vault.exists():
        shutil.rmtree(vault)
    return vault


def _append_tombstone(matters_root: Path, tombstone: AccessAuditEntry) -> None:
    closed_dir = safe_vault_path(matters_root, CLOSED_DIRNAME)
    closed_dir.mkdir(parents=True, exist_ok=True)
    path = safe_vault_path(matters_root, CLOSED_DIRNAME, TOMBSTONES_FILE)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    atomic_write_text(path, existing + tombstone.model_dump_json() + "\n")


def _write_close_record(matters_root: Path, record: CloseRecord) -> Path:
    path = safe_vault_path(matters_root, CLOSED_DIRNAME, f"{record.source_matter_id}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, record.model_dump_json(indent=2) + "\n")
    return path


def close_matter(
    matters_root: Path | str,
    matter_id: str,
    *,
    actor: str,
    now: str,
    backup_dir: Path | str | None,
    skip_backup: bool = False,
    acknowledge_skip_backup: bool = False,
) -> CloseRecord:
    """Close a matter: back up, purge every store in the inventory, retain a tombstone.

    Fail-closed at every step: refuses while a live run holds the lock; requires a
    fresh backup unless ``skip_backup`` is paired with ``acknowledge_skip_backup``;
    purges the vault subtree through `safe_vault_path` containment; then asserts the
    subtree is gone before recording the close. The matter-anonymized `AccessAuditEntry`
    tombstone is linked to the pre-close audit head, preserving the FD-3 hash chain, and
    the `CloseRecord` is written to the matters-root close log (off the vault).
    """
    validate_id(matter_id, kind="matter_id")
    matters_root_path = Path(matters_root)
    registry = MatterRegistry(matters_root_path)
    try:
        vault = registry.resolve(matter_id)
    except MatterNotFoundError as exc:
        raise CloseError(f"cannot close unknown matter {matter_id!r}: {exc}") from exc

    _assert_idle(vault)

    backup_ref: str | None = None
    if skip_backup:
        if not acknowledge_skip_backup:
            raise CloseError(
                "refusing to close without a fresh backup; pass acknowledge_skip_backup "
                "to override (data will be unrecoverable)"
            )
    else:
        if backup_dir is None:
            raise CloseError("a backup destination is required unless the backup is acknowledged")
        from mootloop.engine.backup import backup_matter

        backup_ref = str(backup_matter(vault, backup_dir, now))

    prev_hash = _audit_head(vault)
    removed_counts = _inventory_counts(vault)

    with RunLock(vault, _CLOSE_LOCK_RUN_ID):
        _purge_vault(matters_root_path, matter_id)

    if _real(matters_root_path).joinpath(matter_id).exists():
        raise CloseError(
            f"post-close verification failed: residue remains for matter {matter_id!r}"
        )

    tombstone = AccessAuditEntry.create(
        ts=now,
        actor=actor,
        action="matter-closed",
        matter_id=matter_id,
        resource="",
        prev_hash=prev_hash,
    )
    record = CloseRecord(
        source_matter_id=MatterId(matter_id),
        closed_at=now,
        closed_by=actor,
        backup_ref=backup_ref,
        removed_counts=removed_counts,
        tombstone=tombstone,
    )
    _append_tombstone(matters_root_path, tombstone)
    _write_close_record(matters_root_path, record)
    return record
