"""Corpus ingestion: walk a source folder, content-address every document, copy
originals, normalize what we can to markdown, and update the manifest idempotently.

Fail-closed like `privacy`: symlinked entries and unreadable files become findings
(``unreadable`` status), never silent skips. Content-hash doc IDs keep re-ingest
idempotent — the same bytes always yield the same ``doc-<sha256[:16]>`` id.

All timestamps enter at the CLI edge via the ``now`` parameter; nothing here calls
``datetime.now()``.
"""

from __future__ import annotations

import email
import fnmatch
import hashlib
import mimetypes
import os
from email.message import Message
from pathlib import Path

import yaml

from mootloop.errors import IngestError
from mootloop.models.common import DocId
from mootloop.models.corpus import (
    CorpusDoc,
    DocRole,
    IngestEntry,
    IngestReport,
    IngestStatus,
    Manifest,
)
from mootloop.vault import atomic_copy, atomic_write_text, safe_vault_path

MAX_BYTES = 50 * 1024 * 1024  # 50 MiB → too_large
_HASH_CHUNK = 1024 * 1024
_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}


# --- tags -------------------------------------------------------------------


class _TagRule:
    """A filename-glob → (role, privileged) rule from a tags file."""

    def __init__(self, pattern: str, role: DocRole | None, privileged: bool | None) -> None:
        self.pattern = pattern
        self.role = role
        self.privileged = privileged


