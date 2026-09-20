"""Allowlisted public inputs and fresh local import, separate from public serving."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import ValidationError

from mootloop.models.demo import LocalInputBundle
from mootloop.models.document_task import DocumentTaskInput
from mootloop.models.facts import Fact
from mootloop.models.matter import MatterConfig
from mootloop.models.replay import PreparedReplay
from mootloop.models.requests import RequestSet
from mootloop.vault import atomic_write_text, init_vault, safe_vault_path, validate_id
from mootloop.web.catalog import PublicationError

_INPUT = re.compile(r"^(document|request|replay)-([a-z0-9][a-z0-9._-]{0,63})\.json$")


def validate_bundle(bundle: LocalInputBundle) -> dict[str, tuple[str, ...]]:
    """Validate every permitted file and bind scripts to exact input bytes."""
    destinations: dict[str, tuple[str, ...]] = {}
    documents: dict[str, str] = {}
    discovery: dict[str, str] = {}
    replays: list[PreparedReplay] = []
    matter: MatterConfig | None = None
    try:
        # Revalidate copies too; model_copy intentionally bypasses Pydantic validation.
        bundle = LocalInputBundle.model_validate_json(bundle.model_dump_json())
        for file in bundle.files:
            if file.name == "matter.json":
                matter = MatterConfig.model_validate_json(file.text)
                if matter.attorney is not None:
                    raise PublicationError("bundle must not copy an attorney identity")
                continue
            if file.name == "facts.jsonl":
                if bundle.task != "discovery-responses":
                    raise PublicationError("document task cannot import discovery facts")
                for line in file.text.splitlines():
                    fact = Fact.model_validate_json(line)
                    if fact.reviewed_by or fact.reviewed_at or fact.review_note:
                        raise PublicationError("bundle must not copy fact review identities")
                destinations[file.name] = ("facts", "facts.jsonl")
                discovery["facts/facts.jsonl"] = file.sha256
                continue
            match = _INPUT.fullmatch(file.name)
            if match is None:
                raise PublicationError("bundle filename is outside the input allowlist")
            family, key = match.groups()
            validate_id(key, kind="input_id")
            if family == "document":
                document = DocumentTaskInput.model_validate_json(file.text)
                if document.input_id != key or document.task != bundle.task:
                    raise PublicationError("bundle document identity or task mismatch")
                documents[key] = file.sha256
                destinations[file.name] = ("documents", key + ".json")
            elif family == "request":
                if bundle.task != "discovery-responses":
                    raise PublicationError("document task cannot import discovery requests")
                requests = RequestSet.model_validate_json(file.text)
                if not requests.items:
                    raise PublicationError("empty discovery request set")
                destinations[file.name] = ("requests", key + ".json")
                discovery["requests/" + key + ".json"] = file.sha256
            else:
                replay = PreparedReplay.model_validate_json(file.text)
                if replay.task != bundle.task:
                    raise PublicationError("bundle replay task mismatch")
                replays.append(replay)
                # Replay scripts are returned separately, never installed as run state.
        if matter is None:
            raise PublicationError("bundle requires matter.json")
        if (bundle.task == "business-advice") != (matter.matter_kind == "advisory"):
            raise PublicationError("bundle task and matter kind mismatch")
        if bundle.task == "discovery-responses":
            if not any(path[0] == "requests" for path in destinations.values()):
                raise PublicationError("discovery bundle requires requests")
        elif not documents:
            raise PublicationError("document bundle requires document inputs")
        for replay in replays:
            if replay.document_input_sha256:
                if any(
                    documents.get(key) != digest
                    for key, digest in replay.document_input_sha256.items()
                ):
                    raise PublicationError("replay input digest mismatch")
            elif replay.discovery_input_sha256 != discovery:
                raise PublicationError("replay discovery digest mismatch")
        return destinations
    except ValidationError as exc:
        raise PublicationError("invalid allowlisted input schema") from exc


def import_bundle(
    bundle: LocalInputBundle,
    destination: Path,
    *,
    matter_id: str,
    registry_path: Path | None = None,
) -> Path:
    paths = validate_bundle(bundle)
    validate_id(matter_id, kind="matter_id")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise PublicationError("local import requires an empty destination")
    original = next(file for file in bundle.files if file.name == "matter.json")
    matter = MatterConfig.model_validate_json(original.text)
    matter = MatterConfig.model_validate(matter.model_dump() | {"matter_id": matter_id})
    vault = init_vault(destination, matter, registry_path=registry_path)
    for file in bundle.files:
        if file.name in paths:
            path = safe_vault_path(vault, *paths[file.name])
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, file.text)
    return vault
