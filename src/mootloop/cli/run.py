"""Run-lifecycle CLI adapters over the shared orchestrator services."""

from __future__ import annotations

import json
import os
import pwd
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from mootloop import gate_ledger, orchestrator, panels
from mootloop.errors import MootloopError
from mootloop.journal import load_state
from mootloop.llm import FakeLLMProvider
from mootloop.models.run import DiscardedTurn
from mootloop.registry import MatterRegistry
from mootloop.vault import load_matter

from . import RunModeArg, _fail, _now, run_app


@run_app.command("start")
def run_start(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    task: Annotated[str, typer.Option("--task", help="Task adapter name")] = "discovery-responses",
    mode: Annotated[
        RunModeArg | None, typer.Option("--mode", help="autonomous | gated | observed")
    ] = None,
    task_spec_id: Annotated[
        str | None, typer.Option("--task-spec-id", help="Approved TaskSpec id")
    ] = None,
    run_id: Annotated[
        str | None, typer.Option("--run-id", help="Stable run id (recommended for retries)")
    ] = None,
) -> None:
    """Begin a run: write RunStarted, acquire the run lock, print the run id."""
    launched_at = _now()
    resolved_id = run_id or f"{task}-{''.join(ch for ch in launched_at if ch.isdigit())}"
    try:
        from mootloop.engine.launch import launch_run_from_path

        started_id = launch_run_from_path(
            vault_path,
            task,
            launched_at,
            run_id=resolved_id,
            mode=mode.value if mode else None,
            task_spec_id=task_spec_id,
            idempotent=run_id is not None,
        )
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(started_id)


@run_app.command("continue")
def run_continue(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
) -> None:
    """Clear a gated-mode checkpoint so the run resumes (plan Phase 5)."""
    try:
        orchestrator.continue_run(vault_path, run_id)
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(f"cleared checkpoint for {run_id} — resume with `run drive --fake`")


@run_app.command("pause")
def run_pause(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    reason: Annotated[str, typer.Option("--reason", help="Why the run is pausing")] = "manual",
) -> None:
    """Pause a live run so the driver stops ticking it (plan FE-1)."""
    try:
        orchestrator.pause_run(vault_path, run_id, reason=reason)
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(f"paused {run_id} ({reason})")


@run_app.command("resume")
def run_resume(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
) -> None:
    """Resume a paused run so the driver picks it up again (plan FE-1)."""
    try:
        orchestrator.resume_run(vault_path, run_id)
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(f"resumed {run_id}")


@run_app.command("reopen")
def run_reopen(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    reason: Annotated[
        str, typer.Option("--reason", help="Why the run may resume (logged to the journal)")
    ],
    grant_attempts: Annotated[
        int,
        typer.Option(
            "--grant-attempts",
            help="Extra retry attempts for counter-capped turns (clears that blocker)",
        ),
    ] = 0,
) -> None:
    """Reopen a `needs_attention` run once what blocked it is fixed (auth, a persona
    body, a config change). Refuses while a counter-capped turn is unresolved unless
    `--grant-attempts` restores its retry budget."""
    matter = load_matter(vault_path)
    registry = MatterRegistry()
    try:
        hosted_vault = registry.resolve(matter.matter_id)
    except MootloopError:
        hosted_vault = None
    hosted = hosted_vault is not None and hosted_vault.resolve() == vault_path.resolve()
    try:
        if not (hosted and orchestrator.reopen_enqueue_pending(vault_path, run_id)):
            state = orchestrator.reopen_run(
                vault_path,
                run_id,
                reason=reason,
                grant_attempts=grant_attempts,
                reopened_by=pwd.getpwuid(os.geteuid()).pw_name,
            )
        else:
            state = load_state(vault_path, run_id)
    except MootloopError as exc:
        raise _fail(exc) from exc
    granted = f" (+{grant_attempts} attempt(s))" if grant_attempts else ""
    queued = False
    if hosted:
        from mootloop.engine.queue import Queue, WorkItem

        Queue(registry.root).ensure_enqueued(
            WorkItem.create(
                lane="run",
                matter_id=matter.matter_id,
                run_id=run_id,
                kind="run_turn",
                now=datetime.now(UTC),
                item_id=f"run:{matter.matter_id}:{run_id}",
            )
        )
        queued = True
    next_step = (
        "queued for the hosted driver"
        if queued
        else "standalone vault: resume explicitly with `run drive --fake`"
    )
    typer.echo(f"reopened {run_id}: {state.status}{granted} — {next_step}")


@run_app.command("blockers")
def run_blockers(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit the blocker JSON")] = False,
) -> None:
    """List what a `needs_attention` run must clear before `run reopen` will restart it."""
    try:
        blockers = orchestrator.attention_blockers(vault_path, run_id)
    except MootloopError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(json.dumps([b.model_dump(mode="json") for b in blockers]))
        return
    if not blockers:
        typer.echo("No attention blockers.")
        return
    for blocker in blockers:
        typer.secho(f"{blocker.kind}  {blocker.ref}  {blocker.detail}", fg=typer.colors.YELLOW)


