"""Bounded CourtListener opinion snapshots and deterministic evidence passages."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from mootloop.citations import http
from mootloop.errors import CitationError
from mootloop.models.citations import (
    AuthorityPassage,
    CitationProposition,
    OpinionAuthorityStoreRecord,
)
from mootloop.models.common import CitationId
from mootloop.vault import atomic_write_once_text, safe_vault_path

CL_HOST = "www.courtlistener.com"
TOKEN_ENV = "COURTLISTENER_TOKEN"
MAX_OPINIONS = 8
MAX_AUTHORITY_CHARS = 2_000_000
MAX_PASSAGES = 8
MAX_PASSAGE_CHARS = 4096
MAX_PASSAGE_TOTAL_CHARS = 16_384
AUTHORITY_DIR = ("law", "authorities")

_CLUSTER_URL = re.compile(
    r"^https://www\.courtlistener\.com/opinion/(?P<cluster_id>[1-9][0-9]*)/[^/?#]+/$"
)
_OPINION_API_URL = re.compile(
    r"^(?:https://www\.courtlistener\.com)?/api/rest/v4/opinions/"
    r"(?P<opinion_id>[1-9][0-9]*)/$"
)
_TOKEN = re.compile(r"[a-z0-9]{3,}")
_PARAGRAPH_BREAK = re.compile(r"\n{2,}")


@dataclass(frozen=True)
class AuthorityFetchResult:
    snapshot: OpinionAuthorityStoreRecord | None
    note: str = ""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"p", "div", "li", "blockquote", "br", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag in {"p", "div", "li", "blockquote", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)


def _plain_text(value: str, *, html: bool) -> str:
    if html:
        parser = _TextExtractor()
        parser.feed(value)
        parser.close()
        value = "".join(parser.parts)
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n\n".join(line for line in lines if line)


def _opinion_text(payload: dict[str, Any]) -> str:
    for field in ("html_with_citations", "html_lawbox", "html_columbia", "html"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return _plain_text(value, html=True)
    value = payload.get("plain_text")
    if isinstance(value, str) and value.strip():
        return _plain_text(value, html=False)
    return ""


def _opinion_ids(payload: dict[str, Any]) -> list[int]:
    raw = payload.get("sub_opinions")
    if not isinstance(raw, list):
        return []
    ids: list[int] = []
    for item in raw:
        candidate: object = item
        if isinstance(item, dict):
            candidate = item.get("resource_uri") or item.get("url") or item.get("id")
        if isinstance(candidate, int) and candidate > 0:
            opinion_id = candidate
        elif isinstance(candidate, str):
            match = _OPINION_API_URL.fullmatch(candidate)
            if match is None:
                continue
            opinion_id = int(match.group("opinion_id"))
        else:
            continue
        if opinion_id not in ids:
            ids.append(opinion_id)
        if len(ids) == MAX_OPINIONS:
            break
    return ids


def fetch_case_authority(
    *,
    citation_id: CitationId | str,
    source_url: str,
    fetched_at: str,
    transport: http.Transport | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> AuthorityFetchResult:
    """Capture opinion text using only IDs parsed from a canonical CourtListener URL."""
    match = _CLUSTER_URL.fullmatch(source_url)
    if match is None:
        raise CitationError("source is not a canonical CourtListener opinion URL")
    renew = heartbeat or (lambda: None)
    cluster_id = int(match.group("cluster_id"))
    renew()
    try:
        cluster = http.fetch(
            http.HttpRequest(
                "GET",
                CL_HOST,
                f"/api/rest/v4/clusters/{cluster_id}/",
                auth_token_env=TOKEN_ENV,
            ),
            transport=transport,
        )
    except http.HttpError as exc:
        return AuthorityFetchResult(None, f"CourtListener cluster error: {type(exc).__name__}")
    renew()
    if cluster.status_code != 200 or not isinstance(cluster.json_body, dict):
        return AuthorityFetchResult(None, f"CourtListener cluster returned {cluster.status_code}")
    opinion_ids = _opinion_ids(cluster.json_body)
    if not opinion_ids:
        return AuthorityFetchResult(None, "CourtListener cluster returned no usable opinion IDs")

    texts: list[str] = []
    captured_ids: list[int] = []
    for opinion_id in opinion_ids:
        renew()
        try:
            response = http.fetch(
                http.HttpRequest(
                    "GET",
                    CL_HOST,
                    f"/api/rest/v4/opinions/{opinion_id}/",
                    auth_token_env=TOKEN_ENV,
                ),
                transport=transport,
            )
        except http.HttpError as exc:
            return AuthorityFetchResult(
                None, f"CourtListener opinion error: {type(exc).__name__}"
            )
        renew()
        if response.status_code != 200 or not isinstance(response.json_body, dict):
            return AuthorityFetchResult(
                None, f"CourtListener opinion returned {response.status_code}"
            )
        text = _opinion_text(response.json_body)
        if text:
            texts.append(text)
            captured_ids.append(opinion_id)
    combined = "\n\n".join(texts)
    if not combined:
        return AuthorityFetchResult(None, "CourtListener opinions contained no usable text")
    if len(combined) > MAX_AUTHORITY_CHARS:
        return AuthorityFetchResult(None, "CourtListener opinion text exceeds the safety ceiling")
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    return AuthorityFetchResult(
        OpinionAuthorityStoreRecord(
            citation_id=CitationId(str(citation_id)),
            cluster_id=cluster_id,
            opinion_ids=captured_ids,
            source_url=f"https://{CL_HOST}/opinion/{cluster_id}/",
            fetched_at=fetched_at,
            content_sha256=digest,
            text=combined,
        )
    )


def _paragraphs(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    start = 0
    for match in _PARAGRAPH_BREAK.finditer(text):
        end = match.start()
        if end > start:
            spans.append((start, end, text[start:end]))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text), text[start:]))
    return spans


def select_passages(
    authority: OpinionAuthorityStoreRecord,
    proposition: CitationProposition,
) -> list[AuthorityPassage]:
    """Select bounded excerpts deterministically; selection cannot steer egress."""
    terms = set(_TOKEN.findall(proposition.proposition_text.casefold()))
    ranked: list[tuple[int, int, int, str]] = []
    for start, end, text in _paragraphs(authority.text):
        score = len(terms.intersection(_TOKEN.findall(text.casefold())))
        ranked.append((score, start, end, text))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    passages: list[AuthorityPassage] = []
    total = 0
    for score, start, _end, text in ranked:
        if score == 0 and passages:
            break
        if len(passages) == MAX_PASSAGES or total >= MAX_PASSAGE_TOTAL_CHARS:
            break
        ceiling = min(MAX_PASSAGE_CHARS, MAX_PASSAGE_TOTAL_CHARS - total)
        folded = text.casefold()
        hits = [folded.find(term) for term in terms if term in folded]
        focus = min(hits) if hits else 0
        relative_start = max(0, focus - ceiling // 2)
        relative_end = min(len(text), relative_start + ceiling)
        relative_start = max(0, relative_end - ceiling)
        bounded = text[relative_start:relative_end]
        if not bounded:
            continue
        bounded_start = start + relative_start
        bounded_end = bounded_start + len(bounded)
        text_sha = hashlib.sha256(bounded.encode("utf-8")).hexdigest()
        identity = f"{authority.content_sha256}\n{bounded_start}\n{bounded_end}\n{text_sha}"
        passage_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        passages.append(
            AuthorityPassage(
                passage_id=f"passage-{passage_digest}",
                text=bounded,
                start=bounded_start,
                end=bounded_end,
                text_sha256=text_sha,
                authority_sha256=authority.content_sha256,
                source_url=authority.source_url,
            )
        )
        total += len(bounded)
    return passages


class OpinionAuthorityStore:
    """Content-addressed, write-once public authority snapshots in the matter vault."""

    def __init__(self, vault_root: Path | str) -> None:
        self.vault_root = vault_root

    @staticmethod
    def _filename(citation_id: CitationId | str, content_sha256: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
            raise CitationError("authority content digest must be lowercase SHA-256")
        return f"{citation_id}-{content_sha256}.json"

    def _path(self, citation_id: CitationId | str, content_sha256: str) -> Path:
        return safe_vault_path(
            self.vault_root,
            *AUTHORITY_DIR,
            self._filename(citation_id, content_sha256),
        )

    def capture(self, record: OpinionAuthorityStoreRecord) -> Path:
        path = self._path(record.citation_id, record.content_sha256)
        serialized = record.model_dump_json()
        try:
            atomic_write_once_text(path, serialized)
        except FileExistsError:
            existing = self.load(record.citation_id, record.content_sha256)
            if existing.model_copy(update={"fetched_at": record.fetched_at}) != record:
                raise CitationError("content-addressed authority snapshot conflicts") from None
        return path

    def load(
        self, citation_id: CitationId | str, content_sha256: str
    ) -> OpinionAuthorityStoreRecord:
        path = self._path(citation_id, content_sha256)
        if not path.is_file():
            raise CitationError("authority snapshot is missing")
        record = OpinionAuthorityStoreRecord.model_validate_json(path.read_text(encoding="utf-8"))
        if record.citation_id != citation_id or record.content_sha256 != content_sha256:
            raise CitationError("authority snapshot identity does not match its path")
        actual = hashlib.sha256(record.text.encode("utf-8")).hexdigest()
        if actual != record.content_sha256:
            raise CitationError("authority snapshot content hash does not match")
        return record
