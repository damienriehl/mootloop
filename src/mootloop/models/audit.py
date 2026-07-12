"""`AccessAuditEntry` — the hash-chained who/when/what record of every matter-data
page view and download on the hosted tier (plan FD-3, threat-model asset).

Each entry commits to its predecessor: ``prev_hash`` is the previous entry's
``entry_hash`` (the genesis predecessor is `GENESIS_PREV_HASH`, 64 zeros), and
``entry_hash`` is the SHA-256 of the canonical JSON of the entry with ``entry_hash``
excluded (``prev_hash`` therefore participates, chaining the log). Any reordering,
insertion, or edit breaks the recomputation — the integrity of the log is itself an
asset, and its head is folded into the attestation tuple.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from mootloop.models.common import VersionedModel

SCHEMA_VERSION = "1.0"

# The genesis predecessor hash: the first entry's ``prev_hash`` (no earlier entry).
GENESIS_PREV_HASH = "0" * 64


def _digest(payload: dict[str, Any]) -> str:
    """SHA-256 hex of the canonical JSON of ``payload`` (sorted keys, tight separators).

    Canonicalization is deterministic across processes so a recomputation on read
    reproduces the stored hash exactly — the single definition of "what is signed".
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AccessAuditEntry(VersionedModel):
    """One append-only, hash-chained access-audit record."""

    schema_version: str = SCHEMA_VERSION
    ts: str
    actor: str
    action: str
    matter_id: str
    resource: str
    prev_hash: str
    entry_hash: str

    def expected_hash(self) -> str:
        """Recompute this entry's ``entry_hash`` from its own fields (minus the hash).

        ``prev_hash`` is one of those fields, so the value transitively commits to the
        whole prefix of the chain.
        """
        payload = self.model_dump(mode="json", exclude={"entry_hash"})
        return _digest(payload)

    @classmethod
    def create(
        cls,
        *,
        ts: str,
        actor: str,
        action: str,
        matter_id: str,
        resource: str,
        prev_hash: str,
    ) -> AccessAuditEntry:
        """Build an entry linked to ``prev_hash`` with its ``entry_hash`` computed."""
        partial = cls(
            ts=ts,
            actor=actor,
            action=action,
            matter_id=matter_id,
            resource=resource,
            prev_hash=prev_hash,
            entry_hash="",
        )
        return partial.model_copy(update={"entry_hash": partial.expected_hash()})
