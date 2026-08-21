"""Private firm-profile merge semantics for reviewed shared learnings."""

from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from pydantic import ValidationError

from mootloop.errors import LearningImportError
from mootloop.learn.diff import sha256_text
from mootloop.models.common import PublicText
from mootloop.models.context import ContextContribution
from mootloop.models.learnings import (
    FirmLearningEvent,
    LearningProposalView,
    LearningReview,
    LearningTier,
)
from mootloop.resources import REPO_ROOT
from mootloop.vault import atomic_write_once_text, atomic_write_text

FIRM_PROFILE_ROOT_ENV = "MOOTLOOP_FIRM_PROFILE_ROOT"


def configured_firm_profile_root() -> Path | None:
    value = os.environ.get(FIRM_PROFILE_ROOT_ENV)
    return Path(value) if value else None


class FirmLearningStore:
    """ID-keyed immutable reviewed events outside every matter vault and the repo."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        try:
            if self.root.exists() and self.root.is_symlink():
                raise LearningImportError("firm profile root may not be a symlink")
            real = self.root.resolve(strict=False)
            repo = REPO_ROOT.resolve()
            if real == repo or repo.is_relative_to(real) or real.is_relative_to(repo):
                raise LearningImportError("firm profile root must not overlap the OSS repo")
        except OSError as exc:
            raise LearningImportError("firm profile root could not be resolved") from exc

    def _directory(self, public: bool) -> Path:
        directory = self.root / ("public-candidates" if public else "learnings")
        try:
            if self.root.exists() and self.root.is_symlink():
                raise LearningImportError("firm profile root may not be a symlink")
            root = self.root.resolve(strict=False)
            resolved = directory.resolve(strict=False)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise LearningImportError("firm learning path escapes the firm profile") from exc
        return resolved

    @contextmanager
    def _locked(self) -> Iterator[None]:
        root = self.root.resolve(strict=False)
        root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise LearningImportError("firm profile root may not be a symlink")
        fd = os.open(root / ".learning-merge.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _events(self, *, public: bool) -> list[FirmLearningEvent]:
        directory = self._directory(public)
        if not directory.is_dir():
            return []
        records: list[FirmLearningEvent] = []
        for path in sorted(directory.glob("*.json")):
            try:
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise OSError("entry is not a regular file")
                records.append(
                    FirmLearningEvent.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except (OSError, UnicodeError, ValidationError) as exc:
                raise LearningImportError(f"firm learning {path.name!r} is invalid") from exc
        return records

    def list_all(self) -> list[ContextContribution]:
        return [event.contribution for event in self._events(public=False)]

    def list_public_candidates(self) -> list[ContextContribution]:
        return [event.contribution for event in self._events(public=True)]

    def _refresh_preferences(self) -> None:
        events = self._events(public=False)
        by_task: dict[str, list[str]] = {}
        entries: list[dict[str, object]] = []
        for event in events:
            contribution = event.contribution
            for task in contribution.task_scope:
                by_task.setdefault(task, []).append(contribution.contribution_id)
            entries.append(
                {
                    "contribution_id": contribution.contribution_id,
                    "source_matter_id": str(contribution.source_matter_id),
                    "tasks": list(contribution.task_scope),
                    "excluded_matter_ids": [
                        str(matter_id) for matter_id in contribution.excluded_matter_ids
                    ],
                    "text": contribution.text,
                }
            )
        review_groups = [
            {"task": task, "contribution_ids": sorted(ids)}
            for task, ids in sorted(by_task.items())
            if len(ids) > 1
        ]
        body = yaml.safe_dump(
            {
                "schema_version": "1.0",
                "preferences": entries,
                "potential_conflict_review": review_groups,
            },
            sort_keys=False,
        )
        atomic_write_text(self.root.resolve(strict=False) / "learning-preferences.yaml", body)

    def put(
        self,
        contribution: ContextContribution,
        review: LearningReview,
        *,
        public: bool,
    ) -> None:
        with self._locked():
            self._put_locked(contribution, review, public=public)
            if not public:
                self._refresh_preferences()

    def _put_locked(
        self,
        contribution: ContextContribution,
        review: LearningReview,
        *,
        public: bool,
    ) -> None:
        directory = self._directory(public)
        path = directory / f"{contribution.contribution_id}.json"
        try:
            path.resolve(strict=False).relative_to(directory)
            if path.is_symlink():
                raise LearningImportError("firm learning event may not be a symlink")
            if path.exists() and not path.is_file():
                raise LearningImportError("firm learning event must be a regular file")
        except (OSError, ValueError) as exc:
            raise LearningImportError("firm learning event path escapes the profile") from exc
        body = FirmLearningEvent(
            source_matter_id=review.source_matter_id,
            review=review.model_copy(update={"reviewed_text": contribution.text}),
            contribution=contribution,
        ).model_dump_json(indent=2) + "\n"
        if path.is_file():
            try:
                if path.read_text(encoding="utf-8") == body:
                    return
            except (OSError, UnicodeError) as exc:
                raise LearningImportError("firm learning could not be read") from exc
            raise LearningImportError("firm learning id conflicts with stored content")
        try:
            atomic_write_once_text(path, body)
        except FileExistsError:
            try:
                if path.read_text(encoding="utf-8") == body:
                    return
            except (OSError, UnicodeError) as exc:
                raise LearningImportError("firm learning could not be read") from exc
            raise LearningImportError("firm learning id conflicts with stored content") from None


def shared_contribution(
    proposal: LearningProposalView,
    review: LearningReview,
    *,
    tier: LearningTier,
    scrubbed_text: PublicText,
) -> ContextContribution:
    contribution_id = f"{tier}-learning-{proposal.proposal_id}"
    return ContextContribution(
        contribution_id=contribution_id,
        kind="learning",
        text=scrubbed_text,
        sha256=sha256_text(scrubbed_text),
        provenance_locator=f"firm-profile/{tier}/{contribution_id}.json#{review.review_id}",
        source_matter_id=proposal.source_matter_id,
        task_scope=(proposal.task,),
        permission="matter_confidential",
        approval_state="accepted" if tier == "firm" else "pending",
        sharing_scope="firm" if tier == "firm" else "public_area",
        excluded_matter_ids=review.excluded_matter_ids,
    )
