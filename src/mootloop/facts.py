"""Append-only fact repository — a mini event log folded into current state.

The log is ``facts/facts.jsonl``: one `Fact` per line, append-only, fsync'd. A prior
line is NEVER mutated. A revision appends the new version *and* re-emits the
predecessor with ``superseded_by`` set; `fold` (a pure function) replays the log,
last-line-per-id winning, so the current view reflects both without any in-place
edit. Each distinct version carries its own content-derived ``fact_id``, so every
version stays independently retrievable.

The successor also carries ``supersedes`` (the predecessor's id), and `fold`
derives the supersession from it when the re-emitted predecessor line is missing.
That makes the revision atomic at the FIRST append: a crash before the second one
leaves a log the fold still reads unambiguously, rather than two records that both
look current.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from pydantic import Field, ValidationError

from mootloop.errors import FactError
from mootloop.models.common import DocId, FactId, StrictModel
from mootloop.models.corpus import Manifest
from mootloop.models.facts import Fact, Provenance
from mootloop.vault import safe_vault_path

FACTS_PATH = ("facts", "facts.jsonl")


# --- pure fold --------------------------------------------------------------


def fold(records: list[Fact]) -> dict[str, Fact]:
    """Replay the log into ``fact_id -> latest record`` (last write wins).

    Pure and total: unit-testable resume with no I/O. A re-emitted predecessor
    shares its ``fact_id`` with the original line, so its ``superseded_by`` update
    lands here without mutating the earlier record.

    Second pass: a successor's ``supersedes`` back-pointer closes the transition
    even when the predecessor's re-emitted line never landed (a crash between the
    two appends). The derivation is view-only — it fills ``superseded_by`` on the
    FOLDED copy and never rewrites the log — and it defers to an explicit
    ``superseded_by`` already on the record, so history stays authoritative.
    First successor wins, so a duplicated revision cannot flip a settled edge.
    """
    state: dict[str, Fact] = {}
    for record in records:
        state[record.fact_id] = record
    for record in records:
        predecessor = state.get(record.supersedes) if record.supersedes else None
        if predecessor is not None and predecessor.superseded_by is None:
            state[predecessor.fact_id] = predecessor.model_copy(
                update={"superseded_by": record.fact_id}
            )
    return state


def _fact_id(statement: str, version: int, provenance: list[Provenance]) -> FactId:
    digest = hashlib.sha256()
    digest.update(statement.encode("utf-8"))
    digest.update(f"\x00{version}\x00".encode())
    for prov in provenance:
        digest.update(f"{prov.doc_id}\x00{prov.quote}\x00".encode())
    return FactId(f"fact-{digest.hexdigest()[:16]}")


# --- store ------------------------------------------------------------------


class FactStore:
    """Append-only JSONL fact store folded into current state on read."""

    def __init__(self, vault_root: Path | str) -> None:
        self.vault_root = vault_root
        self._path = safe_vault_path(vault_root, *FACTS_PATH)

    # -- reads --
    def _records(self) -> list[Fact]:
        if not self._path.is_file():
            return []
        records: list[Fact] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(Fact.model_validate_json(line))
        return records

    def all_folded(self) -> list[Fact]:
        """Every version (current and superseded), folded from the log."""
        return list(fold(self._records()).values())

    def get_current(self) -> list[Fact]:
        """The current (non-superseded) facts, in first-seen order."""
        return [f for f in self.all_folded() if f.superseded_by is None]

    def get(self, fact_id: str) -> Fact | None:
        """The folded record for ``fact_id`` (may be superseded), or ``None``."""
        return fold(self._records()).get(fact_id)

    # -- writes --
    def add_fact(
        self,
        statement: str,
        *,
        provenance: list[Provenance] | None = None,
        confidence: float,
    ) -> Fact:
        """Append a brand-new fact at version 1."""
        prov = provenance or []
        fact = Fact(
            fact_id=_fact_id(statement, 1, prov),
            statement=statement,
            provenance=prov,
            confidence=confidence,
            version=1,
            superseded_by=None,
        )
        self._append(fact)
        return fact

    def revise_fact(
        self,
        predecessor_id: str,
        statement: str,
        *,
        provenance: list[Provenance] | None = None,
        confidence: float,
    ) -> Fact:
        """Append a new version and mark the predecessor superseded (append-only).

        The successor line carries ``supersedes``, so the revision is complete — and
        readable as complete — the moment that first line is fsync'd. The re-emitted
        predecessor that follows records the same edge from the other side; it is
        what keeps a raw ``grep`` of the log honest, and is no longer what the fold
        depends on. Crashing between the two appends can no longer leave two records
        that both read as current, which for a fact log behind a court filing means
        two contradictory versions of the same assertion, both citable.
        """
        current = fold(self._records())
        predecessor = current.get(predecessor_id)
        if predecessor is None:
            raise FactError(f"unknown fact_id: {predecessor_id}")
        if predecessor.superseded_by is not None:
            raise FactError(
                f"fact {predecessor_id} is already superseded by {predecessor.superseded_by}"
            )
        prov = provenance or []
        version = predecessor.version + 1
        successor = Fact(
            fact_id=_fact_id(statement, version, prov),
            statement=statement,
            provenance=prov,
            confidence=confidence,
            version=version,
            superseded_by=None,
            supersedes=predecessor.fact_id,
        )
        self._append(successor)
        # Re-emit the predecessor (same fact_id) carrying the supersession pointer.
        self._append(predecessor.model_copy(update={"superseded_by": successor.fact_id}))
        return successor

    def _append(self, fact: Fact) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = fact.model_dump_json() + "\n"
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


# --- input-file loading -----------------------------------------------------


class _ProvenanceInput(StrictModel):
    """One provenance entry in a facts input file. Reference the corpus doc by
    ``doc_id`` or by ``source`` (its original filename, resolved via the manifest)."""

    doc_id: str | None = None
    source: str | None = None
    quote: str
    location_hint: str | None = None


class _FactInput(StrictModel):
    statement: str
    confidence: float = 1.0
    provenance: list[_ProvenanceInput] = Field(default_factory=list)


def _resolve_doc_id(entry: _ProvenanceInput, manifest: Manifest) -> DocId:
    if entry.doc_id is not None:
        return DocId(entry.doc_id)
    if entry.source is None:
        raise FactError("provenance entry needs either 'doc_id' or 'source'")
    matches = [d for d in manifest.docs if d.original_name == entry.source]
    if not matches:
        raise FactError(f"provenance source {entry.source!r} not found in manifest")
    if len(matches) > 1:
        raise FactError(f"provenance source {entry.source!r} is ambiguous ({len(matches)} docs)")
    return matches[0].doc_id


def add_facts_from_file(vault_root: Path | str, input_path: Path | str) -> list[Fact]:
    """Add every fact in a JSON input file to the store.

    The file is a JSON list of ``{statement, confidence?, provenance: [...]}``; each
    provenance entry names a corpus doc by ``doc_id`` or ``source`` (filename).
    """
    path = Path(input_path)
    if not path.is_file():
        raise FactError(f"facts input file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise FactError("facts input must be a JSON list of fact objects")
    try:
        inputs = [_FactInput.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise FactError(f"invalid facts input: {exc}") from exc

    manifest = Manifest.load(vault_root)
    store = FactStore(vault_root)
    added: list[Fact] = []
    for fact_input in inputs:
        provenance = [
            Provenance(
                doc_id=_resolve_doc_id(entry, manifest),
                quote=entry.quote,
                location_hint=entry.location_hint,
            )
            for entry in fact_input.provenance
        ]
        added.append(
            store.add_fact(
                fact_input.statement,
                provenance=provenance,
                confidence=fact_input.confidence,
            )
        )
    return added
