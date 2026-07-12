"""Typer CLI. Commands are thin adapters over vault/service functions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
import yaml
from pydantic import ValidationError

from mootloop import attest as attest_service
from mootloop import decisions as decisions_service
from mootloop import gate_ledger, orchestrator, panels
from mootloop.citations import verify
from mootloop.citations.extract import extract_citations
from mootloop.citations.ledger import ResearchQueue
from mootloop.discovery_parser import parse_discovery_document, save_requests
from mootloop.errors import (
    AttestationBlockedError,
    CitationError,
    DecisionError,
    FactError,
    IngestError,
    MatterConfigError,
    MootloopError,
    VaultBoundaryError,
)
from mootloop.export import service as export_service
from mootloop.facts import FactStore, add_facts_from_file
from mootloop.ingest import content_doc_id, ingest_folder
from mootloop.llm import FakeLLMProvider
from mootloop.models.matter import SCHEMA_VERSION, MatterConfig
from mootloop.models.requests import RequestType
from mootloop.models.run import DiscardedTurn
from mootloop.registry import MatterRegistry
from mootloop.vault import init_vault, load_matter, matter_validation_issues

if TYPE_CHECKING:
    from mootloop.engine.worker import ProviderFactory

app = typer.Typer(help="MootLoop — agentic law firm simulator.", no_args_is_help=True)
requests_app = typer.Typer(
    help="Parse served discovery into request work items.", no_args_is_help=True
)
facts_app = typer.Typer(help="Manage the fact repository.", no_args_is_help=True)
run_app = typer.Typer(
    help="Drive an orchestrator run (stepwise state machine).", no_args_is_help=True
)
cite_app = typer.Typer(help="Extract and verify citations.", no_args_is_help=True)
research_app = typer.Typer(
    help="Manage the citation research-request queue.", no_args_is_help=True
)
decide_app = typer.Typer(help="Review and resolve attorney-gate decisions.", no_args_is_help=True)
web_app = typer.Typer(help="Public demo web tier (synthetic matter only).", no_args_is_help=True)
matters_app = typer.Typer(
    help="Enumerate matter vaults under the matters-root (hosted tier).", no_args_is_help=True
)
driver_app = typer.Typer(
    help="Run the hosted driver worker loop (plan FE-1).", no_args_is_help=True
)
app.add_typer(requests_app, name="requests")
app.add_typer(facts_app, name="facts")
app.add_typer(run_app, name="run")
app.add_typer(cite_app, name="cite")
app.add_typer(research_app, name="research")
app.add_typer(decide_app, name="decide")
app.add_typer(web_app, name="web")
app.add_typer(matters_app, name="matters")
app.add_typer(driver_app, name="driver")


class RunModeArg(StrEnum):
    """CLI-facing run mode (plan D12)."""

    autonomous = "autonomous"
    gated = "gated"
    observed = "observed"


class DecisionActionArg(StrEnum):
    """CLI-facing decision resolution action (plan D11)."""

    approve = "approve"
    modify = "modify"
    deny = "deny"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RequestTypeArg(StrEnum):
    """CLI-facing request type (short code) mapped to the domain `RequestType`."""

    rog = "rog"
    rfp = "rfp"
    rfa = "rfa"


_REQUEST_TYPE_BY_ARG = {
    RequestTypeArg.rog: RequestType.INTERROGATORY,
    RequestTypeArg.rfp: RequestType.RFP,
    RequestTypeArg.rfa: RequestType.RFA,
}


def _fail(exc: Exception) -> typer.Exit:
    typer.secho(str(exc), fg=typer.colors.RED, err=True)
    return typer.Exit(1)


# --- service helpers --------------------------------------------------------


def _matter_from_yaml_file(path: Path) -> MatterConfig:
    if not path.is_file():
        raise MatterConfigError(f"--from-yaml file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise MatterConfigError(f"{path} must contain a YAML mapping")
    return MatterConfig.model_validate(raw)


def _matter_from_flags(
    matter_id: str,
    court: str,
    case_number: str,
    our_side: str,
    state: str,
    forum: str,
    county: str,
    judge: str | None,
) -> MatterConfig:
    return MatterConfig.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "matter_id": matter_id,
            "caption": {
                "court_name": court,
                "case_number": case_number,
                "county": county,
                "judge_name": judge,
            },
            "jurisdiction": {"state": state, "forum": forum},
            "parties": [],
            "our_side": our_side,
            "retention": {"retention_class": "standard"},
        }
    )


def _resolve_matter(
    matter_id: str,
    from_yaml: Path | None,
    interactive: bool,
    court: str | None,
    case_number: str | None,
    our_side: str | None,
    state: str | None,
    forum: str | None,
    county: str,
    judge: str | None,
) -> MatterConfig:
    if from_yaml is not None:
        return _matter_from_yaml_file(from_yaml)
    if interactive:
        court = court or typer.prompt("Court name")
        case_number = case_number or typer.prompt("Case number")
        our_side = our_side or typer.prompt("Our side (plaintiff/defendant)")
        state = state or typer.prompt("Jurisdiction state")
        forum = forum or typer.prompt("Forum (state/federal)")
        county = county or typer.prompt("County", default="")
    missing = [
        name
        for name, val in [
            ("--court", court),
            ("--case-number", case_number),
            ("--our-side", our_side),
            ("--jurisdiction-state", state),
            ("--forum", forum),
        ]
        if not val
    ]
    if missing:
        raise MatterConfigError(
            "non-interactive init needs --from-yaml or all of: " + ", ".join(missing)
        )
    return _matter_from_flags(
        matter_id,
        court or "",
        case_number or "",
        our_side or "",
        state or "",
        forum or "",
        county,
        judge,
    )


# --- commands ---------------------------------------------------------------


@app.command()
def init(
    vault_path: Annotated[Path, typer.Argument(help="Path to create the matter vault")],
    matter_id: Annotated[str, typer.Option("--matter-id", help="Matter id")],
    interactive: Annotated[bool, typer.Option("--interactive/--no-interactive")] = True,
    from_yaml: Annotated[Path | None, typer.Option("--from-yaml")] = None,
    court: Annotated[str | None, typer.Option("--court")] = None,
    case_number: Annotated[str | None, typer.Option("--case-number")] = None,
    our_side: Annotated[str | None, typer.Option("--our-side")] = None,
    jurisdiction_state: Annotated[str | None, typer.Option("--jurisdiction-state")] = None,
    forum: Annotated[str | None, typer.Option("--forum")] = None,
    county: Annotated[str, typer.Option("--county")] = "",
    judge: Annotated[str | None, typer.Option("--judge")] = None,
    allow_sync_folder: Annotated[bool, typer.Option("--allow-sync-folder")] = False,
) -> None:
    """Create a matter vault outside the repo."""
    try:
        matter = _resolve_matter(
            matter_id,
            from_yaml,
            interactive,
            court,
            case_number,
            our_side,
            jurisdiction_state,
            forum,
            county,
            judge,
        )
        root = init_vault(vault_path, matter, allow_sync_folder=allow_sync_folder)
    except (MatterConfigError, VaultBoundaryError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    except ValidationError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Created vault at {root}")


@app.command()
def validate(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit structured errors")] = False,
) -> None:
    """Validate a vault's matter.yaml."""
    issues = matter_validation_issues(vault_path)
    if json_output:
        typer.echo(json.dumps({"ok": not issues, "errors": issues}))
    elif not issues:
        typer.echo("OK")
    else:
        for issue in issues:
            typer.secho(f"{issue['loc']}: {issue['msg']}", fg=typer.colors.RED, err=True)
    raise typer.Exit(0 if not issues else 1)


