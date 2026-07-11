"""Typer CLI. Commands are thin adapters over vault/service functions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from mootloop.discovery_parser import parse_discovery_document, save_requests
from mootloop.errors import FactError, IngestError, MatterConfigError, VaultBoundaryError
from mootloop.facts import FactStore, add_facts_from_file
from mootloop.ingest import content_doc_id, ingest_folder
from mootloop.models.matter import SCHEMA_VERSION, MatterConfig
from mootloop.models.requests import RequestType
from mootloop.vault import init_vault, matter_validation_issues

app = typer.Typer(help="MootLoop — agentic law firm simulator.", no_args_is_help=True)
requests_app = typer.Typer(
    help="Parse served discovery into request work items.", no_args_is_help=True
)
facts_app = typer.Typer(help="Manage the fact repository.", no_args_is_help=True)
app.add_typer(requests_app, name="requests")
app.add_typer(facts_app, name="facts")


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


if __name__ == "__main__":
    app()
