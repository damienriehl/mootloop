"""Offline validation and bounded extraction of a pinned public release archive."""

from __future__ import annotations

import hashlib
import io
import tarfile
import tempfile
from pathlib import Path

from mootloop.models.demo import DemoCatalog, DemoReleasePin, DemoSnapshot, LocalInputBundle
from mootloop.web.catalog import PublicationError, decode, identity, read_regular, validate_release
from mootloop.web.publish import publish_release

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_FILES = 62


def validate_collection(root: Path) -> DemoCatalog:
    catalog = validate_release(root)
    if catalog.legacy_sha256 is None:
        raise PublicationError("release requires the original demo projection")
    counts = dict.fromkeys(("synthetic", "public-record", "business"), 0)
    for entry in catalog.entries:
        counts[entry.descriptor.collection] += 1
        base = (entry.descriptor.demo_id, entry.revision)
        snapshot = decode(
            DemoSnapshot, read_regular(root, *base, "snapshot.json"), entry.snapshot_sha256
        )
        bundle = decode(
            LocalInputBundle, read_regular(root, *base, "inputs.json"), entry.bundle_sha256
        )
        names = {item.name for item in bundle.files}
        for strategy in snapshot.strategies:
            if f"replay-{strategy.strategy_id}.json" not in names:
                raise PublicationError("missing local strategy replay")
            if not all(stage.turn_ids for stage in strategy.stages):
                raise PublicationError("work-product stage has no recorded provenance")
    if counts != {"synthetic": 5, "public-record": 5, "business": 10}:
        raise PublicationError(
            "collection must contain five synthetic, five real and ten business demos"
        )
    # The publisher owns descriptor, input, credential and exact-byte validation.
    with tempfile.TemporaryDirectory(prefix="mootloop-release-check-") as temporary:
        publish_release(root, Path(temporary) / "public")
    return catalog


def stage_archive(archive: Path, pin_path: Path, destination: Path) -> DemoCatalog:
    pin = decode(DemoReleasePin, pin_path.read_bytes())
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise PublicationError("release archive is not a bounded regular file")
    raw = archive.read_bytes()
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise PublicationError("release archive exceeds size limit")
    raw_digest = hashlib.sha256(raw).hexdigest()
    if raw_digest != pin.sha256:
        raise PublicationError("release archive digest mismatch")
    payloads: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
            for member in tar:
                parts = member.name.split("/")
                if not member.isfile() or len(parts) not in {1, 3}:
                    raise PublicationError("invalid archive entry")
                for part in parts:
                    identity(part)
                if member.name in payloads or len(payloads) >= MAX_FILES:
                    raise PublicationError("duplicate or excessive archive entries")
                total += member.size
                if member.size < 0 or total > MAX_ARCHIVE_BYTES:
                    raise PublicationError("archive expanded size exceeds limit")
                stream = tar.extractfile(member)
                if stream is None:
                    raise PublicationError("unreadable archive member")
                payloads[member.name] = stream.read()
    except (tarfile.TarError, OSError) as exc:
        raise PublicationError("invalid release archive") from exc
    with tempfile.TemporaryDirectory(prefix="mootloop-release-stage-") as temporary:
        root = Path(temporary)
        for name, raw in payloads.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        catalog = validate_collection(root)
        if catalog.release_id != pin.release_id:
            raise PublicationError("release identity does not match pin")
        publish_release(root, destination)
        return catalog
