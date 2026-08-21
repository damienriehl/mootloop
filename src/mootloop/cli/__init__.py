"""Typer CLI package. Commands are thin adapters over vault/service functions."""

from __future__ import annotations

import json
import os
import pwd
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from mootloop import attest as attest_service
from mootloop import taskspec as taskspec_service
from mootloop.conversion import (
    FolioEnrichConverter,
    convert_corpus_document,
)
from mootloop.discovery_parser import parse_discovery_document, save_requests
from mootloop.errors import (
    AttestationBlockedError,
    FactError,
    IngestError,
    MatterConfigError,
    MootloopError,
    VaultBoundaryError,
)
from mootloop.export import link as export_link
from mootloop.export import service as export_service
from mootloop.facts import FactStore, add_facts_from_file, build_fact_interview
from mootloop.ingest import content_doc_id, ingest_actions, ingest_folder, set_doc_tag
from mootloop.models.common import DocId
from mootloop.models.corpus import DocRole
from mootloop.models.facts import Provenance
from mootloop.models.matter import SCHEMA_VERSION, MatterConfig
from mootloop.models.requests import RequestType
from mootloop.registry import MatterRegistry
from mootloop.runtime import RUNTIME_MODE_ENV, RuntimeMode, validate_runtime_mode
from mootloop.vault import init_vault, load_matter, matter_validation_issues

app = typer.Typer(help="MootLoop — agentic law firm simulator.", no_args_is_help=True)
requests_app = typer.Typer(
    help="Parse served discovery into request work items.", no_args_is_help=True
)
facts_app = typer.Typer(help="Manage the fact repository.", no_args_is_help=True)
corpus_app = typer.Typer(help="Review corpus triage and run visibility.", no_args_is_help=True)
run_app = typer.Typer(
    help="Drive an orchestrator run (stepwise state machine).", no_args_is_help=True
)
cite_app = typer.Typer(help="Extract and verify citations.", no_args_is_help=True)
research_app = typer.Typer(help="Manage the citation research-request queue.", no_args_is_help=True)
judge_app = typer.Typer(help="Build and inspect assigned-judge profiles.", no_args_is_help=True)
decide_app = typer.Typer(help="Review and resolve attorney-gate decisions.", no_args_is_help=True)
web_app = typer.Typer(help="Public demo web tier (synthetic matter only).", no_args_is_help=True)
matters_app = typer.Typer(
    help="Enumerate matter vaults under the matters-root (hosted tier).", no_args_is_help=True
)
driver_app = typer.Typer(
    help="Run the hosted driver worker loop (plan FE-1).", no_args_is_help=True
)
api_app = typer.Typer(
    help="Write-tier matter API tooling (OpenAPI export, plan FE-2).", no_args_is_help=True
)
tasks_app = typer.Typer(
    help="Begin-task on-ramp: resolve free-text intent into TaskSpecs (plan FE-2.5).",
    no_args_is_help=True,
)
export_app = typer.Typer(
    help="Build deliverables and mint signed download links (plan Phase 7 / FE-2.5).",
    no_args_is_help=True,
)
app.add_typer(requests_app, name="requests")
app.add_typer(facts_app, name="facts")
app.add_typer(corpus_app, name="corpus")
app.add_typer(run_app, name="run")
app.add_typer(cite_app, name="cite")
app.add_typer(research_app, name="research")
app.add_typer(judge_app, name="judge")
app.add_typer(decide_app, name="decide")
app.add_typer(web_app, name="web")
app.add_typer(matters_app, name="matters")
app.add_typer(driver_app, name="driver")
app.add_typer(api_app, name="api")
app.add_typer(tasks_app, name="tasks")
app.add_typer(export_app, name="export")


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


class FactReviewActionArg(StrEnum):
    """Human disposition for a proposed fact."""

    accept = "accept"
    reject = "reject"


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
                f"  [{status}] {entry.doc.original_name!r}: {entry.reason}",
                fg=typer.colors.YELLOW,
            )
    actions = report.actions
    if actions:
        typer.echo(f"Action queue: {len(actions)} item(s)")
        for item in actions:
            typer.echo(f"  {item.action_id} [{item.kind}] {item.original_name!r}")


@corpus_app.command("actions")
def corpus_actions(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON")] = False,
) -> None:
    """List deterministic actions blocking document run visibility."""
    actions = ingest_actions(vault_path)
    if json_output:
        typer.echo(json.dumps([item.model_dump(mode="json") for item in actions]))
        return
    if not actions:
        typer.echo("No corpus triage actions.")
        return
    for item in actions:
        typer.echo(f"{item.action_id} [{item.kind}] {item.original_name!r}: {item.reason}")