def _load_tag_rules(tags_file: Path | None) -> list[_TagRule]:
    if tags_file is None:
        return []
    if not tags_file.is_file():
        raise IngestError(f"tags file not found: {tags_file}")
    raw = yaml.safe_load(tags_file.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise IngestError(f"tags file must be a glob -> {{role, privileged}} mapping: {tags_file}")
    rules: list[_TagRule] = []
    for pattern, spec in raw.items():
        if not isinstance(spec, dict):
            raise IngestError(f"tag entry for {pattern!r} must be a mapping")
        role_val = spec.get("role")
        role = DocRole(role_val) if role_val is not None else None
        priv = spec.get("privileged")
        if priv is not None and not isinstance(priv, bool):
            raise IngestError(f"tag entry for {pattern!r}: privileged must be a boolean")
        rules.append(_TagRule(str(pattern), role, priv))
    return rules


def _apply_tags(name: str, rules: list[_TagRule]) -> tuple[DocRole | None, bool | None]:
    """Resolve role/privilege for ``name``; last matching rule wins per field."""
    role: DocRole | None = None
    privileged: bool | None = None
    for rule in rules:
        if fnmatch.fnmatch(name, rule.pattern):
            if rule.role is not None:
                role = rule.role
            if rule.privileged is not None:
                privileged = rule.privileged
    return role, privileged


# --- hashing & normalization ------------------------------------------------


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _doc_id_from_hash(hexdigest: str) -> DocId:
    return DocId(f"doc-{hexdigest[:16]}")


def _fallback_doc_id(rel_path: str) -> DocId:
    """A path-derived id for documents we cannot read (symlink/unreadable)."""
    digest = hashlib.sha256(rel_path.encode("utf-8")).hexdigest()
    return DocId(f"doc-{digest[:16]}")


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _normalize_docx(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())


def _eml_body(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    return _decode_text(payload)
        return ""
    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        return _decode_text(payload)
    body = message.get_payload()
    return body if isinstance(body, str) else ""


def _normalize_eml(data: bytes) -> str:
    message = email.message_from_bytes(data)
    front: dict[str, str] = {}
    for header in ("From", "To", "Date", "Subject"):
        value = message.get(header)
        if value is not None:
            front[header.lower()] = str(value)
    front_matter = yaml.safe_dump(front, sort_keys=True, default_flow_style=False).strip()
    body = _eml_body(message).strip()
    return f"---\n{front_matter}\n---\n\n{body}\n"


def _normalize(path: Path, suffix: str, data: bytes | None) -> str | None:
    """Return normalized markdown, or ``None`` when the type needs later conversion.

    ``data`` is the file bytes for text/eml types; ``.docx`` is read from ``path``.
    """
    if suffix in _TEXT_SUFFIXES:
        assert data is not None
        return _decode_text(data)
    if suffix == ".docx":
        return _normalize_docx(path)
    if suffix == ".eml":
        assert data is not None
        return _normalize_eml(data)
    return None  # .pdf and everything else → needs_conversion


# --- ingest -----------------------------------------------------------------


def _iter_source_files(source_dir: Path) -> list[tuple[Path, str]]:
    """Yield ``(full_path, rel_path)`` for non-hidden files, never following symlinks.

    Hidden files and hidden directories are skipped. Symlinked entries are still
    yielded so the caller can fail them closed as ``unreadable``.
    """
    found: list[tuple[Path, str]] = []
    for root, dirnames, filenames in os.walk(source_dir, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full = Path(root) / name
            rel = os.path.relpath(full, source_dir)
            found.append((full, rel))
    return found


def _media_type(name: str, suffix: str) -> str:
    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return guessed
    if suffix == ".md":
        return "text/markdown"
    if suffix == ".eml":
        return "message/rfc822"
    return "application/octet-stream"


def _carry_prior_tags(
    doc_id: str,
    manifest: Manifest,
    role: DocRole | None,
    privileged: bool | None,
) -> tuple[DocRole | None, bool | None]:
    """Preserve a prior manual tag when this ingest does not supply one (idempotent
    re-ingest must not wipe a recorded role/privilege call)."""
    prior = manifest.get(doc_id)
    if prior is None:
        return role, privileged
    return (
        role if role is not None else prior.role,
        privileged if privileged is not None else prior.privileged,
    )


def ingest_folder(
    vault_root: Path | str,
    source_dir: Path | str,
    *,
    now: str,
    tags_file: Path | None = None,
) -> IngestReport:
    """Ingest every file under ``source_dir`` into the vault corpus.

    Copies each original to ``corpus/originals/<doc_id><ext>``, normalizes to
    ``corpus/normalized/<doc_id>.md`` when possible, and updates the manifest
    idempotently. Returns an `IngestReport` grouping documents by status.
    """
    src = Path(source_dir)
    if not src.is_dir():
        raise IngestError(f"source dir not found or not a directory: {src}")

    rules = _load_tag_rules(tags_file)
    manifest = Manifest.load(vault_root)
    entries: list[IngestEntry] = []

    for full, rel in _iter_source_files(src):
        entry = _ingest_one(vault_root, full, rel, now=now, rules=rules, manifest=manifest)
        entries.append(entry)
        manifest.upsert(entry.doc)

    manifest.save(vault_root)
    return IngestReport(entries=entries)


def _ingest_one(
    vault_root: Path | str,
    full: Path,
    rel: str,
    *,
    now: str,
    rules: list[_TagRule],
    manifest: Manifest,
) -> IngestEntry:
    name = full.name
    suffix = full.suffix.lower()
    role, privileged = _apply_tags(name, rules)

    # Fail closed on symlinks — a symlinked source escapes our content addressing.
    if os.path.islink(full):
        doc = _error_doc(
            _fallback_doc_id(rel), name, suffix, "unreadable", now, manifest, role, privileged
        )
        return IngestEntry(doc=doc, reason="symlinked source (fail closed)")

    try:
        size = full.stat().st_size
    except OSError as exc:
        doc = _error_doc(
            _fallback_doc_id(rel), name, suffix, "unreadable", now, manifest, role, privileged
        )
        return IngestEntry(doc=doc, reason=f"stat failed: {exc}")

    if size > MAX_BYTES:
        doc_id = _doc_id_from_hash(_hash_file(full))
        role2, priv2 = _carry_prior_tags(doc_id, manifest, role, privileged)
        _copy_original(vault_root, full, doc_id, suffix)
        doc = CorpusDoc(
            doc_id=doc_id,
            original_name=name,
            media_type=_media_type(name, suffix),
            role=role2,
            privileged=priv2,
            ingest_status="too_large",
            normalized_path=None,
            ingested_at=now,
        )
        return IngestEntry(doc=doc, reason=f"{size} bytes exceeds {MAX_BYTES} limit")

    try:
        data = full.read_bytes()
    except OSError as exc:
        doc = _error_doc(
            _fallback_doc_id(rel), name, suffix, "unreadable", now, manifest, role, privileged
        )
        return IngestEntry(doc=doc, reason=f"read failed: {exc}")

    doc_id = _doc_id_from_hash(hashlib.sha256(data).hexdigest())
    role, privileged = _carry_prior_tags(doc_id, manifest, role, privileged)
    _copy_original(vault_root, full, doc_id, suffix)

    status: IngestStatus
    normalized_rel: str | None
    reason: str | None
    try:
        markdown = _normalize(full, suffix, data)
    except Exception as exc:  # noqa: BLE001 — any normalizer failure degrades, never crashes
        status, normalized_rel, reason = "needs_conversion", None, f"normalizer error: {exc}"
    else:
        if markdown is None:
            status, normalized_rel, reason = "needs_conversion", None, f"no normalizer for {suffix}"
        else:
            normalized_rel = _write_normalized(vault_root, doc_id, markdown)
            status, reason = "ok", None

    doc = CorpusDoc(
        doc_id=doc_id,
        original_name=name,
        media_type=_media_type(name, suffix),
        role=role,
        privileged=privileged,
        ingest_status=status,
        normalized_path=normalized_rel,
        ingested_at=now,
    )
    return IngestEntry(doc=doc, reason=reason)


def _error_doc(
    doc_id: DocId,
    name: str,
    suffix: str,
    status: IngestStatus,
    now: str,
    manifest: Manifest,
    role: DocRole | None,
    privileged: bool | None,
) -> CorpusDoc:
    role, privileged = _carry_prior_tags(doc_id, manifest, role, privileged)
    return CorpusDoc(
        doc_id=doc_id,
        original_name=name,
        media_type=_media_type(name, suffix),
        role=role,
        privileged=privileged,
        ingest_status=status,
        normalized_path=None,
        ingested_at=now,
    )


def _copy_original(vault_root: Path | str, src: Path, doc_id: DocId, suffix: str) -> None:
    dst = safe_vault_path(vault_root, "corpus", "originals", f"{doc_id}{suffix}")
    atomic_copy(src, dst)


def _write_normalized(vault_root: Path | str, doc_id: DocId, markdown: str) -> str:
    rel = f"corpus/normalized/{doc_id}.md"
    path = safe_vault_path(vault_root, "corpus", "normalized", f"{doc_id}.md")
    text = markdown if markdown.endswith("\n") else markdown + "\n"
    atomic_write_text(path, text)
    return rel


# --- non-interactive tagging service ----------------------------------------


def set_doc_tag(
    vault_root: Path | str,
    doc_id: str,
    *,
    role: DocRole | None = None,
    privileged: bool | None = None,
) -> CorpusDoc:
    """Record a role and/or privilege call for one document (append-then-save).

    Only the fields passed are changed; ``None`` leaves a field untouched.
    """
    manifest = Manifest.load(vault_root)
    doc = manifest.get(doc_id)
    if doc is None:
        raise IngestError(f"unknown doc_id: {doc_id}")
    updated = doc.model_copy(
        update={
            "role": role if role is not None else doc.role,
            "privileged": privileged if privileged is not None else doc.privileged,
        }
    )
    manifest.upsert(updated)
    manifest.save(vault_root)
    return updated