@run_app.command("gates")
def run_gates(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit the gate-ledger JSON")] = False,
) -> None:
    """Regenerate and show the gate ledger — the single source of truth for export."""
    try:
        gate_ledger.write_ledger(vault_path, run_id)
        doc = gate_ledger.build_ledger(vault_path, run_id)
    except MootloopError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(json.dumps(doc.to_dict()))
        return
    typer.secho(
        f"export_ready: {doc.export_ready}",
        fg=typer.colors.GREEN if doc.export_ready else typer.colors.RED,
    )
    if doc.blockers:
        typer.echo("blockers: " + ", ".join(doc.blockers))


@run_app.command("panels")
def run_panels(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit the panel report JSON")] = False,
) -> None:
    """Show the judge panel's objection-survival distribution (plan Phase 6)."""
    try:
        report = panels.build_panel_report(vault_path, run_id)
    except MootloopError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(report.model_dump_json())
        return
    if not report.results:
        typer.echo("No panel results yet (judge panel not complete).")
        return
    for result in report.results:
        color = typer.colors.GREEN if result.survival_rate >= 0.5 else typer.colors.RED
        typer.secho(
            f"{result.request_id}  obj[{result.objection_index}] {result.objection_basis}: "
            f"{result.survive_votes}/{result.total_votes} survive "
            f"({result.survival_rate:.0%})",
            fg=color,
        )


@run_app.command("plan-next")
def run_plan_next(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit TurnSpec JSON")] = False,
) -> None:
    """List the TurnSpecs that can execute now."""
    try:
        specs = orchestrator.plan_next(vault_path, run_id)
    except MootloopError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(json.dumps([s.model_dump(mode="json") for s in specs]))
    else:
        for spec in specs:
            typer.echo(f"{spec.turn_id}  {spec.persona.value}  {spec.stage}  {spec.request_id}")


@run_app.command("prompt")
def run_prompt(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    turn_id: Annotated[str, typer.Argument(help="Turn id from plan-next")],
) -> None:
    """Print the assembled prompt for a schedulable turn."""
    try:
        typer.echo(orchestrator.assemble_prompt(vault_path, run_id, turn_id))
    except MootloopError as exc:
        raise _fail(exc) from exc


@run_app.command("record-turn")
def run_record_turn(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    turn_id: Annotated[str, typer.Argument(help="Turn id")],
    input_file: Annotated[Path, typer.Option("--input", help="File with the raw turn JSON")],
) -> None:
    """Validate + gate + journal a subagent's raw output for one turn."""
    if not input_file.is_file():
        raise _fail(MootloopError(f"--input file not found: {input_file}")) from None
    raw_text = input_file.read_text(encoding="utf-8")
    try:
        result = orchestrator.record_turn(vault_path, run_id, turn_id, raw_text, None, _now())
    except MootloopError as exc:
        raise _fail(exc) from exc
    if isinstance(result, DiscardedTurn):
        typer.secho(
            f"discarded {turn_id} (attempt {result.attempt}): {result.reason}",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.echo(f"recorded {turn_id}")


@run_app.command("status")
def run_status(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit status JSON")] = False,
) -> None:
    """Print a status snapshot (folded from the journal)."""
    summary = orchestrator.status_summary(vault_path, run_id)
    if json_output:
        typer.echo(json.dumps(summary))
    else:
        for key, value in summary.items():
            typer.echo(f"{key}: {value}")


@run_app.command("drive")
def run_drive(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    fake: Annotated[bool, typer.Option("--fake", help="Drive with the FakeLLMProvider")] = False,
) -> None:
    """Drive a run to completion. v1 only supports the fake provider (--fake)."""
    if not fake:
        raise _fail(
            MootloopError("run drive currently requires --fake (no live provider in v1)")
        ) from None
    try:
        state = orchestrator.run_with_provider(vault_path, run_id, FakeLLMProvider(), _now())
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(f"{run_id}: {state.status} ({len(state.completed_turns)} turns)")


@run_app.command("estimate")
def run_estimate(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    task: Annotated[str, typer.Option("--task", help="Task adapter name")] = "discovery-responses",
    tier: Annotated[str | None, typer.Option("--tier", help="Budget tier override")] = None,
) -> None:
    """Pre-run cost range + per-stage breakdown (plan D5)."""
    try:
        resolved_tier = tier or orchestrator.matter_tier(vault_path)
        estimate = orchestrator.estimate_run_cost(
            vault_path, task, resolved_tier, datetime.now(UTC).date()
        )
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(f"Estimate — task={task} tier={estimate.tier} requests={estimate.requests}")
    typer.echo(
        f"  range: ${estimate.min_usd:.2f} (converge early) – "
        f"${estimate.max_usd:.2f} (all caps)  [notional, plan mode]"
    )
    typer.echo(f"  {'stage':<26} {'model':<20} {'calls':>12} {'usd':>18}")
    for row in estimate.breakdown:
        calls = f"{row.min_calls}–{row.max_calls}"
        usd = f"${row.min_usd:.2f}–${row.max_usd:.2f}"
        typer.echo(f"  {row.stage:<26} {row.model:<20} {calls:>12} {usd:>18}")


@run_app.command("raise-cap")
def run_raise_cap(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    to: Annotated[float, typer.Option("--to", help="New hard cap in USD")],
) -> None:
    """Raise a capped run's hard budget cap and reopen it for resumption (plan D5)."""
    try:
        orchestrator.raise_cap(vault_path, run_id, to)
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(f"raised cap for {run_id} to ${to:.2f} — resume with `run drive --fake`")
