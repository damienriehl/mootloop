"""Read-only access to fully enumerated, digest-bound public projections."""

from __future__ import annotations

import hashlib
import re
import stat
from pathlib import Path

from pydantic import BaseModel, ValidationError

from mootloop.models.demo import (
    DemoCatalog,
    DemoSnapshot,
    LegacyProjection,
    LocalInputBundle,
    PublicationReview,
)

DEMO_IDS = frozenset(
    [
        "supplier-discovery",
        "employment-retaliation",
        "product-liability",
        "land-use-appeal",
        "civil-rights-argument",
        "musk-openai",
        "dominion-fox",
        "epic-apple",
        "tesla-tornetta",
        "google-oracle",
        "supplier-termination",
        "liability-cap",
        "ai-customer-data",
        "worker-classification",
        "competitor-recruitment",
        "comparative-advertising",
        "open-source-release",
        "distributor-restrictions",
        "customer-data-incident",
        "acquisition-diligence",
    ]
)
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_PUBLIC_FILE = 16 * 1024 * 1024


class PublicationError(ValueError):
    """The requested public artifact is unavailable or fails integrity checks."""


def identity(value: str) -> str:
    if not _ID.fullmatch(value) or value in {".", ".."}:
        raise PublicationError("invalid public artifact identity")
    return value


def read_regular(root: Path, *parts: str) -> bytes:
    path = root.absolute()
    try:
        for parent in (*reversed(path.parents), path):
            if parent.is_symlink():
                raise PublicationError("public path contains a symlink")
        for part in parts:
            if part != "CURRENT":
                identity(part)
            path = path / part
            if path.is_symlink():
                raise PublicationError("public path contains a symlink")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PUBLIC_FILE:
            raise PublicationError("public artifact is not a bounded regular file")
        raw = path.read_bytes()
        if len(raw) > MAX_PUBLIC_FILE:
            raise PublicationError("public artifact exceeds size limit")
        return raw
    except OSError as exc:
        raise PublicationError("public artifact is unavailable") from exc


def decode[MODEL: BaseModel](model: type[MODEL], raw: bytes, digest: str | None = None) -> MODEL:
    if digest is not None and hashlib.sha256(raw).hexdigest() != digest:
        raise PublicationError("public artifact digest mismatch")
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        raise PublicationError("invalid public artifact schema") from exc


def release_catalog(root: Path) -> DemoCatalog:
    catalog = decode(DemoCatalog, read_regular(root, "catalog.json"))
    if {entry.descriptor.demo_id for entry in catalog.entries} != DEMO_IDS:
        raise PublicationError("release must contain exactly the twenty approved demos")
    return catalog


def validate_release(root: Path) -> DemoCatalog:
    catalog = release_catalog(root)
    expected = {"catalog.json"}
    if catalog.legacy_sha256 is not None:
        decode(LegacyProjection, read_regular(root, "legacy.json"), catalog.legacy_sha256)
        expected.add("legacy.json")
    for entry in catalog.entries:
        prefix = (entry.descriptor.demo_id, entry.revision)
        expected.update(
            "/".join((*prefix, name)) for name in ("snapshot.json", "inputs.json", "review.json")
        )
        snapshot = decode(
            DemoSnapshot, read_regular(root, *prefix, "snapshot.json"), entry.snapshot_sha256
        )
        bundle = decode(
            LocalInputBundle, read_regular(root, *prefix, "inputs.json"), entry.bundle_sha256
        )
        review = decode(
            PublicationReview, read_regular(root, *prefix, "review.json"), entry.review_sha256
        )
        if (
            snapshot.descriptor != entry.descriptor
            or snapshot.revision != entry.revision
            or snapshot.bundle_sha256 != entry.bundle_sha256
            or bundle.demo_id != entry.descriptor.demo_id
            or bundle.revision != entry.revision
            or bundle.task != entry.descriptor.task
            or review.demo_id != entry.descriptor.demo_id
            or review.revision != entry.revision
            or review.snapshot_sha256 != entry.snapshot_sha256
            or review.bundle_sha256 != entry.bundle_sha256
        ):
            raise PublicationError("cross-demo identity or review binding mismatch")
    try:
        actual = set()
        allowed_dirs = {str(Path(name).parent) for name in expected}
        allowed_dirs |= {str(Path(name).parent.parent) for name in expected}
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise PublicationError("symlink in public release")
            if path.is_dir():
                if relative not in allowed_dirs:
                    raise PublicationError("unexpected directory in public release")
            else:
                actual.add(relative)
        if actual != expected:
            raise PublicationError("unexpected or missing public release files")
    except OSError as exc:
        raise PublicationError("cannot enumerate public release") from exc
    return catalog


class PublicCatalog:
    def __init__(self, root: Path):
        self.root = root

    def _release(self) -> Path:
        try:
            release = read_regular(self.root, "CURRENT").decode("ascii").strip()
        except UnicodeError as exc:
            raise PublicationError("invalid current release") from exc
        return self.root / "releases" / identity(release)

    def catalog(self) -> DemoCatalog:
        return release_catalog(self._release())

    def _artifact(self, demo_id: str, revision: str, kind: str) -> bytes:
        identity(demo_id)
        identity(revision)
        root = self._release()
        catalog = release_catalog(root)
        entry = next((item for item in catalog.entries if item.descriptor.demo_id == demo_id), None)
        if entry is None or entry.revision != revision:
            raise PublicationError("demo revision is unavailable")
        digest = entry.snapshot_sha256 if kind == "snapshot.json" else entry.bundle_sha256
        raw = read_regular(root, demo_id, revision, kind)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise PublicationError("public artifact digest mismatch")
        return raw

    def snapshot(self, demo_id: str, revision: str) -> DemoSnapshot:
        return decode(DemoSnapshot, self._artifact(demo_id, revision, "snapshot.json"))

    def bundle(self, demo_id: str, revision: str) -> LocalInputBundle:
        return decode(LocalInputBundle, self._artifact(demo_id, revision, "inputs.json"))

    def bundle_bytes(self, demo_id: str, revision: str) -> bytes:
        raw = self._artifact(demo_id, revision, "inputs.json")
        decode(LocalInputBundle, raw)
        return raw

    def legacy(self) -> LegacyProjection:
        root = self._release()
        catalog = release_catalog(root)
        if catalog.legacy_sha256 is None:
            raise PublicationError("original demo projection is unavailable")
        return decode(LegacyProjection, read_regular(root, "legacy.json"), catalog.legacy_sha256)

    def ready(self) -> DemoCatalog:
        return validate_release(self._release())
