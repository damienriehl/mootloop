"""The orchestrator state machine (plan D1). Pure mechanics; zero real LLM calls.

v1 runs inside Claude Code, where Python cannot call the session's Agent tool, so
the core is a *stepwise* machine a driver polls:

    start_run -> [plan_next -> (driver executes each spec) -> record_turn]* -> finished

All run state is the fold of the journal, so ``resume just works``: a killed run
re-reads its journal and continues; completed turns replay from disk and are never
re-executed. Three drivers share this one path: FakeLLMProvider (tests), the
``mootloop run`` CLI loop, and the Claude Code skill.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from mootloop import budget, decisions
from mootloop.citations import verify
from mootloop.citations.extract import extract_citations
from mootloop.citations.http import Transport
from mootloop.citations.ledger import DEFAULT_MAX_CACHE_AGE_DAYS
from mootloop.citations.ratelimit import TokenBucket
from mootloop.citations.verify import VerifySummary
from mootloop.context import (
    RunContext,
    build_run_context,
    config_digest,
    load_run_context,
    load_run_corpus,
    resolve_launch_config,
    write_run_context,
)
from mootloop.context_assembly import assemble_context, select_launch_contributions
from mootloop.errors import OrchestratorError
from mootloop.gates.turn import TurnGateContext, evaluate_turn_gates
from mootloop.journal import (
    append,
    load_state,
    read_events,
    write_turn_body,
)
from mootloop.llm import LLMProvider, TokenUsage
from mootloop.models.budget import EstimateRange
from mootloop.models.citations import Citation
from mootloop.models.common import (
    MatterId,
    RubricId,
    RunId,
    TaskSpecId,
    TaskSpecLockId,
    TurnId,
)
from mootloop.models.context import AssembledContextItem, ContextContribution
from mootloop.models.events import (
    CapRaised,
    CheckpointCleared,
    CheckpointReached,
    GateEvaluated,
    QueueIntent,
    RunFinished,
    RunMode,
    RunPaused,
    RunReopened,
    RunResumed,
    RunStarted,
    RunState,
    SpendRecorded,
    StageStarted,
    TurnCompleted,
    TurnDiscarded,
    TurnIntent,
    validate_run_queue_intent,
)
from mootloop.models.gates import GateResult
from mootloop.models.requests import RequestItem, RequestSet, code_from_request_id
from mootloop.models.rubric import final_gate
from mootloop.models.run import (
    OUTPUT_SCHEMAS,
    AttentionBlocker,
    DiscardedTurn,
    DraftOutput,
    RubricScoreOutput,
    TurnOutput,
    TurnRecord,
    TurnSpec,
)
from mootloop.provider_driver import assemble as _assemble
from mootloop.provider_driver import write_observed_status as _write_observed_status
from mootloop.stages import (
    RUBRIC_GATE_STAGE,
    RubricGateStage,
    StageContext,
    first_incomplete_stage,
    plan_request,
    render_prompt,
    request_complete,
)
from mootloop.tasks import TaskBinding, get_binding
from mootloop.vault import RunLock, atomic_write_text, load_matter, safe_vault_path

DEFAULT_MAX_ATTEMPTS = 3


def _launch_max_attempts(run_context: RunContext, requested: int | None) -> int:
    committed = run_context.manifest.resolved_config.max_attempts
    if requested is not None and requested != committed:
        raise OrchestratorError(
            f"max_attempts is launch-bound at {committed}; requested {requested}"
        )
    return committed


def _date_of(now: str) -> date:
    """The calendar date an injected ISO timestamp falls on (never ``datetime.now``)."""
    return datetime.fromisoformat(now).date()


# --- vault reads ------------------------------------------------------------


def load_request_units(vault_root: Path | str) -> list[RequestItem]:
    """Every top-level served request across all parsed sets, in stable order."""
    requests_dir = safe_vault_path(vault_root, "requests")
    if not requests_dir.is_dir():
        return []
    units: list[RequestItem] = []
    for path in sorted(requests_dir.glob("*.json")):
        request_set = RequestSet.model_validate_json(path.read_text(encoding="utf-8"))
        units.extend(item for item in request_set.items if item.subpart is None)
    units.sort(key=lambda i: (i.set_number, i.number))
    return units


# --- context construction ---------------------------------------------------


def _context_for(
    run_id: str,
    state: RunState,
    binding: TaskBinding,
    units: list[RequestItem],
    facts: list[dict[str, str]],
    req_index: int,
    max_attempts: int,
    tier_models: dict[str, str] | None = None,
    assembled_context: tuple[AssembledContextItem, ...] = (),
) -> StageContext:
    return StageContext(
        run_id=run_id,
        req_index=req_index,
        request=units[req_index],
        facts=facts,
        config=binding.config,
        adapter=binding.adapter,
        rubric=binding.rubric,
        state=state,
        max_attempts=max_attempts,
        tier_models=tier_models or {},
        assembled_context=assembled_context,
    )


def _plan(
    run_id: str,
    state: RunState,
    binding: TaskBinding,
    units: list[RequestItem],
    facts: list[dict[str, str]],
    max_attempts: int,
    tier_models: dict[str, str] | None = None,
    assembled_context: tuple[AssembledContextItem, ...] = (),
) -> list[TurnSpec]:
    if state.status != "running":
        return []
    specs: list[TurnSpec] = []
    for i in range(len(units)):
        ctx = _context_for(
            run_id,
            state,
            binding,
            units,
            facts,
            i,
            max_attempts,
            tier_models,
            assembled_context,
        )
        specs.extend(plan_request(ctx))
    return specs


# --- public: start ----------------------------------------------------------


def _compact_ts(now: str) -> str:
    return "".join(ch for ch in now if ch.isdigit())


def _same_queue_intent(existing: QueueIntent | None, proposed: QueueIntent | None) -> bool:
    """Compare launch work identity while allowing an HTTP retry's later wall clock.

    ``enqueued_at`` controls FIFO ordering but not what work the caller launched. The
    first committed timestamp remains authoritative and is what recovery materializes.
    """
    if existing is None or proposed is None:
        return existing is proposed
    return (
        existing.item_id,
        existing.lane,
        existing.kind,
        existing.payload,
        existing.payload_sha256,
    ) == (
        proposed.item_id,
        proposed.lane,
        proposed.kind,
        proposed.payload,
        proposed.payload_sha256,
    )


def start_run(
    vault_root: Path | str,
    task: str,
    now: str,
    *,
    run_id: str | None = None,
    mode: RunMode | None = None,
    task_spec_id: str | None = None,
    max_attempts: int | None = None,
    idempotent: bool = False,
    firm_preferences_path: Path | str | None = None,
    context_contributions: Sequence[ContextContribution] = (),
    queue_intent: QueueIntent | None = None,
) -> str:
    """Begin a run: write RunStarted under the run lock; finalize if there is no work.

    The run ``mode`` resolves ``--mode`` flag -> ``matter.yaml`` -> ``autonomous``
    (plan D12 precedence). ``task_spec_id`` records the on-ramp TaskSpec the run started
    from (plan FE-2.5), when any.
    """
    resolved_id = run_id or f"{task}-{_compact_ts(now)}"
    with RunLock(vault_root, resolved_id):
        existing_events = read_events(vault_root, resolved_id)
        if existing_events:
            if idempotent:
                started = [event for event in existing_events if isinstance(event, RunStarted)]
                context = load_run_context(vault_root, resolved_id)
                matter = load_matter(vault_root)
                try:
                    if queue_intent is not None:
                        queue_intent = validate_run_queue_intent(
                            queue_intent,
                            matter_id=matter.matter_id,
                            run_id=resolved_id,
                        )
                except (ValidationError, ValueError) as exc:
                    raise OrchestratorError("invalid hosted run queue intent") from exc
                proposed = resolve_launch_config(
                    vault_root,
                    task,
                    matter,
                    mode=mode,
                    max_attempts=max_attempts,
                    firm_preferences_path=firm_preferences_path,
                )
                accepted_contributions, context_exclusions = select_launch_contributions(
                    context_contributions,
                    matter_id=MatterId(matter.matter_id),
                    task=task,
                )
                same_launch = (
                    context.manifest.task == task
                    and (
                        str(context.manifest.task_spec.task_spec_id)
                        if context.manifest.task_spec is not None
                        else None
                    )
                    == task_spec_id
                    and context.manifest.resolved_config == proposed
                    and context.manifest.context_contributions == list(accepted_contributions)
                    and context.manifest.context_exclusions == list(context_exclusions)
                    and len(started) == 1
                    and _same_queue_intent(started[0].queue_intent, queue_intent)
                )
                if same_launch:
                    return resolved_id
                raise OrchestratorError(
                    f"run {resolved_id!r} already exists with a different launch context"
                )
            raise OrchestratorError(f"run {resolved_id!r} has already started")
        binding = get_binding(task)
        matter = load_matter(vault_root)
        try:
            if queue_intent is not None:
                queue_intent = validate_run_queue_intent(
                    queue_intent,
                    matter_id=matter.matter_id,
                    run_id=resolved_id,
                )
        except (ValidationError, ValueError) as exc:
            raise OrchestratorError("invalid hosted run queue intent") from exc
        run_context = build_run_context(
            vault_root,
            resolved_id,
            task,
            binding,
            matter,
            mode,
            max_attempts,
            task_spec_id,
            firm_preferences_path,
            context_contributions,
        )
        resolved_config = run_context.manifest.resolved_config
        task_spec_lock = run_context.manifest.task_spec_lock
        context_manifest_sha256 = write_run_context(vault_root, run_context)
        append(
            vault_root,
            resolved_id,
            RunStarted(
                run_id=RunId(resolved_id),
                matter_id=MatterId(matter.matter_id),
                task=task,
                rubric_version=RubricId(resolved_config.rubric_id),
                config_digest=config_digest(resolved_config),
                context_manifest_sha256=context_manifest_sha256,
                mode=resolved_config.run_mode,
                task_spec_id=TaskSpecId(task_spec_id) if task_spec_id is not None else None,
                task_spec_lock_id=(
                    TaskSpecLockId(task_spec_lock.task_spec_lock_id)
                    if task_spec_lock is not None
                    else None
                ),
                task_spec_lock_sha256=(
                    task_spec_lock.record_sha256 if task_spec_lock is not None else None
                ),
                queue_intent=queue_intent,
            ),
        )
        _finalize(vault_root, resolved_id, now, run_context)
    return resolved_id


# --- public: plan -----------------------------------------------------------


def plan_next(
    vault_root: Path | str,
    run_id: str,
    *,
    max_attempts: int | None = None,
) -> list[TurnSpec]:
    """The TurnSpecs that can execute now (per-request fan-out, cap-respecting)."""
    run_context = load_run_context(vault_root, run_id)
    binding = run_context.binding
    max_attempts = _launch_max_attempts(run_context, max_attempts)
    state = load_state(vault_root, run_id)
    # A paused run schedules nothing and short-circuits the cap check (plan FE-1).
    if state.status == "paused":
        return []
    units = run_context.units
    # Budget hard cap (plan D5): at/over cap, gracefully checkpoint before planning.
    if not state.finished and _over_cap(state, run_context):
        with RunLock(vault_root, run_id):
            _cap_transition(vault_root, run_id, run_context)
        return []
    return _plan(
        run_id,
        state,
        binding,
        units,
        run_context.facts,
        max_attempts,
        run_context.manifest.tier_models,
        assemble_context(run_context.manifest, load_run_corpus(vault_root, run_context)),
    )


def find_spec(
    vault_root: Path | str,
    run_id: str,
    turn_id: str,
    *,
    max_attempts: int | None = None,
) -> TurnSpec:
    for spec in plan_next(vault_root, run_id, max_attempts=max_attempts):
        if spec.turn_id == turn_id:
            return spec
    raise OrchestratorError(f"turn {turn_id!r} is not schedulable in run {run_id!r}")


def assemble_prompt(
    vault_root: Path | str,
    run_id: str,
    turn_id: str,
    *,
    max_attempts: int | None = None,
) -> str:
    """Render the full prompt for a currently-schedulable turn."""
    run_context = load_run_context(vault_root, run_id)
    spec = find_spec(vault_root, run_id, turn_id, max_attempts=max_attempts)
    return render_prompt(spec, run_context.manifest.persona_bodies[spec.persona])


def record_turn_intent(vault_root: Path | str, run_id: str, event: TurnIntent) -> None:
    """Commit provider spend intent only against a currently valid launch context."""
    with RunLock(vault_root, run_id):
        load_run_context(vault_root, run_id)
        append(vault_root, run_id, event)


def finish_needs_attention(vault_root: Path | str, run_id: str) -> None:
    """Record a recoverable terminal state only against a valid launch context."""
    with RunLock(vault_root, run_id):
        load_run_context(vault_root, run_id)
        append(vault_root, run_id, RunFinished(status="needs_attention"))


# --- public: record ---------------------------------------------------------


def record_turn(
    vault_root: Path | str,
    run_id: str,
    turn_id: str,
    raw_text: str,
    usage: TokenUsage | None,
    now: str,
    *,
    max_attempts: int | None = None,
    provider_call_id: str | None = None,
) -> TurnRecord | DiscardedTurn:
    """Validate -> degeneracy gate -> journal. Derailment => discard (never repair)."""
    with RunLock(vault_root, run_id):
        run_context = load_run_context(vault_root, run_id)
        binding = run_context.binding
        max_attempts = _launch_max_attempts(run_context, max_attempts)
        state = load_state(vault_root, run_id)
        if turn_id in state.completed_turns:
            record = state.completed_turns[turn_id]
            # Idempotent for the RECORD, never for the money: this call reached a
            # provider and burned tokens even though the slot was already filled (a
            # lost lease, a re-drained queue item). Booking is suppressed only for an
            # exact replay of a result already on the ledger — see `_book_spend`.
            _book_spend(
                vault_root,
                run_id,
                turn_id,
                usage,
                record.spec.model,
                now,
                dedupe=True,
                provider_call_id=provider_call_id,
            )
            return record
        units = run_context.units
        facts = run_context.facts
        specs = _plan(
            run_id,
            state,
            binding,
            units,
            facts,
            max_attempts,
            run_context.manifest.tier_models,
            assemble_context(
                run_context.manifest,
                load_run_corpus(vault_root, run_context),
            ),
        )
        spec = _find_spec_in(specs, turn_id)
        return _record_spec(
            vault_root,
            run_id,
            spec,
            raw_text,
            usage,
            now,
            binding,
            units,
            state,
            max_attempts,
            provider_call_id,
            run_context,
        )


def _book_spend(
    vault_root: Path | str,
    run_id: str,
    turn_id: str,
    usage: TokenUsage | None,
    model: str | None,
    now: str,
    *,
    dedupe: bool = False,
    provider_call_id: str | None = None,
) -> None:
    """Append the ``SpendRecorded`` for one provider call.

    ``model`` is the model the run PLANNED (``spec.model``) — the identity
    ``TurnIntent.max_plausible_usd`` reserved against — so the write-ahead reservation
    and its settlement can never be priced off two different keys; ``usage.model``
    still records what the provider reported.

    ``dedupe`` guards the one path that can be reached without a fresh provider call:
    re-recording an already-completed turn. New providers identify the invocation
    directly. Legacy callers without an identity retain the historical usage-signature
    fallback so old integrations and journals remain idempotent.
    """
    if usage is None:
        return
    if dedupe:
        prior = (
            e
            for e in read_events(vault_root, run_id)
            if isinstance(e, SpendRecorded) and e.turn_id == turn_id
        )
        if provider_call_id is not None:
            if any(e.provider_call_id == provider_call_id for e in prior):
                return
        else:
            signature = (
                usage.input_tokens,
                usage.cache_read,
                usage.cache_write,
                usage.output_tokens,
                usage.model,
            )
            if any(
                (
                    e.input_tokens,
                    e.cache_read,
                    e.cache_write,
                    e.output_tokens,
                    e.model,
                )
                == signature
                for e in prior
            ):
                return
    append(
        vault_root,
        run_id,
        SpendRecorded(
            turn_id=TurnId(turn_id),
            input_tokens=usage.input_tokens,
            cache_read=usage.cache_read,
            cache_write=usage.cache_write,
            output_tokens=usage.output_tokens,
            model=usage.model,
            usd_equiv=budget.cost_of(usage, model or usage.model, _date_of(now)),
            provider_call_id=provider_call_id,
        ),
    )


def _record_spec(
    vault_root: Path | str,
    run_id: str,
    spec: TurnSpec,
    raw_text: str,
    usage: TokenUsage | None,
    now: str,
    binding: TaskBinding,
    units: list[RequestItem],
    state: RunState,
    max_attempts: int,
    provider_call_id: str | None,
    run_context: RunContext,
) -> TurnRecord | DiscardedTurn:
    model_cls = OUTPUT_SCHEMAS[spec.output_schema_name]
    try:
        output = cast(TurnOutput, model_cls.model_validate_json(raw_text))
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()[:6]
        )
        return _discard(
            vault_root,
            run_id,
            spec,
            f"schema-invalid: {exc.error_count()} error(s)",
            max_attempts,
            usage=usage,
            now=now,
            detail=f"your previous output failed `{spec.output_schema_name}` validation — {detail}",
            provider_call_id=provider_call_id,
        )

    gate_run = evaluate_turn_gates(
        tuple(binding.config.gates),
        _turn_gate_context(vault_root, spec, output, binding, units, run_context),
    )
    for result in gate_run.results:
        append(vault_root, run_id, GateEvaluated(turn_id=spec.turn_id, result=result))
    if gate_run.halted_by is not None:
        gate = gate_run.results[-1]
        reasons = "; ".join(f.code for f in gate.findings)
        detail = "; ".join(f"{f.code}: {f.message}" for f in gate.findings[:6])
        return _discard(
            vault_root,
            run_id,
            spec,
            f"degenerate: {reasons}",
            max_attempts,
            usage=usage,
            now=now,
            detail=f"your previous output was discarded by the {gate.gate} gate — {detail}",
            provider_call_id=provider_call_id,
        )

    gate_results = list(gate_run.results)

    if spec.stage != state.current_stage:
        append(vault_root, run_id, StageStarted(stage=spec.stage))
    record = TurnRecord(
        spec=spec,
        output=output.model_dump(),
        gate_results=gate_results,
        completed_at=now,
    )
    record = write_turn_body(vault_root, run_id, record)
    append(vault_root, run_id, TurnCompleted(record=record))
    # Attorney-gate decisions (plan P-28): every draft/bolster turn may imply gates.
    if isinstance(output, DraftOutput):
        decisions.derive_and_store(vault_root, run_id, spec, output, units)
    _book_spend(
        vault_root,
        run_id,
        spec.turn_id,
        usage,
        spec.model,
        now,
        provider_call_id=provider_call_id,
    )

    # Final rubric gate: aggregate the decorrelated panel once the last seat lands.
    _maybe_emit_rubric_gate(vault_root, run_id, spec, binding, units, run_context)

    # Budget hard cap (plan D5): graceful checkpoint before scheduling anything more.
    if _over_cap(load_state(vault_root, run_id), run_context):
        _cap_transition(vault_root, run_id, run_context)
        _write_observed_status(vault_root, run_id, run_context)
        return record

    _finalize(vault_root, run_id, now, run_context)
    # Gated mode (plan Phase 5): pause at the next stage boundary or on open
    # policy-delegable decisions, once this turn leaves the run still running.
    _maybe_checkpoint(vault_root, run_id, run_context)
    _write_observed_status(vault_root, run_id, run_context)
    return record


def _turn_gate_context(
    vault_root: Path | str,
    spec: TurnSpec,
    output: TurnOutput,
    binding: TaskBinding,
    units: list[RequestItem],
    run_context: RunContext,
) -> TurnGateContext:
    request_id = str(spec.request_id) if spec.request_id else ""
    code = code_from_request_id(request_id)
    unit = next((u for u in units if str(u.request_id) == request_id), None)
    req_text = unit.text if unit else ""
    corpus_text = ""
    if isinstance(output, DraftOutput):
        snapshot = load_run_corpus(vault_root, run_context)
        corpus_text = "\n".join(item.text for item in snapshot.documents)
    return TurnGateContext(
        output=output,
        rubric=binding.rubric,
        request_code=code,
        request_text=req_text,
        facts=tuple(run_context.manifest.facts),
        corpus_text=corpus_text,
    )


# --- citation verification (plan Phase 4) -----------------------------------


def _operative_citations(vault_root: Path | str, run_id: str) -> list[Citation]:
    """Every distinct citation in the run's operative (final) draft per request."""
    run_context = load_run_context(vault_root, run_id)
    binding = run_context.binding
    state = load_state(vault_root, run_id)
    units = run_context.units
    facts = run_context.facts
    found: dict[str, Citation] = {}
    for i in range(len(units)):
        ctx = _context_for(
            run_id,
            state,
            binding,
            units,
            facts,
            i,
            run_context.manifest.resolved_config.max_attempts,
        )
        record = ctx.operative_draft()
        if record is None:
            continue
        draft = DraftOutput.model_validate(record.output)
        texts = [draft.response_text, *draft.candidate_citations]
        for text in texts:
            for citation in extract_citations(text, source_turn_id=record.spec.turn_id):
                found.setdefault(citation.citation_id, citation)
    return list(found.values())


