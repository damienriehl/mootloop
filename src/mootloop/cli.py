"""Typer CLI. Commands are thin adapters over vault/service functions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from mootloop.errors import MatterConfigError, VaultBoundaryError
from mootloop.models.matter import SCHEMA_VERSION, MatterConfig
from mootloop.vault import init_vault, matter_validation_issues

app = typer.Typer(help="MootLoop — agentic law firm simulator.", no_args_is_help=True)


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


if __name__ == "__main__":
    app()
