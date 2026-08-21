"""Corpus ingestion: walk a source folder, content-address every document, copy
originals, normalize what we can to markdown, and update the manifest idempotently.

Fail-closed like `privacy`: symlinked entries and unreadable files become findings
(``unreadable`` status), never silent skips. Content-hash doc IDs keep re-ingest
idempotent — the same bytes always yield the same ``doc-<sha256[:16]>`` id.

All timestamps enter at the CLI edge via the ``now`` parameter; nothing here calls
``datetime.now()``.
"""

from __future__ import annotations

import contextlib
import email
import fcntl
import fnmatch
import hashlib
import mimetypes
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import Message
from pathlib import Path

import yaml

from mootloop.errors import IngestError
from mootloop.models.common import DocId
from mootloop.models.corpus import (
    CorpusDoc,
    DocRole,
    IngestAction,
    IngestActionKind,
    IngestEntry,
    IngestReport,
    IngestStatus,
    Manifest,
    TriageIssue,
)
from mootloop.vault import atomic_write_text, fsync_file_and_parent, safe_vault_path

MAX_BYTES = 50 * 1024 * 1024  # 50 MiB → too_large
_HASH_CHUNK = 1024 * 1024
_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
_ENCRYPTED_OFFICE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


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


def _doc_id_from_hash(hexdigest: str) -> DocId:
    return DocId(f"doc-{hexdigest[:16]}")


def content_doc_id(data: bytes) -> DocId:
    """The content-addressed ``doc-<sha256[:16]>`` id for ``data``.

    The same scheme `ingest_folder` assigns, so a served document parsed directly
    keys on the same id it would receive if ingested into the corpus.
    """
    return _doc_id_from_hash(hashlib.sha256(data).hexdigest())


@dataclass(frozen=True)
class _SourceCapture:
    doc_id: DocId
    size: int
    data: bytes | None
    original_path: Path


def _capture_original(
    vault_root: Path | str,
    source: Path,
    suffix: str,
) -> _SourceCapture:
    """Copy one stable no-follow source snapshot into the vault while hashing it."""
    originals = safe_vault_path(vault_root, "corpus", "originals")
    originals.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=originals, prefix=".tmp-ingest-", suffix=suffix)
    source_fd = -1
    try:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("source is not a regular file")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        captured = True
        total = 0
        with os.fdopen(tmp_fd, "wb") as output:
            tmp_fd = -1
            while chunk := os.read(source_fd, _HASH_CHUNK):
                total += len(chunk)
                digest.update(chunk)
                output.write(chunk)
                if captured and total <= MAX_BYTES:
                    chunks.append(chunk)
                else:
                    captured = False
                    chunks.clear()
            output.flush()
            os.fsync(output.fileno())
        after = os.fstat(source_fd)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise OSError("source changed while it was being ingested")
        if total != after.st_size:
            raise OSError("source size changed while it was being ingested")
        doc_id = _doc_id_from_hash(digest.hexdigest())
        destination = safe_vault_path(
            vault_root, "corpus", "originals", f"{doc_id}{suffix}"
        )
        os.replace(tmp_name, destination)
        fsync_file_and_parent(destination)
        return _SourceCapture(
            doc_id=doc_id,
            size=total,
            data=b"".join(chunks) if captured else None,
            original_path=destination,
        )
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if tmp_fd >= 0:
            os.close(tmp_fd)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


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


def _conversion_issue(suffix: str, data: bytes) -> TriageIssue | None:
    """Classify formats that need a protected follow-up before normalization."""
    if not data:
        return "corrupt"
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            return "corrupt"
        if b"/Encrypt" in data:
            return "password_protected"
        text_markers = (b" BT", b"\nBT", b" Tj", b" TJ", b"/Font", b"/ToUnicode")
        image_markers = (b"/Image", b"\x89PNG", b"\xff\xd8\xff")
        if not any(marker in data for marker in text_markers) and any(
            marker in data for marker in image_markers
        ):
            return "needs_ocr"
        return "unsupported_format"
    if suffix == ".docx" and data.startswith(_ENCRYPTED_OFFICE_MAGIC):
        return "password_protected"
    if suffix not in {*_TEXT_SUFFIXES, ".docx", ".eml"}:
        return "unsupported_format"
    return None


# --- ingest -----------------------------------------------------------------


def _iter_source_files(source_dir: Path) -> list[tuple[Path, str]]:
    """Yield ``(full_path, rel_path)`` for non-hidden files, never following symlinks.

    Hidden files and hidden directories are skipped. Symlinked entries are still
    yielded so the caller can fail them closed as ``unreadable``.
    """
    found: list[tuple[Path, str]] = []
    for root, dirnames, filenames in os.walk(source_dir, followlinks=False):
        visible_dirs: list[str] = []
        for dirname in sorted(d for d in dirnames if not d.startswith(".")):
            candidate = Path(root) / dirname
            if os.path.islink(candidate):
                found.append((candidate, os.path.relpath(candidate, source_dir)))
            else:
                visible_dirs.append(dirname)
        dirnames[:] = visible_dirs
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


