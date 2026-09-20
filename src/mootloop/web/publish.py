"""Offline publisher; never imported by the public reader."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml

from mootloop.demo_inputs import validate_bundle
from mootloop.models.demo import DemoCatalog, DemoDescriptor, LocalInputBundle
from mootloop.vault import enclosing_git_repo
from mootloop.web.catalog import PublicationError, PublicCatalog, read_regular, validate_release

_CREDENTIAL = re.compile(
    rb"(?:MOOTLOOP-CANARY-|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def _expected_descriptors() -> dict[str, DemoDescriptor]:
    path = Path(__file__).resolve().parents[3] / "config/demos/catalog.yaml"
    raw = yaml.safe_load(path.read_text())
    descriptors = [DemoDescriptor.model_validate(item) for item in raw["entries"]]
    return {item.demo_id: item for item in descriptors}


def publish_release(source: Path, target: Path, *, manifest: DemoCatalog | None = None) -> str:
    """Validate before promotion; an existing release identity is immutable."""
    if enclosing_git_repo(source) is not None or enclosing_git_repo(target) is not None:
        raise PublicationError("public releases must remain outside Git checkouts")
    catalog = validate_release(source)
    expected = (
        _expected_descriptors()
        if manifest is None
        else {entry.descriptor.demo_id: entry.descriptor for entry in manifest.entries}
    )
    for entry in catalog.entries:
        actual = entry.descriptor.model_dump(exclude={"introduction"})
        wanted = expected[entry.descriptor.demo_id].model_dump(exclude={"introduction"})
        if actual != wanted:
            raise PublicationError("descriptor does not match approved collection manifest")
    payloads = {"catalog.json": read_regular(source, "catalog.json")}
    for entry in catalog.entries:
        for filename in ("snapshot.json", "inputs.json", "review.json"):
            parts = (entry.descriptor.demo_id, entry.revision, filename)
            payloads["/".join(parts)] = read_regular(source, *parts)
    for name, raw in payloads.items():
        if name.endswith("/inputs.json"):
            validate_bundle(LocalInputBundle.model_validate_json(raw))
    if any(
        _CREDENTIAL.search(json.dumps(json.loads(raw), ensure_ascii=False).encode())
        for raw in payloads.values()
    ):
        raise PublicationError("credential or canary marker in public content")
    # No source bytes are read after this point. Validate the captured copy again.
    target = target.absolute()
    for parent in (*reversed(target.parents), target):
        if parent.is_symlink():
            raise PublicationError("publication target contains a symlink")
    releases = target / "releases"
    if releases.is_symlink() or (target / "CURRENT").is_symlink():
        raise PublicationError("publication target contains a symlink")
    if (target / "CURRENT").exists():
        previous = PublicCatalog(target).ready()
        old = {entry.descriptor.demo_id: entry for entry in previous.entries}
        for entry in catalog.entries:
            prior = old[entry.descriptor.demo_id]
            if prior.revision == entry.revision and prior != entry:
                raise PublicationError("changed demo content requires a new revision")
    releases.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=releases))
    try:
        for name, raw in payloads.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        validate_release(stage)
        for directory_path in [
            *sorted((p for p in stage.rglob("*") if p.is_dir()), reverse=True),
            stage,
        ]:
            fd = os.open(directory_path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        destination = releases / catalog.release_id
        if destination.exists() or destination.is_symlink():
            validate_release(destination)
            if any(
                read_regular(destination, *name.split("/")) != raw for name, raw in payloads.items()
            ):
                raise PublicationError("release identity already contains different bytes")
        else:
            stage.rename(destination)
            fd = os.open(releases, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        pointer = target / (".current-" + hashlib.sha256(os.urandom(32)).hexdigest())
        try:
            with pointer.open("x") as stream:
                stream.write(catalog.release_id + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            pointer.replace(target / "CURRENT")
            directory = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            pointer.unlink(missing_ok=True)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return catalog.release_id
