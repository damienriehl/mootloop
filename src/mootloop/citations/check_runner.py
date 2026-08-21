"""Prepare and execute durable cite-checker turns for one completed run."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from mootloop import budget, orchestrator
from mootloop.citations import http
from mootloop.citations.check import (
    PropositionEvidenceBundle,
    build_check_spec,
    proposition_gate,
    validate_check_result,
)
from mootloop.citations.courtlistener_opinions import (
    OpinionAuthorityStore,
    fetch_case_authority,
    select_passages,
)
from mootloop.citations.ledger import DEFAULT_MAX_CACHE_AGE_DAYS, ResearchQueue, VerificationLedger
from mootloop.citations.propositions import PropositionLedger
from mootloop.context import load_run_context
from mootloop.errors import CitationError
from mootloop.journal import append, load_state, write_turn_body
from mootloop.llm import LLMProvider, TokenUsage
from mootloop.models.citations import (
    CitationProposition,
    OpinionAuthorityStoreRecord,
    ResearchRequest,
    VerificationStatus,
)
from mootloop.models.common import CitationId, RunId, TurnId
from mootloop.models.events import GateEvaluated, TurnCompleted, TurnIntent
from mootloop.models.run import CiteCheckOutput, PersonaName, TurnRecord
from mootloop.provider_driver import _run_turn_with_lock_renewal
from mootloop.stages import render_prompt
from mootloop.vault import RunLock


@dataclass
class PreparedPropositionChecks:
    propositions: list[CitationProposition] = field(default_factory=list)
    bundles: list[PropositionEvidenceBundle] = field(default_factory=list)
    unresolved: list[CitationProposition] = field(default_factory=list)
    research_request_ids: list[str] = field(default_factory=list)


class CitationLeaseLostError(CitationError):
    """The citation worker no longer owns its durable queue item."""


def _renew(heartbeat: Callable[[], bool] | None) -> None:
    if heartbeat is not None and not heartbeat():
        raise CitationLeaseLostError("citation proposition queue lease was lost")


def require_completed_draft_set(vault_root: Path | str, run_id: str) -> None:
    """Fail closed unless drafting is complete and no provider turn can race it."""
    load_run_context(vault_root, run_id)
    state = load_state(vault_root, run_id)
    if state.status not in {"finished", "needs_decisions"}:
        raise CitationError(
            "citation proposition checks require a completed draft set; "
            f"{run_id!r} is {state.status}"
        )


def _research_id(proposition: CitationProposition) -> str:
    suffix = str(proposition.proposition_id).removeprefix("prop-")
    return f"research-prop-{suffix}"


def _queue_research(
    vault_root: Path | str,
    proposition: CitationProposition,
    reason: str,
) -> str:
    queue = ResearchQueue(vault_root)
    request_id = _research_id(proposition)
    if queue.get(request_id) is None:
        queue.append(
            ResearchRequest(
                request_id=request_id,
                citation_id=proposition.citation_id,
                normalized=proposition.normalized_citation,
                reason=reason,
            )
        )
    return request_id


def prepare_run_proposition_checks(
    vault_root: Path | str,
    run_id: str,
    now: str,
    *,
    max_cache_age_days: int = DEFAULT_MAX_CACHE_AGE_DAYS,
    transport: http.Transport | None = None,
    extra_heartbeat: Callable[[], bool] | None = None,
) -> PreparedPropositionChecks:
    """Resolve verified case cites to immutable public-text evidence bundles."""
    require_completed_draft_set(vault_root, run_id)
    propositions = orchestrator.operative_citation_propositions(vault_root, run_id)
    verified = VerificationLedger(vault_root).folded(
        now=datetime.fromisoformat(now), max_cache_age_days=max_cache_age_days
    )
    prepared = PreparedPropositionChecks(propositions=propositions)
    store = OpinionAuthorityStore(vault_root)
    captured: dict[CitationId, OpinionAuthorityStoreRecord] = {}
    for proposition in propositions:
        _renew(extra_heartbeat)
        verification = verified.get(proposition.citation_id)
        if (
            verification is None
            or verification.status != VerificationStatus.VERIFIED
            or verification.source != "courtlistener"
            or verification.source_url is None
        ):
            prepared.unresolved.append(proposition)
            prepared.research_request_ids.append(
                _queue_research(
                    vault_root,
                    proposition,
                    "exact authority text unavailable for automated proposition checking",
                )
            )
            continue
        prior_authority = captured.get(proposition.citation_id)
        if prior_authority is not None:
            prepared.bundles.append(
                PropositionEvidenceBundle(
                    run_id=RunId(run_id),
                    proposition=proposition,
                    authority=prior_authority,
                    passages=select_passages(prior_authority, proposition),
                )
            )
            continue
        try:
            result = fetch_case_authority(
                citation_id=proposition.citation_id,
                source_url=verification.source_url,
                fetched_at=now,
                transport=transport,
                heartbeat=lambda: _renew(extra_heartbeat),
            )
        except CitationError as exc:
            fetched = None
            note = str(exc)
        else:
            fetched = result
            note = result.note
        if fetched is None or fetched.snapshot is None:
            prepared.unresolved.append(proposition)
            prepared.research_request_ids.append(
                _queue_research(vault_root, proposition, note or "authority text unavailable")
            )
            continue
        store.capture(fetched.snapshot)
        _renew(extra_heartbeat)
        captured[proposition.citation_id] = fetched.snapshot
        prepared.bundles.append(
            PropositionEvidenceBundle(
                run_id=RunId(run_id),
                proposition=proposition,
                authority=fetched.snapshot,
                passages=select_passages(fetched.snapshot, proposition),
            )
        )
    return prepared


def _record_completed_check(
    vault_root: Path | str,
    run_id: str,
    bundle: PropositionEvidenceBundle,
    spec_model: str | None,
    raw_text: str,
    now: str,
    *,
    usage: TokenUsage | None,
    provider_call_id: str | None,
) -> None:
    output, verification = validate_check_result(bundle, raw_text, checked_at=now)
    state = load_state(vault_root, run_id)
    spec = build_check_spec(run_id, bundle, model=spec_model)
    if spec.turn_id not in state.completed_turns:
        record = write_turn_body(
            vault_root,
            run_id,
            TurnRecord(
                spec=spec,
                output=output.model_dump(mode="json"),
                completed_at=now,
            ),
        )
        append(vault_root, run_id, TurnCompleted(record=record))
        orchestrator._book_spend(
            vault_root,
            run_id,
            spec.turn_id,
            usage,
            spec.model,
            now,
            provider_call_id=provider_call_id,
        )
    ledger = PropositionLedger(vault_root)
    if (
        ledger.get(
            verification.run_id,
            verification.proposition_id,
            verification.source_text_sha256,
            verification.authority_sha256,
        )
        is None
    ):
        ledger.append(verification)


def run_proposition_checks(
    vault_root: Path | str,
    run_id: str,
    provider: LLMProvider,
    now: str,
    *,
    billing_mode: Literal["subscription", "api"] = "subscription",
    transport: http.Transport | None = None,
    extra_heartbeat: Callable[[], bool] | None = None,
) -> PreparedPropositionChecks:
    """Fetch bounded public evidence, run journaled checks, and record the run gate."""
    prepared = prepare_run_proposition_checks(
        vault_root,
        run_id,
        now,
        transport=transport,
        extra_heartbeat=extra_heartbeat,
    )
    run_context = load_run_context(vault_root, run_id)
    body = run_context.manifest.persona_bodies.get(PersonaName.CITE_CHECKER)
    if body is None and prepared.bundles:
        raise CitationError("run context does not contain a snapshotted cite-checker body")
    model = run_context.manifest.tier_models.get(PersonaName.CITE_CHECKER.role)
    store = OpinionAuthorityStore(vault_root)
    for bundle in prepared.bundles:
        ledger = PropositionLedger(vault_root)
        if ledger.get(
            bundle.run_id,
            bundle.proposition.proposition_id,
            bundle.proposition.source_text_sha256,
            bundle.authority.content_sha256,
        ):
            continue
        spec = build_check_spec(run_id, bundle, model=model)
        state = load_state(vault_root, run_id)
        completed = state.completed_turns.get(spec.turn_id)
        if completed is not None:
            raw = CiteCheckOutput.model_validate(completed.output).model_dump_json()
            _record_completed_check(
                vault_root,
                run_id,
                bundle,
                model,
                raw,
                now,
                usage=None,
                provider_call_id=None,
            )
            continue
        with RunLock(vault_root, run_id) as lock:
            current = load_state(vault_root, run_id)
            if orchestrator._over_cap(current, load_run_context(vault_root, run_id)):
                prepared.unresolved.append(bundle.proposition)
                continue
            if spec.turn_id not in current.pending_intents:
                resolved_model = spec.model or "claude"
                append(
                    vault_root,
                    run_id,
                    TurnIntent(
                        turn_id=spec.turn_id,
                        model=resolved_model,
                        billing_mode=billing_mode,
                        max_plausible_usd=budget.max_plausible_cost(
                            resolved_model, datetime.fromisoformat(now).date()
                        ),
                    ),
                )
            assert body is not None
            result = _run_turn_with_lock_renewal(
                lock,
                provider,
                spec,
                render_prompt(spec, body),
                extra_heartbeat=extra_heartbeat,
            )
            load_run_context(vault_root, run_id)
            store.load(bundle.proposition.citation_id, bundle.authority.content_sha256)
            _record_completed_check(
                vault_root,
                run_id,
                bundle,
                model,
                result.text,
                now,
                usage=result.usage,
                provider_call_id=result.provider_call_id,
            )
    gate = proposition_gate(
        vault_root,
        prepared.bundles,
        unresolved=prepared.unresolved,
    )
    digest = hashlib.sha256(
        "\n".join(sorted(str(p.proposition_id) for p in prepared.propositions)).encode("utf-8")
    ).hexdigest()[:12]
    append(
        vault_root,
        run_id,
        GateEvaluated(
            turn_id=TurnId(f"{run_id}-citation-propositions-{digest}"), result=gate
        ),
    )
    combined_gate = orchestrator.citation_export_gate(
        vault_root,
        run_id,
        now,
    )
    append(
        vault_root,
        run_id,
        GateEvaluated(
            turn_id=TurnId(f"{run_id}-citations"),
            result=combined_gate,
        ),
    )
    return prepared
