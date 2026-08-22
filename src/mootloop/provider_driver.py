"""Synchronous provider driver and its lifecycle output helpers.

The orchestrator owns the state-machine primitives.  This module composes those
primitives for the headless/fake-provider execution path without making that
optional driver part of the already-large state-machine module.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from mootloop import decisions
from mootloop.context import (
    RunContext,
    context_manifest_path,
    load_run_context,
    load_run_corpus,
)
from mootloop.context_assembly import assemble_context
from mootloop.errors import LockHeldError
from mootloop.journal import load_state
from mootloop.models.evidence import RunStatusSidecar
from mootloop.models.run import DraftOutput
from mootloop.persistence import sha256_file
from mootloop.stages import first_incomplete_stage, render_prompt
from mootloop.vault import RunLock, atomic_write_text, safe_vault_path

if TYPE_CHECKING:
    from mootloop.llm import LLMProvider, RawTurnResult
    from mootloop.models.events import RunState
    from mootloop.models.run import TurnSpec

logger = logging.getLogger("mootloop.provider_driver")


def _run_turn_with_lock_renewal(
    lock: RunLock,
    provider: LLMProvider,
    spec: TurnSpec,
    prompt: str,
    *,
    extra_heartbeat: Callable[[], bool] | None = None,
) -> RawTurnResult:
    """Keep ``lock`` live across a blocking provider call and fence its result."""
    stop = threading.Event()
    lost = threading.Event()
    interval = max(0.01, min(lock.heartbeat_threshold.total_seconds() / 3.0, 30.0))

    if not lock.heartbeat(best_effort=True) or (
        extra_heartbeat is not None and not extra_heartbeat()
    ):
        raise LockHeldError("run lock ownership was lost before provider call")

    def renew() -> None:
        while not stop.wait(interval):
            if not lock.heartbeat(best_effort=True) or (
                extra_heartbeat is not None and not extra_heartbeat()
            ):
                lost.set()
                return

    keeper = threading.Thread(
        target=renew,
        name=f"run-lock-{lock.run_id}",
        daemon=True,
    )
    keeper.start()
    result: RawTurnResult | None = None
    error: BaseException | None = None
    try:
        result = provider.run_turn(spec, prompt)
    except BaseException as exc:
        error = exc
    finally:
        stop.set()
        keeper.join()

    still_owned = (
        not lost.is_set()
        and lock.heartbeat(best_effort=True)
        and (extra_heartbeat is None or extra_heartbeat())
    )
    if not still_owned:
        logger.warning("discarding provider result after run lock ownership was lost")
        raise LockHeldError("run lock ownership was lost during provider call")
    if error is not None:
        raise error
    assert result is not None
    return result


def run_with_provider(
    vault_root: Path | str,
    run_id: str,
    provider: LLMProvider,
    now: str,
    *,
    max_attempts: int | None,
    max_concurrency: int = 1,
) -> RunState:
    """Drive a run with the retry ceiling committed at launch."""
    # Import lazily: orchestrator exposes the stable public wrapper for this driver.
    from mootloop import orchestrator

    del max_concurrency  # Reserved for the v1-compatible public API.
    run_context = load_run_context(vault_root, run_id)
    binding = run_context.binding
    tier_models = run_context.manifest.tier_models
    max_attempts = orchestrator._launch_max_attempts(run_context, max_attempts)
    with RunLock(vault_root, run_id) as lock:
        while True:
            lock.heartbeat(best_effort=True)
            state = load_state(vault_root, run_id)
            if state.finished:
                break
            units = run_context.units
            if orchestrator._over_cap(state, run_context):
                orchestrator._cap_transition(vault_root, run_id, run_context)
                break
            facts = run_context.facts
            specs = orchestrator._plan(
                run_id,
                state,
                binding,
                units,
                facts,
                max_attempts,
                tier_models,
                assemble_context(
                    run_context.manifest,
                    load_run_corpus(vault_root, run_context),
                ),
            )
            if not specs:
                orchestrator._finalize(vault_root, run_id, now, run_context)
                write_observed_status(vault_root, run_id, run_context)
                break
            for spec in specs:
                fresh = load_state(vault_root, run_id)
                if fresh.finished or spec.turn_id in fresh.completed_turns:
                    continue
                result = _run_turn_with_lock_renewal(
                    lock,
                    provider,
                    spec,
                    render_prompt(spec, run_context.manifest.persona_bodies[spec.persona]),
                )
                # The provider is an untrusted blocking boundary. Rebind from disk
                # immediately before the first protected write so a provider-side or
                # concurrent context mutation cannot ride a stale in-memory manifest.
                run_context = load_run_context(vault_root, run_id)
                binding = run_context.binding
                units = run_context.units
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
                    run_context,
                )
    return load_state(vault_root, run_id)


def write_observed_status(
    vault_root: Path | str,
    run_id: str,
    run_context: RunContext | None = None,
) -> None:
    """Overwrite the derived observed-mode status view."""
    state = load_state(vault_root, run_id)
    if state.mode != "observed":
        return
    run_context = run_context or load_run_context(vault_root, run_id)
    rendered = _render_status_md(vault_root, run_id, state, run_context)
    atomic_write_text(
        safe_vault_path(vault_root, "runs", run_id, "STATUS.md"),
        rendered,
    )
    sidecar = RunStatusSidecar(
        source_matter_id=run_context.manifest.matter_id,
        run_id=run_context.manifest.run_id,
        task=state.task or run_context.manifest.task,
        mode=state.mode,
        status=state.status,
        current_stage=state.current_stage,
        total_spend_usd=state.total_spend_usd,
        completed_turns=len(state.completed_turns),
        discarded_turns=len(state.discarded),
        context_manifest_sha256=sha256_file(context_manifest_path(vault_root, run_id)),
        human_view_sha256=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
    )
    atomic_write_text(
        safe_vault_path(vault_root, "runs", run_id, "STATUS.json"),
        sidecar.model_dump_json(indent=2) + "\n",
    )


def _render_status_md(
    vault_root: Path | str,
    run_id: str,
    state: RunState,
    run_context: RunContext,
) -> str:
    from mootloop import orchestrator

    matter = run_context.manifest.matter_config
    binding = run_context.binding
    units = run_context.units
    facts = run_context.facts
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
            run_context.manifest.resolved_config.max_attempts,
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
    state: RunState,
    run_context: RunContext,
) -> Path:
    """Write the markdown deliverable with one fenced anchor per request."""
    from mootloop import orchestrator

    binding = run_context.binding
    units = run_context.units
    facts = run_context.facts
    lines = [
        f"# Discovery Responses — {binding.config.task}",
        "",
        f"Run: `{run_id}` · Requests: {len(units)} · Rubric: {binding.config.rubric_id}",
        "",
    ]
    for index, request in enumerate(units):
        context = orchestrator._context_for(
            run_id,
            state,
            binding,
            units,
            facts,
            index,
            run_context.manifest.resolved_config.max_attempts,
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
