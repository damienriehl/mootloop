"""Hosted driver, API-schema, backup, restore, and close CLI adapters."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from mootloop.conversion import FOLIO_ENRICH_COMMIT
from mootloop.engine import driver as driver_service
from mootloop.errors import MootloopError
from mootloop.runtime import RuntimeMode

from . import _fail, _now, api_app, app, driver_app


@driver_app.command("run-once")
def driver_run_once(
    matters_root: Annotated[Path, typer.Option("--matters-root", help="Matters-root dir")],
    worker_id: Annotated[str, typer.Option("--worker-id", help="This worker's id")],
    fake: Annotated[
        bool, typer.Option("--fake", help="Use the FakeLLMProvider (smoke test)")
    ] = False,
    mode: Annotated[RuntimeMode, typer.Option("--mode", help="Execution trust mode")] = (
        RuntimeMode.LOCAL
    ),
    matter_id: Annotated[
        str | None, typer.Option("--matter-id", help="Only claim this mounted matter")
    ] = None,
    matter_vault: Annotated[
        Path | None,
        typer.Option("--matter-vault", help="Fixed mounted vault path for hosted mode"),
    ] = None,
) -> None:
    """Run one driver tick: claim + drain one run (or report idle)."""
    try:
        worker = driver_service.build_driver_worker(
            matters_root,
            worker_id,
            fake=fake,
            mode=mode,
            matter_id=matter_id,
            matter_vault=matter_vault,
        )
        did_work = worker.run_once(datetime.now(UTC))
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo("did work" if did_work else "idle")


@driver_app.command("serve")
def driver_serve(
    matters_root: Annotated[Path, typer.Option("--matters-root", help="Matters-root dir")],
    worker_id: Annotated[str, typer.Option("--worker-id", help="This worker's id")],
    interval: Annotated[float, typer.Option("--interval", help="Idle poll seconds")] = 1.0,
    mode: Annotated[RuntimeMode, typer.Option("--mode", help="Execution trust mode")] = (
        RuntimeMode.LOCAL
    ),
    matter_id: Annotated[
        str | None, typer.Option("--matter-id", help="Only claim this mounted matter")
    ] = None,
    matter_vault: Annotated[
        Path | None,
        typer.Option("--matter-vault", help="Fixed mounted vault path for hosted mode"),
    ] = None,
) -> None:
    """Run the supervised driver loop until SIGTERM (drains the current turn first)."""
    try:
        worker = driver_service.build_driver_worker(
            matters_root,
            worker_id,
            fake=False,
            mode=mode,
            matter_id=matter_id,
            matter_vault=matter_vault,
        )
        worker.serve(
            now_fn=lambda: datetime.now(UTC),
            sleep_fn=time.sleep,
            stop=lambda: False,
            interval=interval,
        )
    except MootloopError as exc:
        raise _fail(exc) from exc


@driver_app.command("start-matter-worker")
def driver_start_matter_worker(
    matter_id: Annotated[str, typer.Argument(help="Validated hosted matter id")],
    matters_root: Annotated[Path, typer.Option("--matters-root", help="Host matters root")],
    proxy_password_file: Annotated[
        Path,
        typer.Option("--proxy-password-file", help="Dedicated proxy-password file"),
    ],
    engine_config_root: Annotated[
        Path,
        typer.Option("--engine-config-root", help="Private durable Claude-state root"),
    ] = Path("/srv/mootloop-engine-config"),
    folio_enrich_image: Annotated[
        str,
        typer.Option(
            "--folio-enrich-image",
            help="Reviewed folio-enrich OCI image pinned by sha256 digest",
        ),
    ] = "",
    folio_enrich_commit: Annotated[
        str,
        typer.Option("--folio-enrich-commit", help="Reviewed folio-enrich source commit"),
    ] = FOLIO_ENRICH_COMMIT,
    compose_file: Annotated[
        Path,
        typer.Option("--compose-file", help="Matter-worker Compose file"),
    ] = Path("docker-compose.matter.yaml"),
) -> None:
    """Validate host paths, then start one isolated matter-worker project."""
    try:
        driver_service.start_matter_worker(
            matters_root,
            matter_id,
            compose_file=compose_file,
            proxy_password_file=proxy_password_file,
            engine_config_root=engine_config_root,
            folio_enrich_image=folio_enrich_image,
            folio_enrich_commit=folio_enrich_commit,
        )
    except MootloopError as exc:
        raise _fail(exc) from exc


@api_app.command("export-openapi")
def api_export_openapi(
    path: Annotated[Path, typer.Argument(help="Destination path for the OpenAPI JSON")],
) -> None:
    """Write the write-tier matter API's OpenAPI schema as JSON (feeds the typed TS
    client codegen, plan FD-8)."""
    from mootloop.web.api import create_matter_api

    schema = create_matter_api().openapi()
    path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    typer.echo(f"wrote OpenAPI schema to {path}")


@app.command()
def backup(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    dest: Annotated[Path, typer.Option("--dest", help="Destination dir for the snapshot")],
) -> None:
    """Write a consistent, encrypted tar.gz.enc snapshot of the matter vault (plan FD-6)."""
    from mootloop.engine.backup import backup_matter

    try:
        out = backup_matter(vault_path, dest, _now())
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(f"backup written: {out}")


@app.command()
def restore(
    archive: Annotated[Path, typer.Argument(help="Path to the .tar.gz.enc backup archive")],
    matters_root: Annotated[
        Path, typer.Option("--matters-root", help="Matters-root to restore the vault into")
    ],
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Overwrite an existing non-empty vault")
    ] = False,
) -> None:
    """Decrypt and safely restore a matter vault from a backup archive (plan FD-6)."""
    from mootloop.engine.backup import restore_matter

    try:
        out = restore_matter(archive, matters_root, now=_now(), overwrite=overwrite)
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(f"restored vault: {out}")


@app.command()
def close(
    matter_id: Annotated[str, typer.Argument(help="Matter id under the matters-root")],
    by: Annotated[str, typer.Option("--by", help="Who is closing the matter (audit actor)")],
    backup_dir: Annotated[
        Path | None, typer.Option("--backup-dir", help="Destination for the pre-close backup")
    ] = None,
    matters_root: Annotated[
        Path | None,
        typer.Option("--matters-root", help="Matters-root (defaults to MOOTLOOP_MATTERS_ROOT)"),
    ] = None,
    skip_backup: Annotated[
        bool, typer.Option("--skip-backup", help="Skip the pre-close backup (unsafe)")
    ] = False,
    acknowledge_skip_backup: Annotated[
        bool,
        typer.Option(
            "--yes-delete-without-backup",
            help="Acknowledge that skipping the backup makes the data unrecoverable",
        ),
    ] = False,
) -> None:
    """Purge a closed matter's vault, retaining an anonymized tombstone (plan FD-6)."""
    from mootloop.close import close_matter
    from mootloop.registry import DEFAULT_MATTERS_ROOT, MATTERS_ROOT_ENV

    root = matters_root or Path(os.environ.get(MATTERS_ROOT_ENV, DEFAULT_MATTERS_ROOT))
    try:
        record = close_matter(
            root,
            matter_id,
            actor=by,
            now=_now(),
            backup_dir=backup_dir,
            skip_backup=skip_backup,
            acknowledge_skip_backup=acknowledge_skip_backup,
        )
    except MootloopError as exc:
        raise _fail(exc) from exc
    removed = sum(record.removed_counts.values())
    typer.echo(f"closed {matter_id}: {removed} file(s) purged; tombstone retained")