@app.command()
def ingest(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    source_dir: Annotated[Path, typer.Argument(help="Folder of source documents to ingest")],
    tags: Annotated[Path | None, typer.Option("--tags", help="YAML glob -> role/privilege")] = None,
) -> None:
    """Ingest a folder of documents into the vault corpus."""
    now = datetime.now(UTC).isoformat()
    try:
        report = ingest_folder(vault_path, source_dir, now=now, tags_file=tags)
    except (IngestError, VaultBoundaryError) as exc:
        raise _fail(exc) from exc
    counts = report.status_counts()
    typer.echo(f"Ingested {len(report.entries)} document(s): {counts}")
    for status in ("needs_conversion", "unreadable", "too_large"):
        for entry in report.with_status(status):
            typer.secho(
                f"  [{status}] {entry.doc.original_name}: {entry.reason}",
                fg=typer.colors.YELLOW,
            )


@requests_app.command("parse")
def requests_parse(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    file: Annotated[Path, typer.Argument(help="Served discovery document (text)")],
    request_type: Annotated[RequestTypeArg, typer.Option("--type", help="rog | rfp | rfa")],
    set_number: Annotated[int, typer.Option("--set", help="Set number")] = 1,
) -> None:
    """Parse a served discovery document into numbered request work items."""
    if not file.is_file():
        raise _fail(IngestError(f"file not found: {file}")) from None
    data = file.read_bytes()
    text = data.decode("utf-8", errors="replace")
    source_doc = content_doc_id(data)
    report = parse_discovery_document(
        text, _REQUEST_TYPE_BY_ARG[request_type], source_doc, set_number=set_number
    )
    try:
        path = save_requests(vault_path, report.request_set)
    except VaultBoundaryError as exc:
        raise _fail(exc) from exc
    top = [i for i in report.request_set.items if i.subpart is None]
    subs = [i for i in report.request_set.items if i.subpart is not None]
    typer.echo(f"Parsed {len(top)} request(s) + {len(subs)} subpart(s) -> {path}")
    for warning in report.warnings:
        typer.secho(f"  warning: {warning}", fg=typer.colors.YELLOW)


