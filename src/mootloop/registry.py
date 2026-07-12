"""Matter registry: the matters-root convention and the ``matter_id -> vault``
resolver for the hosted tier.

All matter vaults live under a single matters-root directory (``MOOTLOOP_MATTERS_ROOT``,
default ``/srv/mootloop-matters``), one subdirectory per matter, each holding a
``matter.yaml``. The registry is the hosted tier's single entry point for turning an
untrusted ``matter_id`` from an HTTP route into a vault path: it validates the id
charset (`vault.validate_id`) and asserts realpath-containment of the resolved vault
under realpath(matters-root) before returning — the same fail-closed choke-point
discipline as `safe_vault_path`. Core stays synchronous.
"""

from __future__ import annotations

import os
from pathlib import Path

from mootloop.errors import MatterConfigError, MatterNotFoundError, VaultBoundaryError
from mootloop.models.common import MatterId
from mootloop.models.matter import MatterConfig
from mootloop.models.matters import MatterSummary
from mootloop.vault import (
    MATTER_YAML,
    _is_within,
    _real,
    create_vault,
    load_matter,
    validate_id,
)

DEFAULT_MATTERS_ROOT = "/srv/mootloop-matters"
MATTERS_ROOT_ENV = "MOOTLOOP_MATTERS_ROOT"


class MatterRegistry:
    """Resolve and enumerate matter vaults under a single matters-root.

    The root is taken from the ``root`` constructor arg if given (tests inject a
    ``tmp_path``), else from ``MOOTLOOP_MATTERS_ROOT``, else the packaged default.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        raw = root if root is not None else os.environ.get(MATTERS_ROOT_ENV, DEFAULT_MATTERS_ROOT)
        self.root = Path(raw)

    # -- resolution --
    def resolve(self, matter_id: str) -> Path:
        """Return the vault path for ``matter_id``, fail-closed.

        Validates the id charset (rejecting ``.``, ``..``, and separators), asserts the
        resolved vault stays inside realpath(root), and requires the directory to exist.
        Raises `VaultBoundaryError` on a charset/containment breach and
        `MatterNotFoundError` when no such matter directory is present.
        """
        validate_id(matter_id, kind="matter_id")
        root_real = _real(self.root)
        vault_real = _real(root_real / matter_id)
        if not _is_within(vault_real, root_real) or vault_real == root_real:
            raise VaultBoundaryError(
                f"matter {matter_id!r} resolves to {vault_real}, outside matters-root {root_real}"
            )
        if not vault_real.is_dir():
            raise MatterNotFoundError(f"no matter {matter_id!r} under {root_real}")
        return vault_real

    # -- enumeration --
    def list_matters(self) -> list[MatterSummary]:
        """Scan the root for subdirectories holding a valid ``matter.yaml``.

        Directories without a ``matter.yaml`` and those whose config fails to validate
        are skipped (a partially-provisioned or unrelated dir is not fatal). A directory
        that fails realpath-containment is a hard `VaultBoundaryError` — a security
        signal is never silently swallowed. Results are sorted by matter id.
        """
        root_real = _real(self.root)
        if not root_real.is_dir():
            return []
        summaries: list[MatterSummary] = []
        for child in sorted(root_real.iterdir()):
            if not child.is_dir():
                continue
            child_real = _real(child)
            if not _is_within(child_real, root_real) or child_real == root_real:
                raise VaultBoundaryError(
                    f"matter dir {child_real} escapes matters-root {root_real}"
                )
            if not (child_real / MATTER_YAML).is_file():
                continue
            try:
                matter = load_matter(child_real)
            except MatterConfigError:
                continue
            summaries.append(self._summarize(matter, child_real.name))
        return summaries

    @staticmethod
    def _summarize(matter: MatterConfig, rel_path: str) -> MatterSummary:
        return MatterSummary(
            matter_id=MatterId(matter.matter_id),
            display_name=matter.caption.court_name,
            case_number=matter.caption.case_number,
            rel_path=rel_path,
            loaded=True,
        )

    # -- creation --
    def create(self, matter: MatterConfig) -> Path:
        """Create a vault for ``matter`` under the matters-root via `create_vault`.

        The id is validated and containment-checked through `resolve`'s charset rules;
        `create_vault` refuses a non-empty target and applies its own `safe_vault_path`
        hardening for every write.
        """
        validate_id(matter.matter_id, kind="matter_id")
        root_real = _real(self.root)
        root_real.mkdir(parents=True, exist_ok=True)
        vault_real = _real(root_real / matter.matter_id)
        if not _is_within(vault_real, root_real) or vault_real == root_real:
            raise VaultBoundaryError(
                f"matter {matter.matter_id!r} resolves to {vault_real}, outside {root_real}"
            )
        return create_vault(vault_real, matter)
