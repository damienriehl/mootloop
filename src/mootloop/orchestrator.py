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

import hashlib
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError

from mootloop import budget, decisions
from mootloop.citations import verify
from mootloop.citations.extract import extract_citations
from mootloop.citations.http import Transport
from mootloop.citations.ledger import DEFAULT_MAX_CACHE_AGE_DAYS
from mootloop.citations.ratelimit import TokenBucket
from mootloop.citations.verify import VerifySummary
from mootloop.errors import OrchestratorError
from mootloop.facts import FactStore
from mootloop.gates import completeness, degeneracy, fabrication
from mootloop.journal import (
    append,
    load_state,
    read_events,
    write_turn_body,
)
from mootloop.llm import LLMProvider, RawTurnResult, TokenUsage
from mootloop.models.budget import EstimateRange
from mootloop.models.citations import Citation
from mootloop.models.events import (
    CapRaised,
    CheckpointCleared,
    CheckpointReached,
    GateEvaluated,
    RunFinished,
    RunMode,
    RunStarted,
    RunState,
    SpendRecorded,
    StageStarted,
    TurnCompleted,
    TurnDiscarded,
)
from mootloop.models.gates import GateResult
from mootloop.models.requests import RequestItem, RequestSet, code_from_request_id
from mootloop.models.rubric import final_gate
from mootloop.models.run import (
    OUTPUT_SCHEMAS,
    DiscardedTurn,
    DraftOutput,
    RubricScoreOutput,
    TurnRecord,
    TurnSpec,
)
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


def _date_of(now: str) -> date:
    """The calendar date an injected ISO timestamp falls on (never ``datetime.now``)."""
    return datetime.fromisoformat(now).date()


def _tier_models(vault_root: Path | str) -> dict[str, str]:
    """The run's per-role model map, resolved from the matter's budget tier (D5)."""
    return budget.tier_models(load_matter(vault_root).budget.tier)


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


def _load_facts(vault_root: Path | str) -> list[dict[str, str]]:
    return [
        {"fact_id": f.fact_id, "statement": f.statement}
        for f in FactStore(vault_root).get_current()
    ]


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
    )


def _plan(
    run_id: str,
    state: RunState,
    binding: TaskBinding,
    units: list[RequestItem],
    facts: list[dict[str, str]],
    max_attempts: int,
    tier_models: dict[str, str] | None = None,
) -> list[TurnSpec]:
    if state.status != "running":
        return []
    specs: list[TurnSpec] = []
    for i in range(len(units)):
        ctx = _context_for(run_id, state, binding, units, facts, i, max_attempts, tier_models)
        specs.extend(plan_request(ctx))
    return specs


# --- public: start ----------------------------------------------------------


def _config_digest(binding: TaskBinding) -> str:
    raw = binding.config.model_dump_json().encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _compact_ts(now: str) -> str:
    return "".join(ch for ch in now if ch.isdigit())