@contextmanager
def _manifest_lock(vault_root: Path | str) -> Iterator[None]:
    lock_path = safe_vault_path(vault_root, "corpus", ".manifest.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


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
    if os.path.islink(src) or not src.is_dir():
        raise IngestError(f"source dir not found or not a directory: {src}")

    rules = _load_tag_rules(tags_file)
    with _manifest_lock(vault_root):
        manifest = Manifest.load(vault_root)
        entries: list[IngestEntry] = []
        for full, rel in _iter_source_files(src):
            entry = _ingest_one(vault_root, full, rel, now=now, rules=rules, manifest=manifest)
            entries.append(entry)
            manifest.upsert(entry.doc)
        manifest.save(vault_root)
        return IngestReport(entries=entries, actions=_actions_for_manifest(manifest))


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
        capture = _capture_original(vault_root, full, suffix)
    except OSError as exc:
        doc = _error_doc(
            _fallback_doc_id(rel), name, suffix, "unreadable", now, manifest, role, privileged
        )
        return IngestEntry(doc=doc, reason=f"stable capture failed: {exc}")

    doc_id = capture.doc_id
    role, privileged = _carry_prior_tags(doc_id, manifest, role, privileged)
    if capture.size > MAX_BYTES:
        doc = CorpusDoc(
            doc_id=doc_id,
            original_name=name,
            media_type=_media_type(name, suffix),
            role=role,
            privileged=privileged,
            ingest_status="too_large",
            triage_issue="too_large",
            normalized_path=None,
            ingested_at=now,
        )
        return IngestEntry(
            doc=doc,
            reason=f"{capture.size} bytes exceeds {MAX_BYTES} limit",
        )

    data = capture.data
    assert data is not None

    status: IngestStatus
    triage_issue: TriageIssue | None
    normalized_rel: str | None
    reason: str | None
    preflight_issue = _conversion_issue(suffix, data)
    if preflight_issue is not None:
        status, triage_issue, normalized_rel = "needs_conversion", preflight_issue, None
        reason = f"{preflight_issue.replace('_', ' ')}: protected conversion required"
    else:
        try:
            markdown = _normalize(capture.original_path, suffix, data)
        except Exception as exc:  # noqa: BLE001 — any normalizer failure degrades, never crashes
            status, triage_issue, normalized_rel = "needs_conversion", "corrupt", None
            reason = f"normalizer error (corrupt or unsupported file): {exc}"
        else:
            if markdown is None:
                status, triage_issue, normalized_rel = (
                    "needs_conversion",
                    "unsupported_format",
                    None,
                )
                reason = f"no normalizer for {suffix}"
            else:
                normalized_rel = _write_normalized(vault_root, doc_id, markdown)
                status, triage_issue, reason = "ok", None, None

    doc = CorpusDoc(
        doc_id=doc_id,
        original_name=name,
        media_type=_media_type(name, suffix),
        role=role,
        privileged=privileged,
        ingest_status=status,
        triage_issue=triage_issue,
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
        triage_issue="unreadable" if status == "unreadable" else None,
        normalized_path=None,
        ingested_at=now,
    )


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
    with _manifest_lock(vault_root):
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


_ACTION_REASONS: dict[IngestActionKind, str] = {
    "confirm_role": "confirm the document role",
    "confirm_privilege": "confirm whether the document is privileged",
    "needs_ocr": "run OCR in the protected conversion sandbox",
    "password_protected": "supply an authorized password in the protected conversion flow",
    "corrupt": "replace or repair the corrupt source file",
    "unsupported_format": "convert the unsupported format in the protected conversion sandbox",
    "too_large": "split or explicitly authorize handling of the oversized file",
    "unreadable": "repair permissions or replace the unreadable source",
}
_LEGACY_ISSUE_BY_STATUS: dict[IngestStatus, TriageIssue | None] = {
    "ok": None,
    "needs_conversion": "unsupported_format",
    "too_large": "too_large",
    "unreadable": "unreadable",
}


def _action(doc: CorpusDoc, kind: IngestActionKind) -> IngestAction:
    digest = hashlib.sha256(f"{doc.doc_id}\x00{kind}".encode()).hexdigest()[:16]
    return IngestAction(
        action_id=f"ingest-action-{digest}",
        doc_id=doc.doc_id,
        original_name=doc.original_name,
        kind=kind,
        reason=_ACTION_REASONS[kind],
    )


def _actions_for_manifest(manifest: Manifest) -> list[IngestAction]:
    actions: list[IngestAction] = []
    for doc in sorted(manifest.docs, key=lambda item: str(item.doc_id)):
        if doc.role is None:
            actions.append(_action(doc, "confirm_role"))
        if doc.privileged is None:
            actions.append(_action(doc, "confirm_privilege"))
        issue = doc.triage_issue
        if issue is None:
            issue = _LEGACY_ISSUE_BY_STATUS[doc.ingest_status]
        if issue is not None:
            actions.append(_action(doc, issue))
    return actions


def ingest_actions(vault_root: Path | str) -> list[IngestAction]:
    """Derive the stable protected action queue from the current corpus manifest."""
    return _actions_for_manifest(Manifest.load(vault_root))
