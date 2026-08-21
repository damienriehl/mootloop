"""Shared local and hosted run-launch transaction."""

from __future__ import annotations

from pathlib import Path

from mootloop import orchestrator
from mootloop.context_sources import (
    ContextContributionStore,
    configured_firm_preferences_path,
)
from mootloop.engine.outbox import drain_run_outbox
from mootloop.engine.queue import Queue
from mootloop.errors import MatterNotFoundError, VaultBoundaryError
from mootloop.judge_profiles import (
    JudgeProfileStore,
    profile_context_contribution,
    profile_matches_matter,
)
from mootloop.learn.service import FirmLearningStore, configured_firm_profile_root
from mootloop.models.common import MatterId
from mootloop.models.events import QueueIntent, RunMode
from mootloop.models.matter import MatterConfig
from mootloop.registry import MatterRegistry
from mootloop.vault import load_matter


def _commit_launch(
    vault_root: Path | str,
    task: str,
    launched_at: str,
    *,
    run_id: str,
    mode: RunMode | None = None,
    task_spec_id: str | None = None,
    idempotent: bool = True,
    queue: Queue | None = None,
    matter_id: MatterId | None = None,
) -> str:
    if queue is not None and matter_id is None:
        raise VaultBoundaryError("hosted launch requires a verified matter identity")
    intent = (
        QueueIntent.create(
            item_id=f"run:{matter_id}:{run_id}",
            lane="run",
            kind="run_turn",
            enqueued_at=launched_at,
        )
        if queue is not None
        else None
    )
    matter = load_matter(vault_root)
    contributions = list(ContextContributionStore(vault_root).list_all())
    firm_root = configured_firm_profile_root()
    if firm_root is not None:
        contributions.extend(FirmLearningStore(firm_root).list_all())
    judge_profile = JudgeProfileStore(vault_root).latest_or_none()
    if (
        judge_profile is not None
        and judge_profile.calibration.calibrated
        and profile_matches_matter(judge_profile, matter)
    ):
        contributions.append(
            profile_context_contribution(judge_profile, MatterId(matter.matter_id))
        )
    started_id = orchestrator.start_run(
        vault_root,
        task,
        launched_at,
        run_id=run_id,
        mode=mode,
        task_spec_id=task_spec_id,
        idempotent=idempotent,
        firm_preferences_path=configured_firm_preferences_path(),
        context_contributions=tuple(contributions),
        queue_intent=intent,
    )
    if queue is not None:
        drain_run_outbox(vault_root, queue, started_id)
    return started_id


def launch_run(
    vault_root: Path | str,
    task: str,
    launched_at: str,
    *,
    run_id: str,
    mode: RunMode | None = None,
    task_spec_id: str | None = None,
    idempotent: bool = True,
    queue: Queue | None = None,
    expected_matter_id: MatterId | None = None,
) -> str:
    """Commit one launch and deliver its hosted outbox when a queue is supplied."""
    matter_id: MatterId | None = None
    if queue is not None:
        if expected_matter_id is None:
            raise VaultBoundaryError("hosted launch requires a trusted matter identity")
        actual_matter_id = MatterId(load_matter(vault_root).matter_id)
        if actual_matter_id != expected_matter_id:
            raise VaultBoundaryError(
                f"vault matter {actual_matter_id!r} does not match trusted matter "
                f"{expected_matter_id!r}"
            )
        matter_id = expected_matter_id
    return _commit_launch(
        vault_root,
        task,
        launched_at,
        run_id=run_id,
        mode=mode,
        task_spec_id=task_spec_id,
        idempotent=idempotent,
        queue=queue,
        matter_id=matter_id,
    )


def launch_run_from_path(
    vault_root: Path | str,
    task: str,
    launched_at: str,
    *,
    run_id: str,
    mode: RunMode | None = None,
    task_spec_id: str | None = None,
    idempotent: bool = True,
    registry: MatterRegistry | None = None,
) -> str:
    """Classify a CLI vault as local or hosted, failing closed on registry poison."""
    matter, queue = classify_vault_for_queue(vault_root, registry=registry)
    return _commit_launch(
        vault_root,
        task,
        launched_at,
        run_id=run_id,
        mode=mode,
        task_spec_id=task_spec_id,
        idempotent=idempotent,
        queue=queue,
        matter_id=MatterId(matter.matter_id) if queue is not None else None,
    )


def classify_vault_for_queue(
    vault_root: Path | str,
    *,
    registry: MatterRegistry | None = None,
) -> tuple[MatterConfig, Queue | None]:
    """Classify one physical vault as hosted or local without trusting matter.yaml alone."""
    registry = registry or MatterRegistry()
    matter = load_matter(vault_root)
    vault_real = Path(vault_root).resolve()
    registry_root = registry.root.resolve()
    if vault_real.is_relative_to(registry_root):
        if vault_real.parent != registry_root or vault_real.name != matter.matter_id:
            raise VaultBoundaryError(
                f"hosted vault path {vault_real} does not match matter identity "
                f"{matter.matter_id!r}"
            )
        registered = registry.resolve(str(matter.matter_id))
        queue: Queue | None = Queue(registry.root)
    else:
        try:
            registered = registry.resolve(str(matter.matter_id))
        except MatterNotFoundError:
            queue = None
        else:
            raise VaultBoundaryError(
                f"vault {vault_real} does not match registered matter "
                f"path {registered.resolve()}"
            )
    if queue is not None and registered.resolve() != vault_real:
        raise VaultBoundaryError(
            f"vault {vault_real} does not match registered matter path {registered.resolve()}"
        )
    return matter, queue