@facts_app.command("add")
def facts_add(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    input_file: Annotated[Path, typer.Option("--input", help="JSON list of facts")],
) -> None:
    """Add facts from a JSON input file to the append-only fact repository."""
    try:
        added = add_facts_from_file(vault_path, input_file)
    except (FactError, VaultBoundaryError) as exc:
        raise _fail(exc) from exc
    typer.echo(f"Added {len(added)} fact(s).")
    for fact in added:
        typer.echo(f"  {fact.fact_id} (v{fact.version}, {len(fact.provenance)} provenance)")


@facts_app.command("list")
def facts_list(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
) -> None:
    """List the current (non-superseded) facts in the repository."""
    current = FactStore(vault_path).get_current()
    if not current:
        typer.echo("No facts recorded.")
        return
    for fact in current:
        flag = "" if fact.provenance else "  [UNSUPPORTED]"
        typer.echo(f"{fact.fact_id} (v{fact.version}, conf={fact.confidence}){flag}")
        typer.echo(f"  {fact.statement}")


# --- run verbs (thin adapters over the orchestrator) ------------------------


@run_app.command("start")
def run_start(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    task: Annotated[str, typer.Option("--task", help="Task adapter name")] = "discovery-responses",
    mode: Annotated[
        RunModeArg | None, typer.Option("--mode", help="autonomous | gated | observed")
    ] = None,
) -> None:
    """Begin a run: write RunStarted, acquire the run lock, print the run id."""
    try:
        run_id = orchestrator.start_run(
            vault_path, task, _now(), mode=mode.value if mode else None
        )
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(run_id)


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
    typer.echo(
        f"Estimate — task={task} tier={estimate.tier} requests={estimate.requests}"
    )
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


def _print_verify_summary(summary: verify.VerifySummary) -> None:
    typer.echo(f"Citations: {len(summary.outcomes)}  {summary.counts()}")
    for outcome in summary.outcomes:
        verified = outcome.status.value == "verified"
        line = f"  [{outcome.status.value}] {outcome.citation.raw_text}"
        if outcome.source_url:
            line += f"  <{outcome.source_url}>"
        typer.secho(line, fg=typer.colors.GREEN if verified else typer.colors.RED)
    if summary.research_request_ids:
        typer.echo("Research requests opened: " + ", ".join(summary.research_request_ids))
    typer.secho(summary.disclosure, fg=typer.colors.YELLOW)


