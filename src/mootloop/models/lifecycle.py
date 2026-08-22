"""Matter-lifecycle records (plan FD-6 close-inventory gate).

`CloseRecord` is the durable proof that a matter was closed and its confidential
vault subtree purged. It lives at the matters-root level — *off* the matter vault, so
it survives the very purge it records — and carries no work-product content: the validated
matter id, who/when, the retention policy, the backup reference, the complete registered
store inventory, deletion limitations, and the
matter-anonymized access-audit tombstone that keeps the FD-3 hash-chain intact past
the close.

It is `MatterProvenanced` (the FD-6 ``source_matter_id`` convention) because, unlike
the in-vault stores, its path no longer implies the matter once the vault is gone.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from mootloop.models.audit import AccessAuditEntry
from mootloop.models.common import MatterProvenanced, StrictModel, VersionedModel

SCHEMA_VERSION = "1.1"


class DestructionStore(StrictModel):
    """One registered matter store included in the close manifest."""

    name: str
    glob: str
    description: str
    files_removed: int = Field(ge=0)


class DestructionLimitation(StrictModel):
    """A durable warning that logical deletion is not assured physical erasure."""

    kind: Literal["solid_state_media", "synchronized_storage"]
    detail: str


class CloseRecord(MatterProvenanced, VersionedModel):
    """Append-once record that a matter was closed and purged (plan FD-6).

    ``removed_counts`` maps each matter-scoped inventory store name to the number of
    files removed for it; ``tombstone`` is the anonymized `AccessAuditEntry` retained
    to prove the matter existed and was closed while preserving the audit chain.
    """

    schema_version: str = SCHEMA_VERSION
    closed_at: str
    closed_by: str
    backup_ref: str | None
    retention_class: str
    destruction_date: date
    destruction_method: Literal["logical-tree-deletion"] = "logical-tree-deletion"
    assured_destruction: Literal[False] = False
    limitations: tuple[DestructionLimitation, ...]
    stores: tuple[DestructionStore, ...]
    removed_counts: dict[str, int]
    tombstone: AccessAuditEntry
