"""Protected, recoverable document conversion through a fixed folio-enrich endpoint."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from mootloop.conversion_client import (
    FOLIO_ENRICH_COMMIT,
    MAX_CONVERSION_INPUT_BYTES,
    MAX_CONVERTER_OUTPUT_BYTES,
    SUPPORTED_CONVERSION_SUFFIXES,
    FolioEnrichConverter,
    validate_converter_output,
    validate_folio_enrich_commit,
    validate_folio_enrich_image,
)
from mootloop.errors import ConversionError, IngestError
from mootloop.ingest import content_doc_id, promote_converted_document, write_normalized_text
from mootloop.models.common import DocId, MatterId, canonical_json_sha256
from mootloop.models.conversion import ConversionReceipt, conversion_receipt_sha256
from mootloop.models.corpus import CorpusDoc, Manifest
from mootloop.vault import (
    atomic_write_once_text,
    fsync_file_and_parent,
    load_matter,
    safe_vault_path,
)

_READ_CHUNK = 1024 * 1024
_DOC_ID_RE = re.compile(r"^doc-[0-9a-f]{16}$")

__all__ = [
    "FOLIO_ENRICH_COMMIT",
    "FolioEnrichConverter",
    "convert_corpus_document",
]


class DocumentConverter(Protocol):
    name: str
    image_ref: str
    source_commit: str

    def convert(self, data: bytes, filename: str) -> str: ...


@contextmanager
def _conversion_lock(vault_root: Path | str, doc_id: str) -> Iterator[None]:
    path = _exact_vault_path(vault_root, "corpus", "conversions", f"{doc_id}.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read_stable_regular(path: Path, *, limit: int, label: str) -> bytes:
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ConversionError(f"{label} is not a regular file")
        if before.st_size > limit:
            raise ConversionError(f"{label} exceeds the protected conversion limit")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(fd, _READ_CHUNK):
            total += len(chunk)
            if total > limit:
                raise ConversionError(f"{label} exceeds the protected conversion limit")
            chunks.append(chunk)
        after = os.fstat(fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ConversionError(f"{label} changed while being read")
        return b"".join(chunks)
    except OSError as exc:
        raise ConversionError(f"cannot read {label}: {exc}") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _exact_vault_path(vault_root: Path | str, *parts: str) -> Path:
    """Resolve through the vault choke point and reject any symlinked path component."""
    root = Path(vault_root).resolve()
    expected = root.joinpath(*parts)
    resolved = safe_vault_path(vault_root, *parts)
    if resolved != expected:
        raise ConversionError("protected conversion path contains a symlink")
    return resolved


def _load_original(vault_root: Path | str, doc: CorpusDoc) -> tuple[bytes, str]:
    suffix = Path(doc.original_name).suffix.lower()
    if suffix not in SUPPORTED_CONVERSION_SUFFIXES:
        raise ConversionError(f"unsupported protected conversion suffix: {suffix or '<none>'}")
    path = _exact_vault_path(vault_root, "corpus", "originals", f"{doc.doc_id}{suffix}")
    data = _read_stable_regular(
        path,
        limit=MAX_CONVERSION_INPUT_BYTES,
        label="conversion original",
    )
    if content_doc_id(data) != doc.doc_id:
        raise ConversionError("conversion original content no longer matches its doc_id")
    return data, suffix


def _conversion_id(
    matter_id: MatterId,
    doc_id: DocId,
    input_sha256: str,
    converter: DocumentConverter,
) -> str:
    digest = canonical_json_sha256(
        {
            "matter_id": matter_id,
            "doc_id": doc_id,
            "input_sha256": input_sha256,
            "converter": converter.name,
            "image": converter.image_ref,
            "commit": converter.source_commit,
        }
    )
    return f"conversion-{digest[:24]}"


def _receipt_path(vault_root: Path | str, conversion_id: str) -> Path:
    return _exact_vault_path(
        vault_root, "corpus", "conversions", f"{conversion_id}.json"
    )


def _load_receipt(path: Path) -> ConversionReceipt:
    try:
        raw = _read_stable_regular(
            path,
            limit=64 * 1024,
            label="conversion receipt",
        )
        return ConversionReceipt.model_validate_json(raw)
    except (OSError, ValidationError, UnicodeDecodeError) as exc:
        raise ConversionError(f"conversion receipt is unreadable or invalid: {exc}") from exc


def _validate_recovery(
    receipt: ConversionReceipt,
    *,
    matter_id: MatterId,
    doc: CorpusDoc,
    input_sha256: str,
    conversion_id: str,
    converter: DocumentConverter,
    normalized_path: Path,
) -> None:
    expected = {
        "source_matter_id": matter_id,
        "conversion_id": conversion_id,
        "doc_id": doc.doc_id,
        "input_sha256": input_sha256,
        "normalized_path": f"corpus/normalized/{doc.doc_id}.md",
        "converter": converter.name,
        "converter_image": converter.image_ref,
        "converter_commit": converter.source_commit,
    }
    actual = {key: getattr(receipt, key) for key in expected}
    if actual != expected:
        raise ConversionError("conversion receipt does not match this exact conversion")
    output = _read_stable_regular(
        normalized_path,
        limit=MAX_CONVERTER_OUTPUT_BYTES,
        label="conversion output",
    )
    if hashlib.sha256(output).hexdigest() != receipt.output_sha256:
        raise ConversionError("conversion receipt output digest does not match the output")


def _new_receipt(
    *,
    matter_id: MatterId,
    conversion_id: str,
    doc: CorpusDoc,
    input_sha256: str,
    output_sha256: str,
    converter: DocumentConverter,
    converted_at: str,
    actor: str,
) -> ConversionReceipt:
    provisional = ConversionReceipt.model_construct(
        schema_version="1.0",
        source_matter_id=matter_id,
        conversion_id=conversion_id,
        doc_id=doc.doc_id,
        input_sha256=input_sha256,
        output_sha256=output_sha256,
        normalized_path=f"corpus/normalized/{doc.doc_id}.md",
        converter=converter.name,
        converter_image=converter.image_ref,
        converter_commit=converter.source_commit,
        converted_at=converted_at,
        actor=actor,
        receipt_sha256="0" * 64,
    )
    values = provisional.model_dump(mode="json")
    values["receipt_sha256"] = conversion_receipt_sha256(provisional)
    return ConversionReceipt.model_validate(values)


def convert_corpus_document(
    vault_root: Path | str,
    doc_id: str,
    *,
    converter: DocumentConverter,
    converted_at: str,
    actor: str,
) -> tuple[CorpusDoc, ConversionReceipt]:
    """Convert, commit, and promote one reviewed corpus document idempotently."""
    if not _DOC_ID_RE.fullmatch(doc_id):
        raise ConversionError("doc_id must be a canonical content-derived document id")
    validate_folio_enrich_image(converter.image_ref)
    validate_folio_enrich_commit(converter.source_commit)
    if converter.name != "folio-enrich":
        raise ConversionError("only the reviewed folio-enrich converter is allowed")
    if not actor.strip() or not converted_at.strip():
        raise ConversionError("conversion actor and timestamp are required")
    with _conversion_lock(vault_root, doc_id):
        manifest = Manifest.load(vault_root)
        doc = manifest.get(doc_id)
        if doc is None:
            raise ConversionError(f"unknown doc_id: {doc_id}")
        issue = doc.triage_issue or (
            "unsupported_format" if doc.ingest_status == "needs_conversion" else None
        )
        if doc.ingest_status not in {"needs_conversion", "ok"} or (
            doc.ingest_status == "needs_conversion" and issue != "unsupported_format"
        ):
            raise ConversionError(
                f"document {doc_id} requires a manual protected action before conversion"
            )
        data, suffix = _load_original(vault_root, doc)
        input_sha256 = hashlib.sha256(data).hexdigest()
        matter_id = MatterId(load_matter(vault_root).matter_id)
        conversion_id = _conversion_id(matter_id, doc.doc_id, input_sha256, converter)
        receipt_path = _receipt_path(vault_root, conversion_id)
        normalized_path = _exact_vault_path(
            vault_root, "corpus", "normalized", f"{doc.doc_id}.md"
        )
        if receipt_path.exists():
            receipt = _load_receipt(receipt_path)
            _validate_recovery(
                receipt,
                matter_id=matter_id,
                doc=doc,
                input_sha256=input_sha256,
                conversion_id=conversion_id,
                converter=converter,
                normalized_path=normalized_path,
            )
        else:
            converter_filename = f"{doc.doc_id}{suffix}"
            output = validate_converter_output(converter.convert(data, converter_filename))
            normalized_rel = write_normalized_text(vault_root, doc.doc_id, output)
            output_bytes = normalized_path.read_bytes()
            receipt = _new_receipt(
                matter_id=matter_id,
                conversion_id=conversion_id,
                doc=doc,
                input_sha256=input_sha256,
                output_sha256=hashlib.sha256(output_bytes).hexdigest(),
                converter=converter,
                converted_at=converted_at,
                actor=actor,
            )
            if normalized_rel != receipt.normalized_path:
                raise ConversionError("normalized conversion path is inconsistent")
            try:
                atomic_write_once_text(
                    receipt_path,
                    receipt.model_dump_json(indent=2) + "\n",
                )
            except FileExistsError:
                receipt = _load_receipt(receipt_path)
                _validate_recovery(
                    receipt,
                    matter_id=matter_id,
                    doc=doc,
                    input_sha256=input_sha256,
                    conversion_id=conversion_id,
                    converter=converter,
                    normalized_path=normalized_path,
                )
            fsync_file_and_parent(receipt_path)
        try:
            promoted = promote_converted_document(vault_root, doc_id)
        except IngestError as exc:
            raise ConversionError(str(exc)) from exc
        return promoted, receipt
