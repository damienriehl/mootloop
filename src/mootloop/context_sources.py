"""Trusted launch-time sources for firm policy and approved matter context."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from pydantic import ValidationError

from mootloop.errors import OrchestratorError
from mootloop.models.context import ContextContribution, StoredContextContribution
from mootloop.vault import atomic_write_once_text, safe_vault_path

FIRM_PREFERENCES_ENV = "MOOTLOOP_FIRM_PREFERENCES"
CONTRIBUTIONS_SUBPATH: tuple[str, ...] = ("context", "contributions")


def configured_firm_preferences_path() -> Path | None:
    """Return the operator-injected external firm file, when configured."""
    value = os.environ.get(FIRM_PREFERENCES_ENV)
    return Path(value) if value else None


class ContextContributionStore:
    """Write-once approved-source records loaded by normal launch boundaries."""

    def __init__(self, vault_root: Path | str) -> None:
        self.vault_root = vault_root
        safe_vault_path(vault_root, *CONTRIBUTIONS_SUBPATH)

    def list_all(self) -> tuple[ContextContribution, ...]:
        root = safe_vault_path(self.vault_root, *CONTRIBUTIONS_SUBPATH)
        if not root.is_dir():
            return ()
        records: list[ContextContribution] = []
        directory_fd = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            names = sorted(name for name in os.listdir(directory_fd) if name.endswith(".json"))
            for name in names:
                path = safe_vault_path(
                    self.vault_root,
                    *CONTRIBUTIONS_SUBPATH,
                    name,
                )
                try:
                    file_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=directory_fd,
                    )
                    try:
                        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                            raise OSError("source is not a regular file")
                        with os.fdopen(file_fd, "r", encoding="utf-8") as handle:
                            file_fd = -1
                            stored = StoredContextContribution.model_validate_json(handle.read())
                    finally:
                        if file_fd >= 0:
                            os.close(file_fd)
                except (OSError, UnicodeError, ValidationError) as exc:
                    raise OrchestratorError(
                        f"context contribution source {path.name!r} is invalid: {exc}"
                    ) from exc
                record = stored.contribution
                expected_name = f"{record.contribution_id}.json"
                if path.name != expected_name:
                    raise OrchestratorError(
                        f"context contribution source {path.name!r} does not match record "
                        f"identity {record.contribution_id!r}"
                    )
                records.append(record)
        finally:
            os.close(directory_fd)
        return tuple(records)

    def put(self, record: ContextContribution) -> Path:
        """Publish one immutable candidate record for a trusted governance producer."""
        path = safe_vault_path(
            self.vault_root,
            *CONTRIBUTIONS_SUBPATH,
            f"{record.contribution_id}.json",
        )
        body = StoredContextContribution(contribution=record).model_dump_json(indent=2) + "\n"
        if path.is_file():
            try:
                existing = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise OrchestratorError(
                    f"context contribution source {path.name!r} could not be read: {exc}"
                ) from exc
            if existing == body:
                return path
            raise OrchestratorError(
                f"context contribution {record.contribution_id!r} already exists with "
                "different content"
            )
        atomic_write_once_text(path, body)
        return path