@corpus_app.command("tag")
def corpus_tag(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    doc_id: Annotated[str, typer.Argument(help="Corpus document id")],
    role: Annotated[DocRole | None, typer.Option("--role", help="Confirmed document role")] = None,
    privileged: Annotated[
        bool | None,
        typer.Option("--privileged/--not-privileged", help="Confirmed privilege call"),
    ] = None,
) -> None:
    """Record human role and/or privilege confirmation for a document."""
    if role is None and privileged is None:
        raise _fail(IngestError("corpus tag requires --role or a privilege flag")) from None
    try:
        updated = set_doc_tag(vault_path, doc_id, role=role, privileged=privileged)
    except (IngestError, VaultBoundaryError) as exc:
        raise _fail(exc) from exc
    typer.echo(json.dumps(updated.model_dump(mode="json")))


@corpus_app.command("convert")
def corpus_convert(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    doc_id: Annotated[str, typer.Argument(help="Corpus document id")],
) -> None:
    """Convert one reviewed document through the configured protected sidecar."""
    try:
        mode = validate_runtime_mode(os.environ.get(RUNTIME_MODE_ENV, RuntimeMode.LOCAL))
        converter = FolioEnrichConverter.from_env(mode)
        _doc, receipt = convert_corpus_document(
            vault_path,
            doc_id,
            converter=converter,
            converted_at=_now(),
            actor=pwd.getpwuid(os.geteuid()).pw_name,
        )
    except (MootloopError, ValueError) as exc:
        raise _fail(exc) from exc
    typer.echo(json.dumps(receipt.model_dump(mode="json")))


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
        flags = []
        if fact.review_status != "accepted":
            flags.append(fact.review_status.upper())
        if not fact.provenance:
            flags.append("UNSUPPORTED")
        flag = f"  [{' / '.join(flags)}]" if flags else ""
        typer.echo(f"{fact.fact_id} (v{fact.version}, conf={fact.confidence}){flag}")
        typer.echo(f"  {fact.statement}")


@facts_app.command("propose")
def facts_propose(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    statement: Annotated[str, typer.Option("--statement", help="Proposed fact statement")],
    confidence: Annotated[float, typer.Option("--confidence", min=0.0, max=1.0)] = 1.0,
    doc_id: Annotated[str | None, typer.Option("--doc-id", help="Supporting corpus doc")] = None,
    quote: Annotated[str | None, typer.Option("--quote", help="Exact supporting quote")] = None,
    location: Annotated[
        str | None, typer.Option("--location", help="Optional location hint")
    ] = None,
    supersedes: Annotated[
        str | None, typer.Option("--supersedes", help="Reviewed predecessor fact id")
    ] = None,
) -> None:
    """Append an unapproved fact candidate for hard-human review."""
    if (doc_id is None) != (quote is None):
        raise _fail(FactError("--doc-id and --quote must be supplied together")) from None
    provenance = (
        [Provenance(doc_id=DocId(doc_id), quote=quote, location_hint=location)]
        if doc_id is not None and quote is not None
        else []
    )
    try:
        store = FactStore(vault_path)
        fact = (
            store.propose_revision(
                supersedes,
                statement,
                provenance=provenance,
                confidence=confidence,
            )
            if supersedes is not None
            else store.propose_fact(
                statement,
                provenance=provenance,
                confidence=confidence,
            )
        )
    except (FactError, VaultBoundaryError) as exc:
        raise _fail(exc) from exc
    typer.echo(fact.model_dump_json())


@facts_app.command("review")
def facts_review(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    fact_id: Annotated[str, typer.Argument(help="Pending fact id")],
    action: Annotated[FactReviewActionArg, typer.Option("--action", help="accept | reject")],
    note: Annotated[str | None, typer.Option("--note", help="Optional review note")] = None,
) -> None:
    """Record a hard-human fact acceptance or rejection."""
    try:
        fact = FactStore(vault_path).review_fact(
            fact_id,
            action=action.value,
            reviewer=pwd.getpwuid(os.geteuid()).pw_name,
            reviewed_at=_now(),
            note=note,
        )
    except (FactError, VaultBoundaryError) as exc:
        raise _fail(exc) from exc
    typer.echo(fact.model_dump_json())


@facts_app.command("interview")
def facts_interview(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON")] = False,
) -> None:
    """Show deterministic fact-review and evidentiary-gap questions."""
    interview = build_fact_interview(vault_path)
    if json_output:
        typer.echo(interview.model_dump_json())
        return
    typer.echo(f"Run-visible facts: {interview.run_visible_fact_count}")
    for question in interview.questions:
        typer.echo(f"{question.question_id} [{question.kind}] {question.prompt}")


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


# --- tasks verbs (begin-task on-ramp; plan FE-2.5) --------------------------