@cite_app.command("verify")
def cite_verify(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str | None, typer.Option("--run", help="Verify a run's drafts")] = None,
    text_file: Annotated[Path | None, typer.Option("--text", help="Verify a text file")] = None,
) -> None:
    """Extract citations (from a run's drafts or a text file) and verify them."""
    if (run_id is None) == (text_file is None):
        raise _fail(MootloopError("cite verify needs exactly one of --run or --text")) from None
    try:
        if run_id is not None:
            summary = orchestrator.verify_run_citations(vault_path, run_id, _now())
        else:
            assert text_file is not None
            if not text_file.is_file():
                raise MootloopError(f"--text file not found: {text_file}")
            citations = extract_citations(text_file.read_text(encoding="utf-8"))
            summary = verify.verify_all(vault_path, citations, _now())
    except (MootloopError, VaultBoundaryError) as exc:
        raise _fail(exc) from exc
    _print_verify_summary(summary)


@research_app.command("list")
def research_list(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
) -> None:
    """List open citation research requests (citations the free stack cannot verify)."""
    try:
        open_requests = ResearchQueue(vault_path).open_requests()
    except VaultBoundaryError as exc:
        raise _fail(exc) from exc
    if not open_requests:
        typer.echo("No open research requests.")
        return
    for request in open_requests:
        typer.echo(f"{request.request_id}  {request.normalized}  ({request.reason})")
    typer.secho(verify.CITATOR_DISCLOSURE, fg=typer.colors.YELLOW)


@research_app.command("fulfill")
def research_fulfill(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    request_id: Annotated[str, typer.Argument(help="Research request id")],
    file: Annotated[Path, typer.Option("--file", help="Authority markdown to curate")],
    url: Annotated[str | None, typer.Option("--url", help="Source URL for the authority")] = None,
) -> None:
    """Fulfill a research request: curate the authority and mark its citation verified."""
    try:
        record = verify.fulfill_research_request(
            vault_path, request_id, file=file, now=_now(), url=url
        )
    except (CitationError, VaultBoundaryError) as exc:
        raise _fail(exc) from exc
    typer.echo(f"fulfilled {request_id}: {record.citation_id} verified (curated)")


# --- decide verbs (attorney-gate primitives, plan D11) ----------------------


@decide_app.command("list")
def decide_list(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit decision JSON")] = False,
) -> None:
    """List the run's open attorney-gate decisions."""
    try:
        matter = load_matter(vault_path)
        open_decisions = decisions_service.DecisionStore(vault_path, run_id).list_open()
    except MootloopError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(json.dumps([d.model_dump(mode="json") for d in open_decisions]))
        return
    if not open_decisions:
        typer.echo("No open decisions.")
        return
    for decision in open_decisions:
        mode = decisions_service.gate_mode_for(matter, decision.kind)
        typer.echo(f"{decision.decision_id}  [{mode}]  {decision.kind.value}")
        typer.echo(f"  {decision.proposal.summary}")
        typer.echo(f"  recommended: {decision.proposal.recommended}")


@decide_app.command("show")
def decide_show(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    decision_id: Annotated[str, typer.Argument(help="Decision id")],
) -> None:
    """Show a single decision's full proposal (and resolution, if any)."""
    decision = decisions_service.DecisionStore(vault_path, run_id).get(decision_id)
    if decision is None:
        raise _fail(DecisionError(f"unknown decision {decision_id!r}")) from None
    typer.echo(decision.model_dump_json(indent=2))


def _resolve_one(
    vault_path: Path,
    run_id: str,
    decision_id: str,
    action: str,
    chosen: str | None,
    note: str,
    by: str,
    source: str,
) -> None:
    decisions_service.resolve(
        vault_path,
        run_id,
        decision_id,
        action,  # type: ignore[arg-type]
        chosen,
        note,
        by,
        source,  # type: ignore[arg-type]
        _now(),
    )