def operative_draft_turn_ids(vault_root: Path | str, run_id: str) -> dict[str, str]:
    """``request_id -> turn_id`` of each request's operative (final) draft.

    The gate ledger uses this to answer "which draft's gate verdict governs export":
    the one whose text would actually be served, not every draft the run ever made.
    """
    run_context = load_run_context(vault_root, run_id)
    binding = run_context.binding
    state = load_state(vault_root, run_id)
    units = run_context.units
    facts = run_context.facts
    out: dict[str, str] = {}
    for i in range(len(units)):
        ctx = _context_for(
            run_id,
            state,
            binding,
            units,
            facts,
            i,
            run_context.manifest.resolved_config.max_attempts,
        )
        record = ctx.operative_draft()
        if record is not None:
            out[str(units[i].request_id)] = record.spec.turn_id
    return out


def operative_drafts(
    vault_root: Path | str, run_id: str
) -> list[tuple[RequestItem, DraftOutput | None]]:
    """Each request paired with its operative (final, post-restructure) draft, or None
    if the request never produced one. The export builders read from here (plan Phase 7)."""
    run_context = load_run_context(vault_root, run_id)
    binding = run_context.binding
    state = load_state(vault_root, run_id)
    units = run_context.units
    facts = run_context.facts
    out: list[tuple[RequestItem, DraftOutput | None]] = []
    for i in range(len(units)):
        ctx = _context_for(
            run_id,
            state,
            binding,
            units,
            facts,
            i,
            run_context.manifest.resolved_config.max_attempts,
        )
        record = ctx.operative_draft()
        out.append((units[i], DraftOutput.model_validate(record.output) if record else None))
    return out


