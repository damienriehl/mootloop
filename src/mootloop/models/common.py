"""Shared domain vocabulary: NewType IDs, confidentiality-typed text, and the
VersionedModel base every persisted model extends."""

from __future__ import annotations

from typing import NewType

from pydantic import BaseModel, ConfigDict

# Canonical matter/run ID pattern. `vault.MATTER_ID_RE` compiles this; model fields
# constrain against the same string — one source of truth, no import cycle.
MATTER_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"

# --- Entity IDs -------------------------------------------------------------
# Distinct NewTypes so mypy rejects passing (say) a FactId where a RunId is wanted.
MatterId = NewType("MatterId", str)
RunId = NewType("RunId", str)
FactId = NewType("FactId", str)
RequestId = NewType("RequestId", str)
DocId = NewType("DocId", str)
DecisionId = NewType("DecisionId", str)
CitationId = NewType("CitationId", str)
TaskSpecId = NewType("TaskSpecId", str)

# --- Confidentiality-typed text --------------------------------------------
# MatterText is confidential matter data; PublicText is publishable. The only
# sanctioned producer of PublicText is a future scrub() — mypy then makes "matter
# data in the web-search lane" a build failure.
MatterText = NewType("MatterText", str)
PublicText = NewType("PublicText", str)


class StrictModel(BaseModel):
    """Base for non-persisted sub-models: strict (``extra="forbid"``) but without a
    ``schema_version`` of their own. A nested value versions with its container."""

    model_config = ConfigDict(extra="forbid")


class VersionedModel(StrictModel):
    """Base for every persisted (top-level) model.

    `extra="forbid"` turns unknown/misspelled fields into field-named validation
    errors for free; `schema_version` anchors future migrations.
    """

    schema_version: str


class MatterProvenanced(BaseModel):
    """Mixin carrying the matter a persisted record derives from (plan FD-6).

    v1 convention: existing matter-scoped models bind to their matter via the vault
    path they are written under (the vault subtree *is* the matter boundary), so they
    are not retrofitted with a redundant field. Every *new* persisted model that lives
    off the matter vault (e.g. the matters-root close log) MUST mix this in, so its
    matter of origin is explicit and machine-checkable even once the vault is gone.
    """

    source_matter_id: MatterId
