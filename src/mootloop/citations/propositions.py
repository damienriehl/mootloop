"""Citation propositions, original-text spans, and their append-only result ledger."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from mootloop.citations.extract import extract_citation_occurrences
from mootloop.models.citations import (
    CitationProposition,
    PropositionVerificationRecord,
    make_proposition_id,
)
from mootloop.persistence import append_fsync_line, complete_jsonl_lines
from mootloop.vault import safe_vault_path

LEDGER_PATH = ("law", "proposition-verifications.jsonl")
MAX_PROPOSITION_CHARS = 4096
_PARAGRAPH_BREAK = re.compile(r"(?:\r?\n[ \t]*){2,}")


def _paragraph_span(text: str, citation_start: int, citation_end: int) -> tuple[int, int]:
    start = 0
    end = len(text)
    for match in _PARAGRAPH_BREAK.finditer(text):
        if match.end() <= citation_start:
            start = match.end()
        elif match.start() >= citation_end:
            end = match.start()
            break
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end - start <= MAX_PROPOSITION_CHARS:
        return start, end
    half = MAX_PROPOSITION_CHARS // 2
    bounded_start = max(start, citation_start - half)
    bounded_end = min(end, bounded_start + MAX_PROPOSITION_CHARS)
    bounded_start = max(start, bounded_end - MAX_PROPOSITION_CHARS)
    while bounded_start < citation_start and text[bounded_start].isspace():
        bounded_start += 1
    while bounded_end > citation_end and text[bounded_end - 1].isspace():
        bounded_end -= 1
    return bounded_start, bounded_end


def extract_citation_propositions(
    text: str, *, source_turn_id: str | None = None
) -> list[CitationProposition]:
    """Pair every citation occurrence with its bounded original-text paragraph."""
    source_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    propositions: list[CitationProposition] = []
    for occurrence in extract_citation_occurrences(text, source_turn_id=source_turn_id):
        start, end = _paragraph_span(text, occurrence.start, occurrence.end)
        proposition_text = text[start:end]
        propositions.append(
            CitationProposition(
                proposition_id=make_proposition_id(
                    occurrence.citation.citation_id, proposition_text
                ),
                citation_id=occurrence.citation.citation_id,
                normalized_citation=occurrence.citation.normalized,
                proposition_text=proposition_text,
                proposition_start=start,
                proposition_end=end,
                citation_start=occurrence.start,
                citation_end=occurrence.end,
                source_text_sha256=source_sha256,
                source_turn_id=source_turn_id,
            )
        )
    return propositions


class PropositionLedger:
    """Matter-scoped, append-only cite-checker records keyed by exact authority bytes."""

    def __init__(self, vault_root: Path | str) -> None:
        self._path = safe_vault_path(vault_root, *LEDGER_PATH)

    def _records(self) -> list[PropositionVerificationRecord]:
        return [
            PropositionVerificationRecord.model_validate_json(line)
            for line in complete_jsonl_lines(self._path)
        ]

    def folded(self) -> dict[tuple[str, str, str, str], PropositionVerificationRecord]:
        state: dict[tuple[str, str, str, str], PropositionVerificationRecord] = {}
        for record in self._records():
            state[
                (
                    record.run_id,
                    record.proposition_id,
                    record.source_text_sha256,
                    record.authority_sha256,
                )
            ] = record
        return state

    def get(
        self,
        run_id: str,
        proposition_id: str,
        source_text_sha256: str,
        authority_sha256: str,
    ) -> PropositionVerificationRecord | None:
        return self.folded().get(
            (run_id, proposition_id, source_text_sha256, authority_sha256)
        )

    def latest_by_proposition(
        self, run_id: str
    ) -> dict[tuple[str, str], PropositionVerificationRecord]:
        state: dict[tuple[str, str], PropositionVerificationRecord] = {}
        for record in self._records():
            if record.run_id == run_id:
                state[(record.proposition_id, record.source_text_sha256)] = record
        return state

    def append(self, record: PropositionVerificationRecord) -> None:
        append_fsync_line(self._path, record.model_dump_json())