def verify_run_citations(
    vault_root: Path | str,
    run_id: str,
    now: str,
    *,
    max_cache_age_days: int = DEFAULT_MAX_CACHE_AGE_DAYS,
    limiter: TokenBucket | None = None,
    transport: Transport | None = None,
) -> VerifySummary:
    """Explicit verification step (between bolster and the final gate): extract every
    citation from the run's operative drafts, verify via the router, journal the gate."""
    citations = _operative_citations(vault_root, run_id)
    summary = verify.verify_all(
        vault_root,
        citations,
        now,
        max_cache_age_days=max_cache_age_days,
        limiter=limiter,
        transport=transport,
    )
    gate = verify.citation_gate(
        vault_root, citations, now=now, max_cache_age_days=max_cache_age_days
    )
    append(
        vault_root,
        run_id,
        GateEvaluated(turn_id=TurnId(f"{run_id}-citations"), result=gate),
    )
    return summary


def citation_export_gate(
    vault_root: Path | str,
    run_id: str,
    now: str,
    *,
    max_cache_age_days: int = DEFAULT_MAX_CACHE_AGE_DAYS,
) -> GateResult:
    """The export-readiness citation gate: reads the immutable ledger (no HTTP) and
    blocks unless every citation in the operative drafts is verified/curated (plan H8)."""
    citations = _operative_citations(vault_root, run_id)
    return verify.citation_gate(
        vault_root, citations, now=now, max_cache_age_days=max_cache_age_days
    )


