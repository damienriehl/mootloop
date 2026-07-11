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
from pathlib import Path

from pydantic import ValidationError

from mootloop.errors import OrchestratorError
from mootloop.facts import FactStore
from mootloop.gates import degeneracy
from mootloop.journal import (
    append,
    load_state,
    write_turn_body,
)
from mootloop.llm import LLMProvider, RawTurnResult, TokenUsage, usd_equiv
from mootloop.models.events import (
    GateEvaluated,
    RunFinished,
    RunStarted,
    RunState,
    SpendRecorded,
    StageStarted,
    TurnCompleted,
    TurnDiscarded,
)
from mootloop.models.requests import RequestItem, RequestSet
from mootloop.models.run import (
    OUTPUT_SCHEMAS,
    DiscardedTurn,
    DraftOutput,
    TurnRecord,
    TurnSpec,
)
from mootloop.stages import StageContext, plan_request, render_prompt, request_complete
from mootloop.tasks import TaskBinding, get_binding
from mootloop.vault import RunLock, load_matter, safe_vault_path

DEFAULT_MAX_ATTEMPTS = 3


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
) -> StageContext:
    return StageContext(
        run_id=run_id,
        req_index=req_index,
        request=units[req_index],
        facts=facts,
        config=binding.config,
        adapter=binding.adapter,
        state=state,
        max_attempts=max_attempts,
    )


def _plan(
    run_id: str,
    state: RunState,
    binding: TaskBinding,
    units: list[RequestItem],
    facts: list[dict[str, str]],
    max_attempts: int,
) -> list[TurnSpec]:
    if state.status != "running":
        return []
    specs: list[TurnSpec] = []
    for i in range(len(units)):
        ctx = _context_for(run_id, state, binding, units, facts, i, max_attempts)
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
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> str:
    """Begin a run: write RunStarted under the run lock; finalize if there is no work."""
    binding = get_binding(task)
    matter = load_matter(vault_root)
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
            ),
        )
        units = load_request_units(vault_root)
        _finalize(vault_root, resolved_id, binding, units)
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
    facts = _load_facts(vault_root)
    return _plan(run_id, state, binding, units, facts, max_attempts)


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
        spec = _find_spec_in(_plan(run_id, state, binding, units, facts, max_attempts), turn_id)
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

    if spec.stage != state.current_stage:
        append(vault_root, run_id, StageStarted(stage=spec.stage))
    record = TurnRecord(
        spec=spec,
        output=output.model_dump(),
        gate_results=[gate],
        completed_at=now,
    )
    write_turn_body(vault_root, run_id, record)
    append(vault_root, run_id, TurnCompleted(record=record))
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
                usd_equiv=usd_equiv(usage),
            ),
        )
    _finalize(vault_root, run_id, binding, units)
    return record


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


# --- finalize + assemble ----------------------------------------------------


def _finalize(
    vault_root: Path | str,
    run_id: str,
    binding: TaskBinding,
    units: list[RequestItem],
) -> None:
    state = load_state(vault_root, run_id)
    if state.finished:
        return
    facts = _load_facts(vault_root)
    for i in range(len(units)):
        ctx = _context_for(run_id, state, binding, units, facts, i, DEFAULT_MAX_ATTEMPTS)
        if not request_complete(ctx):
            return
    _assemble(vault_root, run_id, binding, units, state)
    append(vault_root, run_id, RunFinished(status="finished"))


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
    with RunLock(vault_root, run_id):
        while True:
            state = load_state(vault_root, run_id)
            if state.finished:
                break
            units = load_request_units(vault_root)
            facts = _load_facts(vault_root)
            specs = _plan(run_id, state, binding, units, facts, max_attempts)
            if not specs:
                _finalize(vault_root, run_id, binding, units)
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
    return {
        "run_id": run_id,
        "task": state.task,
        "status": state.status,
        "finished": state.finished,
        "requests": len(units),
        "completed_turns": len(state.completed_turns),
        "discarded_turns": len(state.discarded),
        "total_spend_usd": round(state.total_spend_usd, 6),
        "current_stage": state.current_stage,
    }
