"""`MatterSummary` — a registry entry for one matter under the matters-root.

The hosted tier's `MatterRegistry` scans the matters-root and returns one of these
per valid vault. It carries only listing-safe metadata: the id, the caption's public
display strings, and a root-relative descriptor — never a `MatterText`-typed value or
an absolute host path. Confidential matter content stays inside the vault.
"""

from __future__ import annotations

from mootloop.models.common import MatterId, VersionedModel

SCHEMA_VERSION = "1.0"


class MatterSummary(VersionedModel):
    """Listing-safe summary of a single matter vault.

    `display_name`/`case_number` are the caption strings a human uses to recognize a
    matter (court captions are public record, not `MatterText`). `rel_path` is the
    vault directory name relative to the matters-root — never an absolute path.
    """

    schema_version: str = SCHEMA_VERSION
    matter_id: MatterId
    display_name: str
    case_number: str
    rel_path: str
    loaded: bool