def start_run(
    vault_root: Path | str,
    task: str,
    now: str,
    *,
    run_id: str | None = None,
    mode: RunMode | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> str:
    """Begin a run: write RunStarted under the run lock; finalize if there is no work.

    The run ``mode`` resolves ``--mode`` flag -> ``matter.yaml`` -> ``autonomous``
    (plan D12 precedence).
    """
    binding = get_binding(task)
    matter = load_matter(vault_root)
    resolved_mode: RunMode = mode or matter.run_mode
    resolved_id = run_id or f"{task}-{_compact_ts(now)}"
    with RunLock(vault_root, resolved_id):
        append(
            vault_root,
            resolved_id,
            RunStarted(
                run_id=resolved_id,
                matter_id=matter.matter_id,
                task=task,
                rubric_version=binding.config.rubric_id,
                config_digest=_config_digest(binding),
                mode=resolved_mode,
            ),
        )
        units = load_request_units(vault_root)
        _finalize(vault_root, resolved_id, binding, units, now)
    return resolved_id


# --- public: plan -----------------------------------------------------------


def plan_next(
    vault_root: Path | str,
    run_id: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> list[TurnSpec]:
    """The TurnSpecs that can execute now (per-request fan-out, cap-respecting)."""
    binding = _binding_for(vault_root, run_id)
    state = load_state(vault_root, run_id)
    units = load_request_units(vault_root)
    # Budget hard cap (plan D5): at/over cap, gracefully checkpoint before planning.
    if not state.finished and _over_cap(vault_root, state):
        with RunLock(vault_root, run_id):
            _cap_transition(vault_root, run_id, binding, units)
        return []
    facts = _load_facts(vault_root)
    return _plan(run_id, state, binding, units, facts, max_attempts, _tier_models(vault_root))


def find_spec(
    vault_root: Path | str,
    run_id: str,
    turn_id: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
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
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> str:
    """Render the full prompt for a currently-schedulable turn."""
    return render_prompt(find_spec(vault_root, run_id, turn_id, max_attempts=max_attempts))


# --- public: record ---------------------------------------------------------


def record_turn(
    vault_root: Path | str,
    run_id: str,
    turn_id: str,
    raw_text: str,
    usage: TokenUsage | None,
    now: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> TurnRecord | DiscardedTurn:
    """Validate -> degeneracy gate -> journal. Derailment => discard (never repair)."""
    binding = _binding_for(vault_root, run_id)
    with RunLock(vault_root, run_id):
        state = load_state(vault_root, run_id)
        if turn_id in state.completed_turns:
            return state.completed_turns[turn_id]  # idempotent
        units = load_request_units(vault_root)
        facts = _load_facts(vault_root)
        specs = _plan(run_id, state, binding, units, facts, max_attempts, _tier_models(vault_root))
        spec = _find_spec_in(specs, turn_id)
        return _record_spec(
            vault_root, run_id, spec, raw_text, usage, now, binding, units, state, max_attempts
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
) -> TurnRecord | DiscardedTurn:
    model_cls = OUTPUT_SCHEMAS[spec.output_schema_name]
    try:
        output = model_cls.model_validate_json(raw_text)
    except ValidationError as exc:
        return _discard(
            vault_root, run_id, spec, f"schema-invalid: {exc.error_count()} error(s)", max_attempts
        )

    gate = degeneracy.evaluate(output)  # type: ignore[arg-type]
    append(vault_root, run_id, GateEvaluated(turn_id=spec.turn_id, result=gate))
    if gate.status != "pass":
        reasons = "; ".join(f.code for f in gate.findings)
        return _discard(vault_root, run_id, spec, f"degenerate: {reasons}", max_attempts)

    gate_results: list[GateResult] = [gate]
    # Deterministic completeness gate on every draft (presence criteria; plan D7) —
    # recorded, never fatal, never sent to a judge.
    if isinstance(output, DraftOutput):
        comp = _completeness_gate(spec, output, binding, units)
        append(vault_root, run_id, GateEvaluated(turn_id=spec.turn_id, result=comp))
        gate_results.append(comp)
        # Fabrication gate on every draft/bolster turn (plan D12): every assertion must
        # trace to a fact or corpus. Recorded, non-fatal at turn time; blocks at export.
        fab = _fabrication_gate(vault_root, output)
        append(vault_root, run_id, GateEvaluated(turn_id=spec.turn_id, result=fab))
        gate_results.append(fab)

    if spec.stage != state.current_stage:
        append(vault_root, run_id, StageStarted(stage=spec.stage))
    record = TurnRecord(
        spec=spec,
        output=output.model_dump(),
        gate_results=gate_results,
        completed_at=now,
    )
    write_turn_body(vault_root, run_id, record)
    append(vault_root, run_id, TurnCompleted(record=record))
    # Attorney-gate decisions (plan P-28): every draft/bolster turn may imply gates.
    if isinstance(output, DraftOutput):
        decisions.derive_and_store(vault_root, run_id, spec, output, units)
    if usage is not None:
        append(
            vault_root,
            run_id,
            SpendRecorded(
                turn_id=spec.turn_id,
                input_tokens=usage.input_tokens,
                cache_read=usage.cache_read,
                cache_write=usage.cache_write,
                output_tokens=usage.output_tokens,
                model=usage.model,
                usd_equiv=budget.cost_of(usage, usage.model, _date_of(now)),
            ),
        )

    # Final rubric gate: aggregate the decorrelated panel once the last seat lands.
    _maybe_emit_rubric_gate(vault_root, run_id, spec, binding, units)

    # Budget hard cap (plan D5): graceful checkpoint before scheduling anything more.
    if _over_cap(vault_root, load_state(vault_root, run_id)):
        _cap_transition(vault_root, run_id, binding, units)
        _write_observed_status(vault_root, run_id, binding, units)
        return record

    _finalize(vault_root, run_id, binding, units, now)
    # Gated mode (plan Phase 5): pause at the next stage boundary or on open
    # policy-delegable decisions, once this turn leaves the run still running.
    _maybe_checkpoint(vault_root, run_id, binding, units)
    _write_observed_status(vault_root, run_id, binding, units)
    return record


def _completeness_gate(
    spec: TurnSpec,
    draft: DraftOutput,
    binding: TaskBinding,
    units: list[RequestItem],
) -> GateResult:
    request_id = str(spec.request_id) if spec.request_id else ""
    code = code_from_request_id(request_id)
    unit = next((u for u in units if str(u.request_id) == request_id), None)
    req_text = unit.text if unit else ""
    return completeness.evaluate(draft, binding.rubric, code, req_text)


def _fabrication_gate(vault_root: Path | str, draft: DraftOutput) -> GateResult:
    """Fabrication gate for one draft: assertions vs. current facts + corpus (plan D12)."""
    facts = FactStore(vault_root).get_current()
    corpus_text = fabrication.build_corpus_text(vault_root)
    return fabrication.check(draft, facts, corpus_text)


# --- citation verification (plan Phase 4) -----------------------------------


def _operative_citations(vault_root: Path | str, run_id: str) -> list[Citation]:
    """Every distinct citation in the run's operative (final) draft per request."""
    binding = _binding_for(vault_root, run_id)
    state = load_state(vault_root, run_id)
    units = load_request_units(vault_root)
    facts = _load_facts(vault_root)
    found: dict[str, Citation] = {}
    for i in range(len(units)):
        ctx = _context_for(run_id, state, binding, units, facts, i, DEFAULT_MAX_ATTEMPTS)
        record = ctx.operative_draft()
        if record is None:
            continue
        draft = DraftOutput.model_validate(record.output)
        texts = [draft.response_text, *draft.candidate_citations]
        for text in texts:
            for citation in extract_citations(text, source_turn_id=record.spec.turn_id):
                found.setdefault(citation.citation_id, citation)
    return list(found.values())


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
    append(vault_root, run_id, GateEvaluated(turn_id=f"{run_id}-citations", result=gate))
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
        run_id, state, binding, units, _load_facts(vault_root), idx, DEFAULT_MAX_ATTEMPTS
    )
    if not RubricGateStage().is_complete(ctx):
        return
    panel: list[dict[str, int]] = []
    for m in range(1, binding.config.panels.rubric_judges + 1):
        out = RubricScoreOutput.model_validate(ctx.record(ctx.layout.rubric_final(m)).output)
        panel.append({s.criterion_id: s.score for s in out.scores})
    result = final_gate(binding.rubric, panel, ctx.code, binding.config.rubric_threshold)
    append(vault_root, run_id, GateEvaluated(turn_id=spec.turn_id, result=result))


def _discard(
    vault_root: Path | str, run_id: str, spec: TurnSpec, reason: str, max_attempts: int
) -> DiscardedTurn:
    state = load_state(vault_root, run_id)
    attempt = state.discarded.get(spec.turn_id, 0) + 1
    append(vault_root, run_id, TurnDiscarded(turn_id=spec.turn_id, reason=reason, attempt=attempt))
    if attempt >= max_attempts:
        # Counter-capped: the run pauses, journal intact, never silently absorbed.
        append(vault_root, run_id, RunFinished(status="needs_attention"))
    return DiscardedTurn(turn_id=spec.turn_id, reason=reason, attempt=attempt)


# --- budget hard cap (plan D5) ----------------------------------------------


def _effective_cap(vault_root: Path | str, state: RunState) -> float | None:
    """The cap now in force: a ``CapRaised`` override wins over matter.yaml."""
    if state.cap_raised_to is not None:
        return state.cap_raised_to
    return load_matter(vault_root).budget.hard_cap_usd


def _over_cap(vault_root: Path | str, state: RunState) -> bool:
    cap = _effective_cap(vault_root, state)
    return cap is not None and state.total_spend_usd >= cap


def _cap_transition(
    vault_root: Path | str,
    run_id: str,
    binding: TaskBinding,
    units: list[RequestItem],
) -> None:
    """Graceful at-cap checkpoint: write a gaps report, then mark the run ``capped``
    (a resumable finished state a later ``raise-cap`` reopens)."""
    state = load_state(vault_root, run_id)
    if state.finished:
        return
    _write_gaps_report(vault_root, run_id, binding, units, state)
    append(vault_root, run_id, RunFinished(status="capped"))


def _write_gaps_report(
    vault_root: Path | str,
    run_id: str,
    binding: TaskBinding,
    units: list[RequestItem],
    state: RunState,
) -> Path:
    facts = _load_facts(vault_root)
    cap = _effective_cap(vault_root, state)
    lines: list[str] = [
        f"# Gaps report — run `{run_id}`",
        "",
        f"Run halted at the budget cap (${cap:.2f}) after "
        f"${state.total_spend_usd:.2f} of notional spend.",
        "",
    ]
    unfinished: list[tuple[str, str]] = []
    for i in range(len(units)):
        ctx = _context_for(run_id, state, binding, units, facts, i, DEFAULT_MAX_ATTEMPTS)
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
    lines.append(
        f"Raise the cap and resume: "
        f"`mootloop run raise-cap <vault> {run_id} --to <usd>`."
    )
    path = safe_vault_path(vault_root, "deliverables", f"gaps-{run_id}.md")
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def raise_cap(vault_root: Path | str, run_id: str, to_usd: float) -> None:
    """Append a ``CapRaised`` event, reopening a capped run to ``running`` (plan D5)."""
    with RunLock(vault_root, run_id):
        append(vault_root, run_id, CapRaised(to_usd=to_usd))


# --- finalize + assemble ----------------------------------------------------


def _all_requests_complete(
    vault_root: Path | str,
    run_id: str,
    binding: TaskBinding,
    units: list[RequestItem],
    state: RunState,
) -> bool:
    facts = _load_facts(vault_root)
    for i in range(len(units)):
        ctx = _context_for(run_id, state, binding, units, facts, i, DEFAULT_MAX_ATTEMPTS)
        if not request_complete(ctx):
            return False
    return True


def _finalize(
    vault_root: Path | str,
    run_id: str,
    binding: TaskBinding,
    units: list[RequestItem],
    now: str,
) -> None:
    """Once every request is complete, assemble the DRAFT deliverable, then either
    finish or block on open hard-human attorney gates (plan Phase 5).

    ``needs_decisions`` is treated as still-finalizable: resolving the last hard-human
    gate re-enters here (via ``finalize_if_ready``) and flips the run to ``finished``.
    """
    state = load_state(vault_root, run_id)
    if state.status not in ("running", "needs_decisions"):
        return  # finished / needs_attention / capped / checkpoint are handled elsewhere
    if not _all_requests_complete(vault_root, run_id, binding, units, state):
        return
    # The md-master is a DRAFT until attestation; assemble it now so it exists for the
    # gate ledger and attestation hash even while decisions are pending.
    _assemble(vault_root, run_id, binding, units, state)
    matter = load_matter(vault_root)
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
    binding = get_binding(state.task)
    units = load_request_units(vault_root)
    _finalize(vault_root, run_id, binding, units, now)
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
    "finished": "done",
}


def state_marker(status: str) -> str:
    """Map a run status to its trailing ``STATE:`` marker (house convention)."""
    return _STATE_MARKER.get(status, "working")


def _maybe_checkpoint(
    vault_root: Path | str,
    run_id: str,
    binding: TaskBinding,
    units: list[RequestItem],
) -> None:
    """Gated mode: pause the run when it is uniformly poised to enter a checkpoint
    stage, or (once) while policy-delegable decisions are open."""
    state = load_state(vault_root, run_id)
    if state.mode != "gated" or state.status != "running":
        return
    facts = _load_facts(vault_root)
    tier = _tier_models(vault_root)
    specs = _plan(run_id, state, binding, units, facts, DEFAULT_MAX_ATTEMPTS, tier)
    if specs:
        stages = {s.stage for s in specs}
        if len(stages) == 1:
            (stage,) = tuple(stages)
            if stage in _CHECKPOINT_STAGE_ORDER and stage not in state.cleared_checkpoints:
                append(vault_root, run_id, CheckpointReached(boundary=stage))
                return
    matter = load_matter(vault_root)
    if "policy_decisions" not in state.cleared_checkpoints and decisions.open_by_taxonomy(
        vault_root, run_id, matter, "policy-delegable"
    ):
        append(vault_root, run_id, CheckpointReached(boundary="policy_decisions"))


def continue_run(vault_root: Path | str, run_id: str) -> None:
    """Clear a gated checkpoint (``mootloop run continue``) so the run resumes."""
    with RunLock(vault_root, run_id):
        events = read_events(vault_root, run_id)
        state = load_state(vault_root, run_id)
        if state.status != "checkpoint":
            raise OrchestratorError(f"run {run_id!r} is not paused at a checkpoint")
        boundary = "unknown"
        for event in events:
            if isinstance(event, CheckpointReached):
                boundary = event.boundary
        append(vault_root, run_id, CheckpointCleared(boundary=boundary))


def _write_observed_status(
    vault_root: Path | str,
    run_id: str,
    binding: TaskBinding,
    units: list[RequestItem],
) -> None:
    """Observed mode: overwrite ``runs/<run-id>/STATUS.md`` (a derived view)."""
    state = load_state(vault_root, run_id)
    if state.mode != "observed":
        return
    path = safe_vault_path(vault_root, "runs", run_id, "STATUS.md")
    atomic_write_text(path, _render_status_md(vault_root, run_id, binding, units, state))


def _render_status_md(
    vault_root: Path | str,
    run_id: str,
    binding: TaskBinding,
    units: list[RequestItem],
    state: RunState,
) -> str:
    matter = load_matter(vault_root)
    facts = _load_facts(vault_root)
    lines: list[str] = [
        f"# Run status — `{run_id}`",
        "",
        f"- Matter: `{matter.matter_id}`",
        f"- Task: `{state.task}`  ·  Mode: `{state.mode}`  ·  Status: `{state.status}`",
        f"- Spend so far: ${state.total_spend_usd:.4f} (notional)",
        "",
        "## Stage progress",
        "",
        "| request | stage |",
        "| --- | --- |",
    ]
    for i in range(len(units)):
        ctx = _context_for(run_id, state, binding, units, facts, i, DEFAULT_MAX_ATTEMPTS)
        stage = first_incomplete_stage(ctx) or "complete"
        lines.append(f"| `{units[i].request_id}` | {stage} |")
    open_decisions = decisions.DecisionStore(vault_root, run_id).list_open()
    lines += ["", "## Open decisions", ""]
    if not open_decisions:
        lines.append("_none_")
    else:
        for decision in open_decisions:
            mode = decisions.gate_mode_for(matter, decision.kind)
            lines.append(f"- `{decision.decision_id}` [{mode}] {decision.proposal.summary}")
    lines += ["", f"STATE: {state_marker(state.status)}", ""]
    return "\n".join(lines)


def _assemble(
    vault_root: Path | str,
    run_id: str,
    binding: TaskBinding,
    units: list[RequestItem],
    state: RunState,
) -> Path:
    """Write the deliverable: a markdown master with one fenced anchor per request."""
    facts = _load_facts(vault_root)
    lines: list[str] = [
        f"# Discovery Responses — {binding.config.task}",
        "",
        f"Run: `{run_id}` · Requests: {len(units)} · Rubric: {binding.config.rubric_id}",
        "",
    ]
    for i in range(len(units)):
        request = units[i]
        ctx = _context_for(run_id, state, binding, units, facts, i, DEFAULT_MAX_ATTEMPTS)
        record = ctx.operative_draft()
        draft = DraftOutput.model_validate(record.output) if record else None
        lines.append(f"::: {{#resp-{request.request_id}}}")
        lines.append(f"## {request.request_id}")
        lines.append("")
        lines.append(draft.response_text if draft else "_no response drafted_")
        if draft and draft.objections:
            lines.append("")
            lines.append("**Objections**")
            for objection in draft.objections:
                lines.append(f"- {objection.basis} — {objection.text}")
        lines.append("")
        lines.append(":::")
        lines.append("")
    deliverable = binding.config.deliverables[0] if binding.config.deliverables else "draft.md"
    path = safe_vault_path(vault_root, "deliverables", deliverable)
    from mootloop.vault import atomic_write_text

    atomic_write_text(path, "\n".join(lines))
    return path


# --- public: drive (fake/headless provider) ---------------------------------


def run_with_provider(
    vault_root: Path | str,
    run_id: str,
    provider: LLMProvider,
    now: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    max_concurrency: int = 1,
) -> RunState:
    """Drive plan_next/record_turn to completion via ``provider`` (sync in v1)."""
    binding = _binding_for(vault_root, run_id)
    tier_models = _tier_models(vault_root)
    with RunLock(vault_root, run_id):
        while True:
            state = load_state(vault_root, run_id)
            if state.finished:
                break
            units = load_request_units(vault_root)
            if _over_cap(vault_root, state):
                _cap_transition(vault_root, run_id, binding, units)
                break
            facts = _load_facts(vault_root)
            specs = _plan(run_id, state, binding, units, facts, max_attempts, tier_models)
            if not specs:
                _finalize(vault_root, run_id, binding, units, now)
                _write_observed_status(vault_root, run_id, binding, units)
                break
            for spec in specs:
                fresh = load_state(vault_root, run_id)
                if fresh.finished or spec.turn_id in fresh.completed_turns:
                    continue
                result: RawTurnResult = provider.run_turn(spec, render_prompt(spec))
                _record_spec(
                    vault_root,
                    run_id,
                    spec,
                    result.text,
                    result.usage,
                    now,
                    binding,
                    units,
                    fresh,
                    max_attempts,
                )
    return load_state(vault_root, run_id)


# --- internals --------------------------------------------------------------


def _binding_for(vault_root: Path | str, run_id: str) -> TaskBinding:
    state = load_state(vault_root, run_id)
    if state.task is None:
        raise OrchestratorError(f"run {run_id!r} has no RunStarted event")
    return get_binding(state.task)


def _find_spec_in(specs: list[TurnSpec], turn_id: str) -> TurnSpec:
    for spec in specs:
        if spec.turn_id == turn_id:
            return spec
    raise OrchestratorError(f"turn {turn_id!r} is not currently schedulable")


def status_summary(vault_root: Path | str, run_id: str) -> dict[str, object]:
    """A machine-readable status snapshot for the ``status`` CLI verb / skill loop."""
    state = load_state(vault_root, run_id)
    units = load_request_units(vault_root)
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
        "total_tokens": total_tokens,
        "input_tokens": state.total_input_tokens,
        "cache_read_tokens": state.total_cache_read,
        "cache_write_tokens": state.total_cache_write,
        "output_tokens": state.total_output_tokens,
        "spend_usd": round(state.total_spend_usd, 6),
        "spend_label": "notional (plan mode)",
        "hard_cap_usd": _effective_cap(vault_root, state),
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
