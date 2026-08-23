"""The sole, fixed-destination HTTP client for protected folio-enrich extraction."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from mootloop.errors import ConversionError
from mootloop.runtime import RuntimeMode

FOLIO_ENRICH_COMMIT = "f5364365346d93a3aa01fd5fecf219090afe5410"
FOLIO_ENRICH_ENDPOINT_ENV = "MOOTLOOP_FOLIO_ENRICH_URL"
FOLIO_ENRICH_IMAGE_ENV = "MOOTLOOP_FOLIO_ENRICH_IMAGE"
FOLIO_ENRICH_COMMIT_ENV = "MOOTLOOP_FOLIO_ENRICH_COMMIT"

MAX_CONVERSION_INPUT_BYTES = 50 * 1024 * 1024
MAX_CONVERTER_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_CONVERTER_RESPONSE_BYTES = MAX_CONVERTER_OUTPUT_BYTES + 64 * 1024
_IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_REPOSITORY_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?"
    r"(?::(?P<port>[0-9]{1,5})(?=/))?"
    r"(?:/[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?)*$"
)
_SOURCE_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_FORMAT_BY_SUFFIX = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".rtf": "rtf",
    ".html": "html",
    ".htm": "html",
    ".eml": "email",
    ".msg": "email",
}
SUPPORTED_CONVERSION_SUFFIXES = frozenset(_FORMAT_BY_SUFFIX)


def conversion_format_for_suffix(suffix: str) -> str:
    """Return the exact folio-enrich parser format selected for a supported suffix."""
    try:
        return _FORMAT_BY_SUFFIX[suffix]
    except KeyError as exc:
        raise ConversionError(
            f"unsupported protected conversion suffix: {suffix or '<none>'}"
        ) from exc


def validate_folio_enrich_image(image_ref: str) -> str:
    """Require an OCI digest reference; mutable tags are never accepted."""
    repository, separator, digest = image_ref.rpartition("@")
    if not separator:
        digest = image_ref
    repository_match = _IMAGE_REPOSITORY_RE.fullmatch(repository) if repository else None
    port = repository_match.group("port") if repository_match is not None else None
    if (
        not _IMAGE_DIGEST_RE.fullmatch(digest)
        or (separator and repository_match is None)
        or (port is not None and not 1 <= int(port) <= 65535)
    ):
        raise ConversionError("folio-enrich image must be pinned by a lowercase SHA-256 digest")
    return image_ref


def validate_folio_enrich_commit(source_commit: str) -> str:
    """Require the exact reviewed folio-enrich source commit."""
    if not _SOURCE_COMMIT_RE.fullmatch(source_commit) or source_commit != FOLIO_ENRICH_COMMIT:
        raise ConversionError(
            f"folio-enrich source commit must match reviewed commit {FOLIO_ENRICH_COMMIT}"
        )
    return source_commit


def _validate_endpoint(endpoint: str, runtime_mode: RuntimeMode) -> str:
    expected = (
        "http://folio-enrich:8731"
        if runtime_mode is RuntimeMode.HOSTED
        else "http://127.0.0.1:8731"
    )
    parsed = urlsplit(endpoint)
    if (
        endpoint != expected
        or parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ConversionError(f"folio-enrich endpoint must be exactly {expected}")
    return endpoint


def validate_converter_output(text: object) -> str:
    if not isinstance(text, str) or not text or "\x00" in text:
        raise ConversionError("converter output must be non-empty text without NUL bytes")
    normalized = text if text.endswith("\n") else text + "\n"
    if len(normalized.encode("utf-8")) > MAX_CONVERTER_OUTPUT_BYTES:
        raise ConversionError("converter output exceeds the protected conversion limit")
    return normalized


class FolioEnrichConverter:
    """Narrow client for folio-enrich's extraction-only API."""

    name = "folio-enrich"

    def __init__(
        self,
        *,
        endpoint: str,
        image_ref: str,
        source_commit: str,
        runtime_mode: RuntimeMode,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.endpoint = _validate_endpoint(endpoint, runtime_mode)
        self.image_ref = validate_folio_enrich_image(image_ref)
        self.source_commit = validate_folio_enrich_commit(source_commit)
        self._transport = transport

    @classmethod
    def from_env(cls, runtime_mode: RuntimeMode) -> FolioEnrichConverter:
        expected_endpoint = (
            "http://folio-enrich:8731"
            if runtime_mode is RuntimeMode.HOSTED
            else "http://127.0.0.1:8731"
        )
        return cls(
            endpoint=os.environ.get(FOLIO_ENRICH_ENDPOINT_ENV, expected_endpoint),
            image_ref=os.environ.get(FOLIO_ENRICH_IMAGE_ENV, ""),
            source_commit=os.environ.get(FOLIO_ENRICH_COMMIT_ENV, ""),
            runtime_mode=runtime_mode,
        )

    def convert(self, data: bytes, filename: str) -> str:
        if (
            not filename
            or Path(filename).name != filename
            or "\\" in filename
            or len(filename) > 255
            or any(ord(character) < 32 for character in filename)
        ):
            raise ConversionError("converter filename must be safe basename metadata")
        suffix = Path(filename).suffix.lower()
        format_name = conversion_format_for_suffix(suffix)
        if len(data) > MAX_CONVERSION_INPUT_BYTES:
            raise ConversionError("conversion input exceeds the protected conversion limit")
        request = {
            "content": base64.b64encode(data).decode("ascii"),
            "format": format_name,
            "filename": filename,
        }
        try:
            with httpx.Client(
                transport=self._transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(60.0),
            ) as client, client.stream(
                "POST", f"{self.endpoint}/enrich/extract", json=request
            ) as response:
                if response.status_code != 200:
                    raise ConversionError(
                        f"folio-enrich returned HTTP {response.status_code}"
                    )
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_CONVERTER_RESPONSE_BYTES:
                        raise ConversionError("converter response exceeds the protected limit")
                    chunks.append(chunk)
        except ConversionError:
            raise
        except httpx.HTTPError as exc:
            raise ConversionError(f"folio-enrich request failed: {exc}") from exc
        try:
            payload = json.loads(b"".join(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConversionError("folio-enrich returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ConversionError("folio-enrich response must be a JSON object")
        if payload.get("format") != format_name or payload.get("filename") != filename:
            raise ConversionError("folio-enrich response metadata does not match the request")
        return validate_converter_output(payload.get("text"))