@tasks_app.command("freeform")
def tasks_freeform(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    intent: Annotated[str, typer.Option("--intent", help="Free-text task intent")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit the TaskSpec JSON")] = False,
) -> None:
    """Resolve free-text intent into a TaskSpec (deterministic v1). An unmapped intent is
    still recorded, with ``task=None`` — not runnable until a later lane resolves it."""
    try:
        matter = load_matter(vault_path)
        spec = taskspec_service.create_freeform(vault_path, matter.matter_id, intent, _now())
    except MootloopError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(spec.model_dump_json())
        return
    if spec.runnable:
        typer.secho(
            f"{spec.task_spec_id}  -> {spec.task} (RESOLVED; human lock required)",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            f"{spec.task_spec_id}  -> UNMAPPED (cannot start a run yet)", fg=typer.colors.YELLOW
        )


@tasks_app.command("list")
def tasks_list(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit TaskSpec JSON list")] = False,
) -> None:
    """List the matter's recorded TaskSpecs (all lanes, append order)."""
    try:
        specs = taskspec_service.list_specs(vault_path)
    except MootloopError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(json.dumps([s.model_dump(mode="json") for s in specs]))
        return
    if not specs:
        typer.echo("No task specs recorded.")
        return
    for spec in specs:
        target = spec.task if spec.runnable else "UNMAPPED"
        typer.echo(f"{spec.task_spec_id}  [{spec.source_lane}]  {target}")
        typer.echo(f"  {spec.intent_text}")


@tasks_app.command("lock")
def tasks_lock(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    task_spec_id: Annotated[str, typer.Argument(help="Resolved TaskSpec id")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit the TaskSpecLock JSON")] = False,
) -> None:
    """Human-lock a resolved TaskSpec and its exact adapter/rubric sources."""
    try:
        matter = load_matter(vault_path)
        actor = pwd.getpwuid(os.geteuid()).pw_name
        record = taskspec_service.lock_task_spec(
            vault_path,
            str(matter.matter_id),
            task_spec_id,
            actor,
            _now(),
        )
    except MootloopError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(record.model_dump_json())
        return
    typer.echo(
        f"{record.task_spec_lock_id}  {record.task_spec_id}  "
        f"LOCKED by {record.locked_by} (v{record.lock_version})"
    )


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
) -> None:
    """Attest as the local OS principal; refuses when attorney gates remain open."""
    try:
        actor = pwd.getpwuid(os.geteuid()).pw_name
        record = attest_service.attest(vault_path, run_id, actor, _now())
    except (AttestationBlockedError, MootloopError) as exc:
        raise _fail(exc) from exc
    typer.echo(f"attested {run_id}: master {record.master_sha256[:12]} by {record.reviewer}")


@export_app.command("build")
def export_build(
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


@export_app.command("link")
def export_link_cmd(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Option("--run", help="Run id")],
    doc: Annotated[str, typer.Option("--doc", help="Deliverable name (run-relative)")],
) -> None:
    """Mint a short-expiry signed download link for a deliverable (plan FD-7 / P-37).

    Clean (non-DRAFT) DOCX require the run to be export-ready; DRAFT files are always
    linkable. The signing key is loaded (or derived + stored) from the service-user
    secrets — never hard-coded."""
    from mootloop.secrets import load_or_create_signing_key

    try:
        matter = load_matter(vault_path)
        signer = export_link.LinkSigner(load_or_create_signing_key())
        link = export_link.mint_link(vault_path, matter.matter_id, run_id, doc, _now(), signer)
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(link.url)
    typer.secho(
        f"  doc={link.doc}  draft={link.is_draft}  expires={link.expires_at}",
        fg=typer.colors.YELLOW,
    )


@app.command("attest-status")
def attest_status(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the complete machine-readable integrity state")
    ] = False,
) -> None:
    """Report the read-only attorney commitment and linked export-seal state."""
    status = attest_service.review_integrity_status(vault_path, run_id)
    if as_json:
        typer.echo(status.model_dump_json())
        return
    color = {"valid": typer.colors.GREEN, "invalidated": typer.colors.RED}.get(
        status.attestation_status, typer.colors.YELLOW
    )
    attorney = status.attestation_status.upper()
    if status.attestation_reason:
        attorney += f" — {status.attestation_reason}"
    typer.secho(f"ATTESTATION: {attorney}", fg=color)
    seal = status.export_seal_status.upper()
    if status.export_seal_reason:
        seal += f" — {status.export_seal_reason}"
    typer.secho(f"EXPORT SEAL: {seal}", fg=color)



# Import command modules only after the shared Typer apps and helpers exist.
from . import operations as _operations, review as _review, run as _run  # noqa: E402,F401,I001


if __name__ == "__main__":
    app()