def _maybe_emit_rubric_gate(
    vault_root: Path | str,
    run_id: str,
    spec: TurnSpec,
    binding: TaskBinding,
    units: list[RequestItem],
    run_context: RunContext,
) -> None:
    """When the final rubric seat lands, aggregate the panel (median-per-criterion,
    weighted) into a single ``rubric`` GateEvaluated event (plan D6)."""
    if spec.stage != RUBRIC_GATE_STAGE:
        return
    state = load_state(vault_root, run_id)
    idx = next((i for i, u in enumerate(units) if u.request_id == spec.request_id), None)
    if idx is None:
        return
    ctx = _context_for(
        run_id,
        state,
        binding,
        units,
        run_context.facts,
        idx,
        run_context.manifest.resolved_config.max_attempts,
    )
    if not RubricGateStage().is_complete(ctx):
        return
    panel: list[dict[str, int]] = []
    for m in range(1, binding.config.panels.rubric_judges + 1):
        out = RubricScoreOutput.model_validate(ctx.record(ctx.layout.rubric_final(m)).output)
        panel.append({s.criterion_id: s.score for s in out.scores})
    result = final_gate(binding.rubric, panel, ctx.code, binding.config.rubric_threshold)
    append(vault_root, run_id, GateEvaluated(turn_id=spec.turn_id, result=result))


