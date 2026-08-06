"""Synchronous provider driver and its lifecycle output helpers.

The orchestrator owns the state-machine primitives.  This module composes those
primitives for the headless/fake-provider execution path without making that
optional driver part of the already-large state-machine module.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from mootloop import decisions
from mootloop.journal import load_state
from mootloop.models.run import DraftOutput
from mootloop.stages import first_incomplete_stage, render_prompt
from mootloop.vault import RunLock, atomic_write_text, load_matter, safe_vault_path

if TYPE_CHECKING:
    from mootloop.llm import LLMProvider
    from mootloop.models.events import RunState
    from mootloop.models.requests import RequestItem
    from mootloop.tasks import TaskBinding


def run_with_provider(
    vault_root: Path | str,
    run_id: str,
    provider: LLMProvider,
    now: str,
    *,
    max_attempts: int,
    max_concurrency: int = 1,
) -> RunState:
    """Drive the orchestrator primitives to completion via a sync provider."""
    # Import lazily: orchestrator exposes the stable public wrapper for this driver.
    from mootloop import orchestrator

    del max_concurrency  # Reserved for the v1-compatible public API.
    binding = orchestrator._binding_for(vault_root, run_id)
    tier_models = orchestrator._tier_models(vault_root)
    with RunLock(vault_root, run_id) as lock:
        while True:
            lock.heartbeat(best_effort=True)
            state = load_state(vault_root, run_id)
            if state.finished:
                break
            units = orchestrator.load_request_units(vault_root)
            if orchestrator._over_cap(vault_root, state):
                orchestrator._cap_transition(vault_root, run_id, binding, units)
                break
            facts = orchestrator._load_facts(vault_root)
            specs = orchestrator._plan(
                run_id, state, binding, units, facts, max_attempts, tier_models
            )
            if not specs:
                orchestrator._finalize(vault_root, run_id, binding, units, now)
                write_observed_status(vault_root, run_id, binding, units)
                break
            for spec in specs:
                fresh = load_state(vault_root, run_id)
                if fresh.finished or spec.turn_id in fresh.completed_turns:
                    continue
                lock.heartbeat(best_effort=True)
                result = provider.run_turn(spec, render_prompt(spec))
                lock.heartbeat(best_effort=True)
                orchestrator._record_spec(
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
                    result.provider_call_id,
                )
    return load_state(vault_root, run_id)


def write_observed_status(
    vault_root: Path | str,
    run_id: str,
    binding: TaskBinding,
    units: list[RequestItem],
) -> None:
    """Overwrite the derived observed-mode status view."""
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
    from mootloop import orchestrator

    matter = load_matter(vault_root)
    facts = orchestrator._load_facts(vault_root)
    lines = [
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
    for index, unit in enumerate(units):
        context = orchestrator._context_for(
            run_id,
            state,
            binding,
            units,
            facts,
            index,
            orchestrator.DEFAULT_MAX_ATTEMPTS,
        )
        lines.append(f"| `{unit.request_id}` | {first_incomplete_stage(context) or 'complete'} |")
    open_decisions = decisions.DecisionStore(vault_root, run_id).list_open()
    lines += ["", "## Open decisions", ""]
    if not open_decisions:
        lines.append("_none_")
    else:
        for decision in open_decisions:
            mode = decisions.gate_mode_for(matter, decision.kind)
            lines.append(f"- `{decision.decision_id}` [{mode}] {decision.proposal.summary}")
    lines += ["", f"STATE: {orchestrator.state_marker(state.status)}", ""]
    return "\n".join(lines)


def assemble(
    vault_root: Path | str,
    run_id: str,
    binding: TaskBinding,
    units: list[RequestItem],
    state: RunState,
) -> Path:
    """Write the markdown deliverable with one fenced anchor per request."""
    from mootloop import orchestrator

    facts = orchestrator._load_facts(vault_root)
    lines = [
        f"# Discovery Responses — {binding.config.task}",
        "",
        f"Run: `{run_id}` · Requests: {len(units)} · Rubric: {binding.config.rubric_id}",
        "",
    ]
    for index, request in enumerate(units):
        context = orchestrator._context_for(
            run_id, state, binding, units, facts, index, orchestrator.DEFAULT_MAX_ATTEMPTS
        )
        record = context.operative_draft()
        draft = DraftOutput.model_validate(record.output) if record else None
        lines.extend(
            [
                f"::: {{#resp-{request.request_id}}}",
                f"## {request.request_id}",
                "",
                draft.response_text if draft else "_no response drafted_",
            ]
        )
        if draft and draft.objections:
            lines.extend(["", "**Objections**"])
            for objection in draft.objections:
                lines.append(f"- {objection.basis} — {objection.text}")
        lines.extend(["", ":::", ""])
    deliverable = binding.config.deliverables[0] if binding.config.deliverables else "draft.md"
    path = safe_vault_path(vault_root, "deliverables", deliverable)
    atomic_write_text(path, "\n".join(lines))
    return path
