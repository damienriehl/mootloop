"""Deterministic, bounded assembly of launch-approved prompt data."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence

from mootloop.errors import OrchestratorError
from mootloop.models.common import MatterId
from mootloop.models.context import (
    AssembledContextItem,
    ContextContribution,
    ContextExclusion,
    ContextExclusionReason,
    CorpusSnapshot,
    RunContextManifest,
)
from mootloop.models.run import PersonaName

MAX_CONTEXT_ITEM_CHARS = 256 * 1024
MAX_CONTEXT_ITEMS = 256
MAX_CONTEXT_TOTAL_CHARS = 2 * 1024 * 1024
MAX_CORPUS_PASSAGE_CHARS = 16 * 1024
MAX_CORPUS_PASSAGES_PER_DOC = 2

_KIND_ORDER = {
    "fact": 0,
    "corpus_passage": 1,
    "context_note": 2,
    "firm_playbook": 3,
    "board": 4,
    "learning": 5,
}
_INTERNAL_CONTEXT_PERSONAS = frozenset({PersonaName.ASSOCIATE, PersonaName.PARTNER})


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_approved(contribution: ContextContribution) -> bool:
    if contribution.kind == "learning":
        return contribution.approval_state == "accepted"
    return contribution.approval_state == "approved"


def select_launch_contributions(
    candidates: Sequence[ContextContribution],
    *,
    matter_id: MatterId,
    task: str,
) -> tuple[tuple[ContextContribution, ...], tuple[ContextExclusion, ...]]:
    """Keep only launch-authorized candidates and audit exclusions without their text."""
    seen: set[str] = set()
    accepted: list[ContextContribution] = []
    excluded: list[ContextExclusion] = []
    for contribution in sorted(candidates, key=lambda item: (item.kind, item.contribution_id)):
        if contribution.contribution_id in seen:
            raise OrchestratorError(
                f"duplicate context contribution id {contribution.contribution_id!r}"
            )
        seen.add(contribution.contribution_id)
        reason: ContextExclusionReason | None = None
        if contribution.source_matter_id != matter_id:
            reason = "wrong_matter"
        elif contribution.task_scope and task not in contribution.task_scope:
            reason = "wrong_task"
        elif not _is_approved(contribution):
            reason = "not_approved"
        if reason is None:
            accepted.append(contribution)
        else:
            excluded.append(
                ContextExclusion(
                    contribution_id=contribution.contribution_id,
                    kind=contribution.kind,
                    reason=reason,
                )
            )
    excluded.sort(key=lambda item: item.contribution_id)
    return tuple(accepted), tuple(excluded)


def _fact_items(manifest: RunContextManifest) -> Iterable[AssembledContextItem]:
    for fact in manifest.facts:
        locator = f"facts/facts.jsonl#{fact.fact_id}@v{fact.version}"
        yield AssembledContextItem(
            context_id=f"fact:{fact.fact_id}:v{fact.version}",
            kind="fact",
            text=fact.statement,
            sha256=_sha(fact.statement),
            provenance_locator=locator,
            source_matter_id=manifest.matter_id,
            trust="untrusted_data",
            permission="matter_confidential",
        )


def _corpus_items(
    manifest: RunContextManifest, snapshot: CorpusSnapshot
) -> Iterable[AssembledContextItem]:
    docs = {str(doc.doc_id): doc for doc in manifest.corpus_manifest.docs}
    query_terms = {
        token
        for request_set in manifest.request_sets
        for request in request_set.items
        for token in re.findall(r"[a-z0-9]+", request.text.casefold())
        if len(token) >= 4
    }
    for captured in snapshot.documents:
        metadata = docs.get(str(captured.doc_id))
        if metadata is None:
            raise OrchestratorError(
                f"corpus context {captured.doc_id!r} has no captured manifest metadata"
            )
        ranges = [
            (start, min(start + MAX_CORPUS_PASSAGE_CHARS, len(captured.text)))
            for start in range(0, len(captured.text), MAX_CORPUS_PASSAGE_CHARS)
        ] or [(0, 0)]

        def score(bounds: tuple[int, int], captured_text: str = captured.text) -> tuple[int, int]:
            passage = captured_text[bounds[0] : bounds[1]].casefold()
            return -sum(passage.count(term) for term in query_terms), bounds[0]

        ranked = sorted(ranges, key=score)[:MAX_CORPUS_PASSAGES_PER_DOC]
        for start, end in sorted(ranked):
            passage = captured.text[start:end]
            yield AssembledContextItem(
                context_id=f"corpus:{captured.doc_id}:{start}-{end}",
                kind="corpus_passage",
                text=passage,
                sha256=_sha(passage),
                provenance_locator=f"{captured.locator}#chars={start}-{end}",
                source_matter_id=manifest.matter_id,
                trust="untrusted_data",
                permission=(
                    "matter_confidential" if metadata.privileged is False else "privileged"
                ),
            )


def _contribution_items(
    manifest: RunContextManifest,
) -> Iterable[AssembledContextItem]:
    sources = {
        (source.locator, source.sha256)
        for source in manifest.sources
        if source.kind == "context_contribution"
    }
    for contribution in manifest.context_contributions:
        if contribution.source_matter_id != manifest.matter_id:
            raise OrchestratorError(
                f"context contribution {contribution.contribution_id!r} crosses matter boundary"
            )
        if contribution.task_scope and manifest.task not in contribution.task_scope:
            raise OrchestratorError(
                f"context contribution {contribution.contribution_id!r} is outside run task"
            )
        if not _is_approved(contribution):
            raise OrchestratorError(
                f"context contribution {contribution.contribution_id!r} is not approved"
            )
        if (contribution.provenance_locator, contribution.sha256) not in sources:
            raise OrchestratorError(
                f"context contribution {contribution.contribution_id!r} has no exact source record"
            )
        yield AssembledContextItem(
            context_id=f"{contribution.kind}:{contribution.contribution_id}",
            kind=contribution.kind,
            text=contribution.text,
            sha256=contribution.sha256,
            provenance_locator=contribution.provenance_locator,
            source_matter_id=contribution.source_matter_id,
            task_scope=contribution.task_scope,
            persona_scope=contribution.persona_scope,
            trust=contribution.trust,
            permission=contribution.permission,
        )


def assemble_context(
    manifest: RunContextManifest, snapshot: CorpusSnapshot
) -> tuple[AssembledContextItem, ...]:
    """Combine immutable sources in a stable order and fail closed at explicit limits."""
    items = [
        *_fact_items(manifest),
        *_corpus_items(manifest, snapshot),
        *_contribution_items(manifest),
    ]
    items.sort(key=lambda item: (_KIND_ORDER[item.kind], item.context_id))
    if len(items) > MAX_CONTEXT_ITEMS:
        raise OrchestratorError(
            f"assembled context has {len(items)} items; context limit is {MAX_CONTEXT_ITEMS}. "
            "Select fewer evidence passages before running."
        )
    total = 0
    for item in items:
        size = len(item.text)
        if size > MAX_CONTEXT_ITEM_CHARS:
            raise OrchestratorError(
                f"context item {item.context_id!r} exceeds the per-item context limit "
                f"of {MAX_CONTEXT_ITEM_CHARS} characters; select a bounded passage"
            )
        total += size
    if total > MAX_CONTEXT_TOTAL_CHARS:
        raise OrchestratorError(
            f"assembled context has {total} characters; total context limit is "
            f"{MAX_CONTEXT_TOTAL_CHARS}. Select fewer evidence passages before running."
        )
    return tuple(items)


def items_for_turn(
    items: Sequence[AssembledContextItem],
    *,
    task: str,
    persona: PersonaName,
) -> tuple[AssembledContextItem, ...]:
    """Re-apply task/persona permissions at the final TurnSpec boundary."""
    if persona not in _INTERNAL_CONTEXT_PERSONAS:
        return tuple(
            item
            for item in items
            if item.persona_scope
            and persona in item.persona_scope
            and (not item.task_scope or task in item.task_scope)
        )
    return tuple(
        item
        for item in items
        if (not item.task_scope or task in item.task_scope)
        and (not item.persona_scope or persona in item.persona_scope)
    )