def effective_max_attempts(state: RunState, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> int:
    """The run's per-turn retry ceiling: the driver's ``max_attempts`` plus every extra
    attempt granted by a ``RunReopened`` (``mootloop run reopen --grant-attempts``)."""
    return max_attempts + state.attempts_granted


def _discard(
    vault_root: Path | str,
    run_id: str,
    spec: TurnSpec,
    reason: str,
    max_attempts: int,
    *,
    usage: TokenUsage | None = None,
    now: str,
    detail: str = "",
    provider_call_id: str | None = None,
) -> DiscardedTurn:
    # A discarded turn is thrown away, but it was not free: the provider ran and
    # billed for it. Book it BEFORE the discard so the cap sees the money even on the
    # attempt that trips `max_attempts`, and so the turn's write-ahead `TurnIntent`
    # reservation is reconciled by a real settlement instead of lingering as pending.
    _book_spend(
        vault_root,
        run_id,
        spec.turn_id,
        usage,
        spec.model,
        now,
        provider_call_id=provider_call_id,
    )
    state = load_state(vault_root, run_id)
    attempt = state.discarded.get(spec.turn_id, 0) + 1
    append(
        vault_root,
        run_id,
        TurnDiscarded(turn_id=spec.turn_id, reason=reason, attempt=attempt, detail=detail),
    )
    if attempt >= effective_max_attempts(state, max_attempts):
        # Counter-capped: the run pauses, journal intact, never silently absorbed.
        append(vault_root, run_id, RunFinished(status="needs_attention"))
    return DiscardedTurn(turn_id=spec.turn_id, reason=reason, attempt=attempt)


# --- budget hard cap (plan D5) ----------------------------------------------


def effective_cap(state: RunState, run_context: RunContext) -> float | None:
    """The cap now in force: a ``CapRaised`` override wins over launch context."""
    if state.cap_raised_to is not None:
        return state.cap_raised_to
    return run_context.manifest.resolved_config.budget.hard_cap_usd


def _over_cap(state: RunState, run_context: RunContext) -> bool:
    # Conservative cap (plan FD-6): count every unreconciled write-ahead intent at its
    # max-plausible cost, so an in-flight turn presses against the cap until it settles.
    projected = state.total_spend_usd + sum(state.pending_intents.values())
    cap = effective_cap(state, run_context)
    return cap is not None and projected >= cap


def _cap_transition(
    vault_root: Path | str,
    run_id: str,
    run_context: RunContext,
) -> None:
    """Graceful at-cap checkpoint: write a gaps report, then mark the run ``capped``
    (a resumable finished state a later ``raise-cap`` reopens)."""
    state = load_state(vault_root, run_id)
    if state.finished:
        return
    _write_gaps_report(vault_root, run_id, state, run_context)
    append(vault_root, run_id, RunFinished(status="capped"))


def _write_gaps_report(
    vault_root: Path | str,
    run_id: str,
    state: RunState,
    run_context: RunContext,
) -> Path:
    binding = run_context.binding
    units = run_context.units
    facts = run_context.facts
    cap = effective_cap(state, run_context)
    lines: list[str] = [
        f"# Gaps report — run `{run_id}`",
        "",
        f"Run halted at the budget cap (${cap:.2f}) after "
        f"${state.total_spend_usd:.2f} of notional spend.",
        "",
    ]
    unfinished: list[tuple[str, str]] = []
    for i in range(len(units)):
        ctx = _context_for(
            run_id,
            state,
            binding,
            units,
            facts,
            i,
            run_context.manifest.resolved_config.max_attempts,
        )
        if request_complete(ctx):
            continue
        stopped = first_incomplete_stage(ctx) or "unknown"
        unfinished.append((str(units[i].request_id), stopped))
    if not unfinished:
        lines.append("All requests completed before the cap was reached.")
    else:
        lines.append(f"**{len(unfinished)} request(s) unfinished:**")
        lines.append("")
        for request_id, stage in unfinished:
            lines.append(f"- `{request_id}` — stopped at stage `{stage}`")
    lines.append("")
    lines.append(f"Raise the cap and resume: `mootloop run raise-cap <vault> {run_id} --to <usd>`.")
    path = safe_vault_path(vault_root, "deliverables", f"gaps-{run_id}.md")
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def raise_cap(vault_root: Path | str, run_id: str, to_usd: float) -> None:
    """Append a ``CapRaised`` event, reopening a capped run to ``running`` (plan D5)."""
    with RunLock(vault_root, run_id):
        load_run_context(vault_root, run_id)
        append(vault_root, run_id, CapRaised(to_usd=to_usd))


# --- finalize + assemble ----------------------------------------------------


def _all_requests_complete(
    vault_root: Path | str,
    run_id: str,
    binding: TaskBinding,
    units: list[RequestItem],
    state: RunState,
    facts: list[dict[str, str]],
    max_attempts: int,
) -> bool:
    for i in range(len(units)):
        ctx = _context_for(run_id, state, binding, units, facts, i, max_attempts)
        if not request_complete(ctx):
            return False
    return True


def _finalize(
    vault_root: Path | str,
    run_id: str,
    now: str,
    run_context: RunContext,
) -> None:
    """Once every request is complete, assemble the DRAFT deliverable, then either
    finish or block on open hard-human attorney gates (plan Phase 5).

    ``needs_decisions`` is treated as still-finalizable: resolving the last hard-human
    gate re-enters here (via ``finalize_if_ready``) and flips the run to ``finished``.
    """
    state = load_state(vault_root, run_id)
    if state.status not in ("running", "needs_decisions"):
        return  # finished / needs_attention / capped / checkpoint are handled elsewhere
    binding = run_context.binding
    units = run_context.units
    if not _all_requests_complete(
        vault_root,
        run_id,
        binding,
        units,
        state,
        run_context.facts,
        run_context.manifest.resolved_config.max_attempts,
    ):
        return
    # The md-master is a DRAFT until attestation; assemble it now so it exists for the
    # gate ledger and attestation hash even while decisions are pending.
    _assemble(vault_root, run_id, state, run_context)
    matter = run_context.manifest.matter_config
    if decisions.open_by_taxonomy(vault_root, run_id, matter, "hard-human"):
        if state.status != "needs_decisions":
            append(vault_root, run_id, RunFinished(status="needs_decisions"))
        return
    append(vault_root, run_id, RunFinished(status="finished"))


def finalize_if_ready(
    vault_root: Path | str,
    run_id: str,
    now: str,
) -> RunState:
    """Re-run finalization after a decision resolves (plan Phase 5). Caller holds the
    run lock. Reopens a ``needs_decisions`` run to ``finished`` once the last
    hard-human gate clears."""
    state = load_state(vault_root, run_id)
    if state.task is None or state.status not in ("running", "needs_decisions"):
        return state
    run_context = load_run_context(vault_root, run_id)
    _finalize(vault_root, run_id, now, run_context)
    return load_state(vault_root, run_id)


# --- gated checkpoints + observed status (plan Phase 5) ---------------------

# Stage boundaries a gated run pauses before (after associate_draft completes ->
# before partner_loop; before oc_attack; before judge_panel).
_CHECKPOINT_STAGE_ORDER: tuple[str, ...] = ("partner_loop", "oc_attack", "judge_panel")

# Run status -> the house STATE marker (plan Phase 5 / D12 convention).
_STATE_MARKER: dict[str, str] = {
    "running": "working",
    "needs_decisions": "ask-pending",
    "checkpoint": "ask-pending",
    "needs_attention": "blocked",
    "capped": "blocked",
    "paused": "blocked",
    "finished": "done",
}


def state_marker(status: str) -> str:
    """Map a run status to its trailing ``STATE:`` marker (house convention)."""
    return _STATE_MARKER.get(status, "working")


def _maybe_checkpoint(
    vault_root: Path | str,
    run_id: str,
    run_context: RunContext,
) -> None:
    """Gated mode: pause the run when it is uniformly poised to enter a checkpoint
    stage, or (once) while policy-delegable decisions are open."""
    state = load_state(vault_root, run_id)
    if state.mode != "gated" or state.status != "running":
        return
    binding = run_context.binding
    units = run_context.units
    facts = run_context.facts
    max_attempts = run_context.manifest.resolved_config.max_attempts
    tier_models = run_context.manifest.tier_models
    specs = _plan(run_id, state, binding, units, facts, max_attempts, tier_models)
    if specs:
        stages = {s.stage for s in specs}
        if len(stages) == 1:
            (stage,) = tuple(stages)
            if stage in _CHECKPOINT_STAGE_ORDER and stage not in state.cleared_checkpoints:
                append(vault_root, run_id, CheckpointReached(boundary=stage))
                return
    matter = run_context.manifest.matter_config
    if "policy_decisions" not in state.cleared_checkpoints and decisions.open_by_taxonomy(
        vault_root, run_id, matter, "policy-delegable"
    ):
        append(vault_root, run_id, CheckpointReached(boundary="policy_decisions"))


def pause_run(vault_root: Path | str, run_id: str, reason: str = "manual") -> None:
    """Pause a live run (plan FE-1): append ``RunPaused`` so the worker stops ticking.

    Refuses to pause a terminally-complete run (``finished`` / ``needs_attention`` /
    ``capped``) — a paused run must be resumable, and those states are not."""
    with RunLock(vault_root, run_id):
        load_run_context(vault_root, run_id)
        state = load_state(vault_root, run_id)
        if state.is_terminal:
            raise OrchestratorError(f"run {run_id!r} is complete ({state.status}); cannot pause")
        append(vault_root, run_id, RunPaused(reason=reason))


def resume_run(vault_root: Path | str, run_id: str) -> None:
    """Resume a paused run (plan FE-1): append ``RunResumed`` so it reopens to running."""
    with RunLock(vault_root, run_id):
        load_run_context(vault_root, run_id)
        state = load_state(vault_root, run_id)
        if state.status != "paused":
            raise OrchestratorError(f"run {run_id!r} is not paused")
        append(vault_root, run_id, RunResumed())


def continue_run(vault_root: Path | str, run_id: str) -> None:
    """Clear a gated checkpoint (``mootloop run continue``) so the run resumes."""
    with RunLock(vault_root, run_id):
        load_run_context(vault_root, run_id)
        events = read_events(vault_root, run_id)
        state = load_state(vault_root, run_id)
        if state.status != "checkpoint":
            raise OrchestratorError(f"run {run_id!r} is not paused at a checkpoint")
        boundary = "unknown"
        for event in events:
            if isinstance(event, CheckpointReached):
                boundary = event.boundary
        append(vault_root, run_id, CheckpointCleared(boundary=boundary))


# --- needs-attention reopen (the operator's un-block verb) ------------------


def attention_blockers(
    vault_root: Path | str,
    run_id: str,
    *,
    max_attempts: int | None = None,
    grant_attempts: int = 0,
) -> list[AttentionBlocker]:
    """Everything still blocking a ``needs_attention`` run, folded from the journal.

    A blocker is a turn that burned its whole retry budget without ever completing.
    ``grant_attempts`` asks the *prospective* question ``reopen`` needs — "would this
    grant clear the block?" — by raising the ceiling the check measures against.
    """
    run_context = load_run_context(vault_root, run_id)
    launch_attempts = _launch_max_attempts(run_context, max_attempts)
    state = load_state(vault_root, run_id)
    ceiling = effective_max_attempts(state, launch_attempts) + grant_attempts
    return [
        AttentionBlocker(
            kind="counter_capped_turn",
            ref=turn_id,
            detail=(
                f"{attempts} discarded attempt(s) against a ceiling of {ceiling}; "
                "no completed turn recorded"
            ),
        )
        for turn_id, attempts in sorted(state.discarded.items())
        if attempts >= ceiling and turn_id not in state.completed_turns
    ]


def reopen_run(
    vault_root: Path | str,
    run_id: str,
    *,
    reason: str,
    grant_attempts: int = 0,
    reopened_by: str = "operator",
    max_attempts: int | None = None,
) -> RunState:
    """Reopen a ``needs_attention`` run to ``running`` (``mootloop run reopen``).

    ``needs_attention`` is the one halt with no self-clearing signal: a counter-capped
    turn or a driver auth/provider failure is fixed *outside* the run (a persona body,
    a rotated key, a config change), so nothing in the journal ever flips it back. This
    verb is that flip — explicit, logged, and refusing to paper over a live blocker.

    Preconditions:
      - the run is actually ``needs_attention`` (never a way to un-finish a run);
      - ``reason`` is non-empty — it is the audit trail;
      - every counter-capped turn is cleared, either because it since completed or
        because ``grant_attempts`` restores its retry budget.
    """
    reason = reason.strip()
    if not reason:
        raise OrchestratorError("reopen requires a non-empty reason (it is the audit trail)")
    if grant_attempts < 0:
        raise OrchestratorError("grant_attempts must be >= 0")
    with RunLock(vault_root, run_id):
        load_run_context(vault_root, run_id)
        state = load_state(vault_root, run_id)
        if state.status != "needs_attention":
            raise OrchestratorError(
                f"run {run_id!r} is not blocked on attention (status={state.status!r})"
            )
        blockers = attention_blockers(
            vault_root, run_id, max_attempts=max_attempts, grant_attempts=grant_attempts
        )
        if blockers:
            listed = "; ".join(f"{b.ref} ({b.detail})" for b in blockers)
            raise OrchestratorError(
                f"run {run_id!r} still has {len(blockers)} unresolved blocker(s) "
                f"caused by spent retry budget: {listed}. Grant retry budget with "
                "--grant-attempts N "
                "after fixing what derailed the turn."
            )
        append(
            vault_root,
            run_id,
            RunReopened(
                reason=reason,
                grant_attempts=grant_attempts,
                reopened_by=reopened_by,
            ),
        )
    return load_state(vault_root, run_id)


def reopen_enqueue_pending(vault_root: Path | str, run_id: str) -> bool:
    """Whether the journal committed a reopen but no later run event exists yet.

    This narrow predicate lets the hosted API replay only the queue half of its
    journal-then-queue operation after a queue failure. It does not make the reopen
    transition itself generally idempotent.
    """
    load_run_context(vault_root, run_id)
    events = read_events(vault_root, run_id)
    return bool(
        events
        and isinstance(events[-1], RunReopened)
        and load_state(vault_root, run_id).status == "running"
    )


# --- public: drive (fake/headless provider) ---------------------------------


def run_with_provider(
    vault_root: Path | str,
    run_id: str,
    provider: LLMProvider,
    now: str,
    *,
    max_attempts: int | None = None,
    max_concurrency: int = 1,
) -> RunState:
    """Drive plan_next/record_turn to completion via ``provider`` (sync in v1)."""
    from mootloop.provider_driver import run_with_provider as drive

    return drive(
        vault_root,
        run_id,
        provider,
        now,
        max_attempts=max_attempts,
        max_concurrency=max_concurrency,
    )


def _find_spec_in(specs: list[TurnSpec], turn_id: str) -> TurnSpec:
    for spec in specs:
        if spec.turn_id == turn_id:
            return spec
    raise OrchestratorError(f"turn {turn_id!r} is not currently schedulable")


def status_summary(vault_root: Path | str, run_id: str) -> dict[str, object]:
    """A machine-readable status snapshot for the ``status`` CLI verb / skill loop."""
    state = load_state(vault_root, run_id)
    context_blocker: str | None = None
    try:
        run_context = load_run_context(vault_root, run_id)
    except OrchestratorError as exc:
        run_context = None
        context_blocker = str(exc)
    units = run_context.units if run_context is not None else []
    total_tokens = (
        state.total_input_tokens
        + state.total_cache_read
        + state.total_cache_write
        + state.total_output_tokens
    )
    # v1 drives everything through the fake/seat provider, so spend is notional
    # (plan quota, not billed) — one mechanism, two labels (plan D5).
    open_decisions = decisions.DecisionStore(vault_root, run_id).list_open()
    return {
        "run_id": run_id,
        "task": state.task,
        "mode": state.mode,
        "status": state.status,
        "finished": state.finished,
        "requests": len(units),
        "completed_turns": len(state.completed_turns),
        "discarded_turns": len(state.discarded),
        "open_decisions": [d.decision_id for d in open_decisions],
        # What a `needs_attention` run must clear before `run reopen` will restart it
        # (empty for every other status — and for a driver-halted run with no
        # counter-capped turn, which reopens on the operator's reason alone).
        "attention_blockers": [
            b.model_dump(mode="json")
            for b in (
                attention_blockers(vault_root, run_id)
                if state.status == "needs_attention" and run_context is not None
                else []
            )
        ],
        "total_tokens": total_tokens,
        "input_tokens": state.total_input_tokens,
        "cache_read_tokens": state.total_cache_read,
        "cache_write_tokens": state.total_cache_write,
        "output_tokens": state.total_output_tokens,
        "spend_usd": round(state.total_spend_usd, 6),
        "spend_label": "notional (plan mode)",
        "hard_cap_usd": (
            effective_cap(state, run_context) if run_context is not None else state.cap_raised_to
        ),
        "replayable": run_context is not None,
        "context_blocker": context_blocker,
        "current_stage": state.current_stage,
    }


def estimate_run_cost(
    vault_root: Path | str,
    task: str,
    tier: str,
    on: date,
) -> EstimateRange:
    """A pre-run cost range + per-stage breakdown for a task at a tier (plan D5)."""
    binding = get_binding(task)
    units = load_request_units(vault_root)
    return budget.estimate_run(len(units), binding.config, tier, on)


def matter_tier(vault_root: Path | str) -> str:
    """The matter's configured budget tier (the estimate default)."""
    return load_matter(vault_root).budget.tier
