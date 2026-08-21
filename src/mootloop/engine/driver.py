"""Driver construction and host-side matter-worker provisioning services."""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mootloop import secrets
from mootloop.engine.claude_provider import HeadlessClaudeProvider
from mootloop.engine.isolation import PROXY_PASSWORD_ENV, hosted_wrapper
from mootloop.engine.queue import Queue
from mootloop.engine.worker import ProviderFactory, Worker
from mootloop.errors import DriverError, VaultBoundaryError
from mootloop.llm import FakeLLMProvider, LLMProvider
from mootloop.models.common import MatterId
from mootloop.registry import MatterRegistry
from mootloop.runtime import RuntimeMode
from mootloop.vault import load_matter, preflight_vault_location, validate_id

_UNBOUND_MATTER = "unbound"
ComposeRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DriverBinding:
    matter_id: MatterId | None
    vault: Path | None


def _prepare_engine_config_source(root: Path, matter_id: MatterId, matters_root: Path) -> Path:
    """Create or validate one private, durable Claude-state directory per matter."""
    if not root.is_absolute():
        raise DriverError("engine config root must be an absolute path")
    try:
        root_mode = root.lstat().st_mode
    except OSError as exc:
        raise DriverError("engine config root is missing or unreadable") from exc
    if not stat.S_ISDIR(root_mode) or stat.S_ISLNK(root_mode):
        raise DriverError("engine config root must be a real directory")
    root_real = root.resolve()
    matters_real = matters_root.resolve()
    if (
        root_real == matters_real
        or root_real in matters_real.parents
        or matters_real in root_real.parents
    ):
        raise DriverError("engine config root must be outside the matters root")

    source = root_real / str(matter_id)
    try:
        source.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise DriverError("matter engine config directory could not be created") from exc
    try:
        source_mode = source.lstat().st_mode
    except OSError as exc:
        raise DriverError("matter engine config directory is unreadable") from exc
    if not stat.S_ISDIR(source_mode) or stat.S_ISLNK(source_mode):
        raise DriverError("matter engine config path must be a real directory")
    if source_mode & 0o077:
        raise DriverError("matter engine config directory must not be group/world accessible")
    source_real = source.resolve()
    if source_real.parent != root_real:
        raise DriverError("matter engine config directory escapes its root")
    return source_real


def resolve_driver_binding(
    matters_root: Path,
    mode: RuntimeMode,
    matter_id: str | None,
    *,
    matter_vault: Path | None = None,
) -> DriverBinding:
    """Resolve and preflight an optional worker matter boundary."""
    if mode is RuntimeMode.HOSTED and matter_id in {None, _UNBOUND_MATTER}:
        raise VaultBoundaryError("hosted mode requires a matter id")
    if matter_id is None:
        if matter_vault is not None:
            raise VaultBoundaryError("a bound vault requires a matter id")
        return DriverBinding(None, None)
    bound_id = MatterId(validate_id(matter_id, kind="matter_id"))
    vault = matter_vault or MatterRegistry(root=matters_root).resolve(str(bound_id))
    matter = load_matter(vault)
    if matter.matter_id != bound_id:
        raise VaultBoundaryError("mounted matter identity does not match the worker binding")
    preflight_vault_location(vault)
    return DriverBinding(bound_id, Path(vault).resolve())


def provider_factory(fake: bool, mode: RuntimeMode) -> ProviderFactory:
    if fake:

        def _fake(vault_root: Path, run_dir: Path, billing_mode: str) -> LLMProvider:
            return FakeLLMProvider()

        return _fake

    def _headless(vault_root: Path, run_dir: Path, billing_mode: str) -> LLMProvider:
        return HeadlessClaudeProvider(
            vault_root=vault_root,
            run_dir=run_dir,
            billing_mode=billing_mode,
            runtime_mode=mode,
            egress_wrapper=hosted_wrapper() if mode is RuntimeMode.HOSTED else [],
        )

    return _headless


def build_driver_worker(
    matters_root: Path,
    worker_id: str,
    *,
    fake: bool,
    mode: RuntimeMode,
    matter_id: str | None,
    matter_vault: Path | None = None,
) -> Worker:
    binding = resolve_driver_binding(
        matters_root,
        mode,
        matter_id,
        matter_vault=matter_vault,
    )
    return Worker(
        matters_root,
        worker_id,
        Queue(matters_root),
        provider_factory(fake, mode),
        bound_matter_id=binding.matter_id,
        bound_vault=binding.vault,
    )


def start_matter_worker(
    matters_root: Path,
    matter_id: str,
    *,
    compose_file: Path,
    proxy_password_file: Path,
    engine_config_root: Path,
    runner: ComposeRunner = subprocess.run,
) -> None:
    """Validate host inputs, then start one fixed-target Compose worker project."""
    binding = resolve_driver_binding(matters_root, RuntimeMode.HOSTED, matter_id)
    if binding.vault is None or binding.matter_id is None:  # pragma: no cover
        raise DriverError("hosted matter binding did not resolve a vault")
    try:
        password_mode = proxy_password_file.lstat().st_mode
    except OSError as exc:
        raise DriverError("egress proxy password file is missing or unreadable") from exc
    if not stat.S_ISREG(password_mode):
        raise DriverError("egress proxy password path must be a regular file")
    if password_mode & 0o077:
        raise DriverError("egress proxy password file must not be group/world accessible")
    try:
        proxy_password = proxy_password_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise DriverError("egress proxy password file is missing or unreadable") from exc
    if not proxy_password:
        raise DriverError("egress proxy password file is empty")
    configured_password = secrets.load_secret(PROXY_PASSWORD_ENV)
    if not configured_password or configured_password != proxy_password:
        raise DriverError("egress proxy password file does not match the driver secret")
    secrets.register_secret(proxy_password)
    if not compose_file.is_file():
        raise DriverError("matter-worker compose file is missing")
    engine_config_source = _prepare_engine_config_source(
        engine_config_root,
        binding.matter_id,
        matters_root,
    )
    environment = os.environ.copy()
    environment.update(
        {
            "MOOTLOOP_MATTER_ID": str(binding.matter_id),
            "MOOTLOOP_MATTER_SOURCE": str(binding.vault),
            "MOOTLOOP_ENGINE_CONFIG_SOURCE": str(engine_config_source),
            "MOOTLOOP_EGRESS_PROXY_PASSWORD_FILE": str(proxy_password_file.resolve()),
        }
    )
    project_id = str(binding.matter_id).replace(".", "-")
    command = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "-p",
        f"mootloop-worker-{project_id}",
        "--profile",
        "matter-worker",
        "up",
        "-d",
        "driver",
        "egress-proxy",
    ]
    try:
        runner(command, check=True, env=environment, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DriverError("matter-worker Compose startup failed") from exc
