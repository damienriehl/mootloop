"""Corpus vocabulary: document roles, the per-document manifest entry, the corpus
`Manifest`, and the `IngestReport` returned by a folder ingest.

`doc_id` is content-derived (`doc-<sha256[:16]>`) so provenance never dangles across
re-ingest or re-normalization (plan D9). The manifest persists atomically under
`safe_vault_path`.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field

from mootloop.models.common import DocId, StrictModel, VersionedModel

SCHEMA_VERSION = "1.1"

MANIFEST_PATH = ("corpus", "manifest.json")

# One of the four terminal states a document reaches at ingest.
IngestStatus = Literal["ok", "needs_conversion", "unreadable", "too_large"]
TriageIssue = Literal[
    "needs_ocr",
    "password_protected",
    "corrupt",
    "unsupported_format",
    "too_large",
    "unreadable",
]
IngestActionKind = Literal[
    "confirm_role",
    "confirm_privilege",
    "needs_ocr",
    "password_protected",
    "corrupt",
    "unsupported_format",
    "too_large",
    "unreadable",
]


class DocRole(StrEnum):
    """How a corpus document functions in the matter."""

    COMPLAINT = "complaint"
    ANSWER = "answer"
    SERVED_DISCOVERY = "served-discovery"
    CLIENT_DOC = "client-doc"
    AUTHORITY = "authority"
    CORRESPONDENCE = "correspondence"
    OTHER = "other"


class CorpusDoc(StrictModel):
    """One document's manifest entry.

    ``privileged`` is tri-state: ``None`` means unreviewed, ``True``/``False`` a
    recorded privilege call. ``normalized_path`` is a vault-relative path, present
    only when normalization produced markdown (``ingest_status == "ok"``).
    """

    doc_id: DocId
    original_name: str
    media_type: str
    role: DocRole | None = None
    privileged: bool | None = None
    ingest_status: IngestStatus
    triage_issue: TriageIssue | None = None
    normalized_path: str | None = None
    ingested_at: str

    @property
    def run_visible(self) -> bool:
        """Only normalized documents with both human triage calls may enter runs."""
        return (
            self.ingest_status == "ok"
            and self.normalized_path is not None
            and self.role is not None
            and self.privileged is not None
        )


class IngestAction(StrictModel):
    """One deterministic human action required before a document is usable."""

    action_id: str
    doc_id: DocId
    original_name: str
    kind: IngestActionKind
    reason: str
    blocks_run_visibility: bool = True


class Manifest(VersionedModel):
    """The corpus manifest — the authoritative inventory of ingested documents."""

    schema_version: str = SCHEMA_VERSION
    docs: list[CorpusDoc] = Field(default_factory=list)

    # -- lookup / mutation (in-memory; persistence is explicit via save) --
    def get(self, doc_id: str) -> CorpusDoc | None:
        return next((d for d in self.docs if d.doc_id == doc_id), None)

    def upsert(self, doc: CorpusDoc) -> None:
        """Insert ``doc`` or replace the existing entry with the same ``doc_id``."""
        for idx, existing in enumerate(self.docs):
            if existing.doc_id == doc.doc_id:
                self.docs[idx] = doc
                return
        self.docs.append(doc)

    # -- persistence --
    @classmethod
    def load(cls, vault_root: Path | str) -> Manifest:
        """Load the manifest, or an empty one if none exists yet."""
        # Lazy import: vault imports models at its top, so importing vault at this
        # module's top would re-enter a half-initialized vault (mirrors create_vault).
        from mootloop.vault import safe_vault_path

        path = safe_vault_path(vault_root, *MANIFEST_PATH)
        if not path.is_file():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, vault_root: Path | str) -> Path:
        """Persist the manifest atomically (temp + ``os.replace``)."""
        from mootloop.vault import atomic_write_text, safe_vault_path

        path = safe_vault_path(vault_root, *MANIFEST_PATH)
        atomic_write_text(path, json.dumps(self.model_dump(mode="json"), indent=2) + "\n")
        return path


class IngestEntry(StrictModel):
    """A single document's outcome in an `IngestReport` (doc + optional reason)."""

    doc: CorpusDoc
    reason: str | None = None


class IngestReport(StrictModel):
    """The result of one `ingest_folder` call: every document processed this run."""

    entries: list[IngestEntry] = Field(default_factory=list)
    actions: list[IngestAction] = Field(default_factory=list)

    def with_status(self, status: IngestStatus) -> list[IngestEntry]:
        return [e for e in self.entries if e.doc.ingest_status == status]

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.doc.ingest_status] = counts.get(entry.doc.ingest_status, 0) + 1
        return counts
