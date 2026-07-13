"""Matter-lifecycle records (plan FD-6 close-inventory gate).

`CloseRecord` is the durable proof that a matter was closed and its confidential
vault subtree purged. It lives at the matters-root level — *off* the matter vault, so
it survives the very purge it records — and carries no confidential content: an opaque
matter id, who/when, the backup reference, per-store removed counts, and the
matter-anonymized access-audit tombstone that keeps the FD-3 hash-chain intact past
the close.

It is `MatterProvenanced` (the FD-6 ``source_matter_id`` convention) because, unlike
the in-vault stores, its path no longer implies the matter once the vault is gone.
"""

from __future__ import annotations

from mootloop.models.audit import AccessAuditEntry
from mootloop.models.common import MatterProvenanced, VersionedModel

SCHEMA_VERSION = "1.0"


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
    removed_counts: dict[str, int]
    tombstone: AccessAuditEntry