@decide_app.command("resolve")
def decide_resolve(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    decision_id: Annotated[
        str | None, typer.Argument(help="Decision id (omit with --input)")
    ] = None,
    action: Annotated[
        DecisionActionArg | None, typer.Option("--action", help="approve | modify | deny")
    ] = None,
    choose: Annotated[str | None, typer.Option("--choose", help="Chosen option key")] = None,
    note: Annotated[str, typer.Option("--note", help="Resolution note")] = "",
    by: Annotated[str | None, typer.Option("--by", help="Deciding attorney's name")] = None,
    input_file: Annotated[
        Path | None, typer.Option("--input", help="JSON list of resolutions (batch)")
    ] = None,
) -> None:
    """Resolve one decision, or a batch via ``--input`` (source is human unless the
    batch entry marks it ``policy``)."""
    try:
        if input_file is not None:
            if not input_file.is_file():
                raise MootloopError(f"--input file not found: {input_file}")
            entries = json.loads(input_file.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                raise MootloopError("--input must be a JSON list of resolutions")
            for entry in entries:
                _resolve_one(
                    vault_path,
                    run_id,
                    entry["decision_id"],
                    entry.get("action", "approve"),
                    entry.get("choose"),
                    entry.get("note", ""),
                    entry.get("by", by or "batch"),
                    entry.get("source", "human"),
                )
            typer.echo(f"resolved {len(entries)} decision(s)")
            return
        if decision_id is None or action is None or by is None:
            raise MootloopError("single resolve needs <decision-id>, --action, and --by")
        _resolve_one(vault_path, run_id, decision_id, action.value, choose, note, by, "human")
        typer.echo(f"resolved {decision_id}: {action.value}")
    except (MootloopError, KeyError) as exc:
        raise _fail(exc if isinstance(exc, MootloopError) else DecisionError(str(exc))) from exc


# --- matters verbs (hosted-tier registry; read-only listing) ----------------


@matters_app.command("list")
def matters_list(
    json_output: Annotated[bool, typer.Option("--json", help="Emit the registry JSON")] = False,
) -> None:
    """List matters under the matters-root (``MOOTLOOP_MATTERS_ROOT``)."""
    try:
        summaries = MatterRegistry().list_matters()
    except MootloopError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(json.dumps([s.model_dump(mode="json") for s in summaries]))
        return
    if not summaries:
        typer.echo("No matters found.")
        return
    for summary in summaries:
        typer.echo(f"{summary.matter_id}  {summary.display_name}  ({summary.case_number})")


# --- web verbs (demo tier; the bake is the tier's only writer) ---------------


@web_app.command("bake")
def web_bake(
    dest: Annotated[Path, typer.Argument(help="Destination for the baked demo vault")],
) -> None:
    """Bake the synthetic demo vault (full pipeline, FakeLLMProvider, deterministic)."""
    from mootloop.web.bake import build_demo_vault

    try:
        vault = build_demo_vault(dest)
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(f"Baked demo vault at {vault}")


# --- attest verb (its own primitive; export reads it, never sets it) --------


@app.command()
def attest(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    by: Annotated[str, typer.Option("--by", help="Reviewing attorney's name")],
) -> None:
    """Record an attestation over the run's md-master (plan D9). Refuses on open gates."""
    try:
        record = attest_service.attest(vault_path, run_id, by, _now())
    except (AttestationBlockedError, MootloopError) as exc:
        raise _fail(exc) from exc
    typer.echo(f"attested {run_id}: master {record.master_sha256[:12]} by {record.reviewer}")


@app.command()
def export(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    force_draft: Annotated[
        bool, typer.Option("--force-draft", help="Force the DRAFT watermark regardless of state")
    ] = False,
) -> None:
    """Build every deliverable and render per-set DOCX (draft until attested + green).

    The markdown deliverables are always produced; DOCX is clean only when the run is
    attested and the gate ledger is export-ready with a clean residue scan (plan D3
    M12). Prints what was produced and any blockers.
    """
    try:
        result = export_service.export_run(vault_path, run_id, _now(), force_draft=force_draft)
    except MootloopError as exc:
        raise _fail(exc) from exc

    typer.echo(f"Deliverables for {run_id} (draft={result.is_draft}):")
    typer.echo(f"  master:        {result.master}")
    if result.verification is not None:
        typer.echo(f"  verification:  {result.verification}")
    typer.echo(f"  privilege log: {result.privilege_log}")
    typer.echo(f"  strategy memo: {result.memo}")
    typer.echo(f"  audit log:     {result.audit_log}")
    for path in result.set_masters:
        typer.echo(f"  set master:    {path}")
    if result.docx_skipped_reason is not None:
        typer.secho(
            f"  DOCX skipped: {result.docx_skipped_reason} (markdown still written)",
            fg=typer.colors.YELLOW,
        )
    for path in result.docx:
        typer.secho(f"  DOCX:          {path}", fg=typer.colors.GREEN)
    for label, scan in result.residue_results:
        if scan.status != "pass":
            reasons = "; ".join(f.code for f in scan.findings)
            typer.secho(f"  residue FAIL [{label}]: {reasons}", fg=typer.colors.RED)

    clean = result.export_ready and not result.is_draft
    color = typer.colors.GREEN if clean else typer.colors.YELLOW
    typer.secho(
        f"export_ready: {result.export_ready}  ·  attestation: {result.attestation_state}",
        fg=color,
    )
    if result.blockers:
        typer.echo("blockers: " + ", ".join(result.blockers))


@app.command("attest-status")
def attest_status(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
) -> None:
    """Report attestation state (Valid | Invalidated | Missing), logging invalidation."""
    check = attest_service.check_attestation(vault_path, run_id, _now())
    color = {"valid": typer.colors.GREEN, "invalidated": typer.colors.RED}.get(
        check.status, typer.colors.YELLOW
    )
    line = check.status.upper()
    if check.reason:
        line += f" — {check.reason}"
    typer.secho(line, fg=color)


# --- driver verbs (hosted worker loop, plan FE-1) ---------------------------


def _provider_factory(fake: bool) -> ProviderFactory:
    from mootloop.llm import LLMProvider

    if fake:
        def _fake(vault_root: Path, run_dir: Path, billing_mode: str) -> LLMProvider:
            return FakeLLMProvider()

        return _fake

    def _headless(vault_root: Path, run_dir: Path, billing_mode: str) -> LLMProvider:
        from mootloop.engine.claude_provider import HeadlessClaudeProvider

        return HeadlessClaudeProvider(
            vault_root=vault_root, run_dir=run_dir, billing_mode=billing_mode
        )

    return _headless


@driver_app.command("run-once")
def driver_run_once(
    matters_root: Annotated[Path, typer.Option("--matters-root", help="Matters-root dir")],
    worker_id: Annotated[str, typer.Option("--worker-id", help="This worker's id")],
    fake: Annotated[
        bool, typer.Option("--fake", help="Use the FakeLLMProvider (smoke test)")
    ] = False,
) -> None:
    """Run one driver tick: claim + drain one run (or report idle)."""
    from mootloop.engine.queue import Queue
    from mootloop.engine.worker import Worker

    worker = Worker(matters_root, worker_id, Queue(matters_root), _provider_factory(fake))
    try:
        did_work = worker.run_once(datetime.now(UTC))
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo("did work" if did_work else "idle")


@driver_app.command("serve")
def driver_serve(
    matters_root: Annotated[Path, typer.Option("--matters-root", help="Matters-root dir")],
    worker_id: Annotated[str, typer.Option("--worker-id", help="This worker's id")],
    interval: Annotated[float, typer.Option("--interval", help="Idle poll seconds")] = 1.0,
) -> None:
    """Run the supervised driver loop until SIGTERM (drains the current turn first)."""
    import time

    from mootloop.engine.queue import Queue
    from mootloop.engine.worker import Worker

    worker = Worker(matters_root, worker_id, Queue(matters_root), _provider_factory(False))
    worker.serve(
        now_fn=lambda: datetime.now(UTC),
        sleep_fn=time.sleep,
        stop=lambda: False,
        interval=interval,
    )


@app.command()
def backup(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    dest: Annotated[Path, typer.Option("--dest", help="Destination dir for the snapshot")],
) -> None:
    """Write a consistent tar.gz snapshot of the matter vault (plan FD-6)."""
    from mootloop.engine.backup import backup_matter

    try:
        out = backup_matter(vault_path, dest, _now())
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(f"backup written: {out}")


if __name__ == "__main__":
    app()
