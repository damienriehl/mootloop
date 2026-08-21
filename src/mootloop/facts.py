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

import fcntl
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from mootloop.errors import FactError
from mootloop.models.common import DocId, FactId, StrictModel
from mootloop.models.corpus import Manifest
from mootloop.models.facts import Fact, FactInterview, FactQuestion, Provenance
from mootloop.persistence import append_fsync_line, complete_jsonl_lines
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
        if record.review_status != "accepted":
            continue
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
        self._lock_path = safe_vault_path(vault_root, "facts", ".review.lock")

    # -- reads --
    def _records(self) -> list[Fact]:
        if not self._path.is_file():
            return []
        return [Fact.model_validate_json(line) for line in complete_jsonl_lines(self._path)]

    @contextmanager
    def _operation_lock(self) -> Iterator[None]:
        """Serialize read-check-append fact transitions across local processes."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "rb") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield

    def all_folded(self) -> list[Fact]:
        """Every version (current and superseded), folded from the log."""
        return list(fold(self._records()).values())

    def get_current(self) -> list[Fact]:
        """The current (non-superseded) facts, in first-seen order."""
        return [f for f in self.all_folded() if f.superseded_by is None]

    def get_run_visible(self) -> list[Fact]:
        """Current facts whose human review status permits run-context capture."""
        return [fact for fact in self.get_current() if fact.review_status == "accepted"]

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

    def propose_fact(
        self,
        statement: str,
        *,
        provenance: list[Provenance] | None = None,
        confidence: float,
    ) -> Fact:
        """Append an unapproved candidate that cannot enter a run until reviewed."""
        prov = provenance or []
        fact = Fact(
            fact_id=_fact_id(statement, 1, prov),
            statement=statement,
            provenance=prov,
            confidence=confidence,
            version=1,
            superseded_by=None,
            review_status="pending",
        )
        with self._operation_lock():
            if self.get(fact.fact_id) is not None:
                raise FactError(f"fact candidate {fact.fact_id} already exists")
            self._append(fact)
        return fact

    def propose_revision(
        self,
        predecessor_id: str,
        statement: str,
        *,
        provenance: list[Provenance] | None = None,
        confidence: float,
    ) -> Fact:
        """Append a pending revision without displacing the accepted predecessor."""
        with self._operation_lock():
            predecessor = self.get(predecessor_id)
            if predecessor is None:
                raise FactError(f"unknown fact_id: {predecessor_id}")
            if predecessor.superseded_by is not None:
                raise FactError(
                    f"fact {predecessor_id} is already superseded by "
                    f"{predecessor.superseded_by}"
                )
            prov = provenance or []
            candidate = Fact(
                fact_id=_fact_id(statement, predecessor.version + 1, prov),
                statement=statement,
                provenance=prov,
                confidence=confidence,
                version=predecessor.version + 1,
                supersedes=predecessor.fact_id,
                review_status="pending",
            )
            if self.get(candidate.fact_id) is not None:
                raise FactError(f"fact candidate {candidate.fact_id} already exists")
            self._append(candidate)
            return candidate

    def review_fact(
        self,
        fact_id: str,
        *,
        action: Literal["accept", "reject"],
        reviewer: str,
        reviewed_at: str,
        note: str | None = None,
    ) -> Fact:
        """Append a hard-human acceptance or rejection of one pending candidate."""
        if action not in ("accept", "reject"):
            raise FactError(f"unsupported fact review action: {action}")
        with self._operation_lock():
            return self._review_fact_locked(
                fact_id,
                action=action,
                reviewer=reviewer,
                reviewed_at=reviewed_at,
                note=note,
            )

    def _review_fact_locked(
        self,
        fact_id: str,
        *,
        action: Literal["accept", "reject"],
        reviewer: str,
        reviewed_at: str,
        note: str | None,
    ) -> Fact:
        fact = self.get(fact_id)
        if fact is None:
            raise FactError(f"unknown fact_id: {fact_id}")
        if fact.review_status != "pending":
            raise FactError(f"fact {fact_id} is already {fact.review_status}")
        if not reviewer.strip() or not reviewed_at.strip():
            raise FactError("fact review requires a reviewer and timestamp")
        if action == "accept":
            problems = _fact_support_problems(self.vault_root, fact)
            if problems:
                raise FactError(f"fact provenance is not review-ready: {'; '.join(problems)}")
        predecessor = self.get(fact.supersedes) if fact.supersedes is not None else None
        if action == "accept" and fact.supersedes is not None:
            if predecessor is None:
                raise FactError(f"unknown predecessor fact: {fact.supersedes}")
            if predecessor.superseded_by is not None:
                raise FactError(
                    f"fact {fact.supersedes} is already superseded by "
                    f"{predecessor.superseded_by}"
                )
        reviewed = fact.model_copy(
            update={
                "review_status": "accepted" if action == "accept" else "rejected",
                "reviewed_by": reviewer,
                "reviewed_at": reviewed_at,
                "review_note": note,
            }
        )
        self._append(reviewed)
        if action == "accept" and predecessor is not None:
            self._append(predecessor.model_copy(update={"superseded_by": reviewed.fact_id}))
        return reviewed

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
        with self._operation_lock():
            return self._revise_fact_locked(
                predecessor_id,
                statement,
                provenance=provenance,
                confidence=confidence,
            )

    def _revise_fact_locked(
        self,
        predecessor_id: str,
        statement: str,
        *,
        provenance: list[Provenance] | None,
        confidence: float,
    ) -> Fact:
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
        append_fsync_line(self._path, fact.model_dump_json())


def _fact_support_problems(vault_root: Path | str, fact: Fact) -> list[str]:
    if not fact.provenance:
        return ["no provenance"]
    manifest = Manifest.load(vault_root)
    problems: list[str] = []
    for provenance in fact.provenance:
        doc = manifest.get(provenance.doc_id)
        if doc is None:
            problems.append(f"unknown document {provenance.doc_id}")
            continue
        if not doc.run_visible:
            problems.append(f"document {provenance.doc_id} is not role/privilege reviewed")
            continue
        assert doc.normalized_path is not None
        path = safe_vault_path(vault_root, *Path(doc.normalized_path).parts)
        if not path.is_file():
            problems.append(f"document {provenance.doc_id} normalized text is missing")
            continue
        quote = provenance.quote.strip()
        if not quote or quote not in path.read_text(encoding="utf-8"):
            problems.append(f"quote not found in document {provenance.doc_id}")
    return problems


def _question_id(kind: str, identity: str) -> str:
    digest = hashlib.sha256(f"{kind}\x00{identity}".encode()).hexdigest()[:16]
    return f"fact-question-{digest}"


def build_fact_interview(vault_root: Path | str) -> FactInterview:
    """Build deterministic human questions for fact review and evidentiary gaps."""
    store = FactStore(vault_root)
    facts = store.get_current()
    visible_docs = [doc for doc in Manifest.load(vault_root).docs if doc.run_visible]
    questions: list[FactQuestion] = []
    covered: set[str] = set()
    for fact in sorted(facts, key=lambda item: str(item.fact_id)):
        if fact.review_status == "pending":
            questions.append(
                FactQuestion(
                    question_id=_question_id("review_fact", str(fact.fact_id)),
                    kind="review_fact",
                    prompt=f"Accept or reject proposed fact {fact.fact_id}: {fact.statement}",
                    fact_id=fact.fact_id,
                )
            )
        if fact.review_status != "accepted":
            continue
        if not fact.provenance:
            questions.append(
                FactQuestion(
                    question_id=_question_id("missing_provenance", str(fact.fact_id)),
                    kind="missing_provenance",
                    prompt=f"Add documentary support for fact {fact.fact_id}: {fact.statement}",
                    fact_id=fact.fact_id,
                )
            )
            continue
        problems = _fact_support_problems(vault_root, fact)
        if problems:
            questions.append(
                FactQuestion(
                    question_id=_question_id("repair_provenance", str(fact.fact_id)),
                    kind="repair_provenance",
                    prompt=f"Repair provenance for fact {fact.fact_id}: {'; '.join(problems)}",
                    fact_id=fact.fact_id,
                )
            )
        else:
            covered.update(str(item.doc_id) for item in fact.provenance)
    for doc in sorted(visible_docs, key=lambda item: str(item.doc_id)):
        if str(doc.doc_id) in covered:
            continue
        questions.append(
            FactQuestion(
                question_id=_question_id("uncovered_document", str(doc.doc_id)),
                kind="uncovered_document",
                prompt=f"Identify legally relevant facts, if any, in {doc.original_name!r}.",
                doc_id=doc.doc_id,
            )
        )
    return FactInterview(
        run_visible_fact_count=len(store.get_run_visible()),
        questions=questions,
    )


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
