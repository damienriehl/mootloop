"""U-04B isolated folio-enrich conversion and recovery contracts."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from mootloop.cli import app
from mootloop.conversion import (
    FOLIO_ENRICH_COMMIT,
    ConversionError,
    FolioEnrichConverter,
    convert_corpus_document,
)
from mootloop.conversion_client import MAX_CONVERTER_OUTPUT_BYTES, validate_converter_output
from mootloop.ingest import ingest_actions, ingest_folder, set_doc_tag
from mootloop.models.corpus import DocRole, Manifest
from mootloop.runtime import RuntimeMode
from mootloop.vault import create_vault
from tests.conftest import make_matter

NOW = "2026-08-21T12:00:00+00:00"
IMAGE = "ghcr.io/alea-institute/folio-enrich@sha256:" + "a" * 64
runner = CliRunner()


class FakeConverter:
    name = "folio-enrich"
    image_ref = IMAGE
    source_commit = FOLIO_ENRICH_COMMIT

    def __init__(self, text: str = "Converted evidence.\n") -> None:
        self.text = text
        self.calls = 0
        self.last_filename: str | None = None

    def convert(self, data: bytes, filename: str) -> str:
        self.calls += 1
        self.last_filename = filename
        return self.text


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    create_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
    return vault


def _reviewed_pdf(tmp_path: Path) -> tuple[Path, str]:
    vault = _vault(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "evidence.pdf").write_bytes(b"%PDF-1.7\n BT /Font 12 Tf (evidence) Tj ET")
    doc = ingest_folder(vault, source, now=NOW).entries[0].doc
    set_doc_tag(vault, doc.doc_id, role=DocRole.CLIENT_DOC, privileged=False)
    return vault, str(doc.doc_id)


def test_folio_client_uses_only_fixed_extract_endpoint_and_bounded_json() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        assert base64.b64decode(body["content"]) == b"%PDF-1.7"
        assert body["format"] == "pdf"
        assert body["filename"] == "doc-safe.pdf"
        return httpx.Response(
            200,
            json={
                "text": "Extracted text.",
                "format": "pdf",
                "filename": "doc-safe.pdf",
            },
        )

    converter = FolioEnrichConverter(
        endpoint="http://127.0.0.1:8731",
        image_ref=IMAGE,
        source_commit=FOLIO_ENRICH_COMMIT,
        runtime_mode=RuntimeMode.LOCAL,
        transport=httpx.MockTransport(handler),
    )

    assert converter.convert(b"%PDF-1.7", "doc-safe.pdf") == "Extracted text.\n"
    assert [str(request.url) for request in seen] == [
        "http://127.0.0.1:8731/enrich/extract"
    ]


@pytest.mark.parametrize("filename", ["../hostile.pdf", "a\\b.pdf", "bad\nname.pdf"])
def test_folio_client_rejects_traversal_and_control_filename_metadata(
    filename: str,
) -> None:
    converter = FolioEnrichConverter(
        endpoint="http://127.0.0.1:8731",
        image_ref=IMAGE,
        source_commit=FOLIO_ENRICH_COMMIT,
        runtime_mode=RuntimeMode.LOCAL,
    )

    with pytest.raises(ConversionError, match="filename"):
        converter.convert(b"%PDF-1.7", filename)


@pytest.mark.parametrize(
    ("mode", "endpoint"),
    [
        (RuntimeMode.HOSTED, "http://127.0.0.1:8731"),
        (RuntimeMode.HOSTED, "http://folio-enrich:8731/extra"),
        (RuntimeMode.LOCAL, "http://example.com:8731"),
        (RuntimeMode.LOCAL, "https://127.0.0.1:8731"),
    ],
)
def test_folio_client_rejects_nonfixed_endpoints(mode: RuntimeMode, endpoint: str) -> None:
    with pytest.raises(ConversionError, match="endpoint"):
        FolioEnrichConverter(
            endpoint=endpoint,
            image_ref=IMAGE,
            source_commit=FOLIO_ENRICH_COMMIT,
            runtime_mode=mode,
        )


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(302, headers={"location": "http://example.com"}), "HTTP 302"),
        (
            httpx.Response(
                200,
                json={"text": "text", "format": "pdf", "filename": "other.pdf"},
            ),
            "metadata",
        ),
    ],
    ids=["redirect", "metadata-drift"],
)
def test_folio_client_rejects_redirects_and_response_metadata_drift(
    response: httpx.Response,
    message: str,
) -> None:
    converter = FolioEnrichConverter(
        endpoint="http://127.0.0.1:8731",
        image_ref=IMAGE,
        source_commit=FOLIO_ENRICH_COMMIT,
        runtime_mode=RuntimeMode.LOCAL,
        transport=httpx.MockTransport(lambda _request: response),
    )

    with pytest.raises(ConversionError, match=message):
        converter.convert(b"%PDF-1.7", "doc-safe.pdf")


@pytest.mark.parametrize(
    "image_ref",
    [
        "ghcr.io/alea-institute/folio-enrich:latest",
        "ghcr.io/alea-institute/folio-enrich@sha256:short",
        "https://ghcr.io/alea-institute/folio-enrich@sha256:" + "a" * 64,
        "127.0.0.1:0/folio-enrich@sha256:" + "a" * 64,
        "127.0.0.1:65536/folio-enrich@sha256:" + "a" * 64,
        "127.0.0.1:http/folio-enrich@sha256:" + "a" * 64,
        "folio-enrich:5000@sha256:" + "a" * 64,
        "127.0.0.1:5000@sha256:" + "a" * 64,
        "registry.example/owner/../folio-enrich@sha256:" + "a" * 64,
        "registry.example/owner//folio-enrich@sha256:" + "a" * 64,
        "registry.example/folio-enrich@SHA256:" + "a" * 64,
        "registry.example/folio-enrich@sha256:" + "A" * 64,
        "registry.example/folio-enrich@@sha256:" + "a" * 64,
        "registry_bad.example:5000/folio-enrich@sha256:" + "a" * 64,
        "registry..example:5000/folio-enrich@sha256:" + "a" * 64,
        "registry_bad.example/folio-enrich@sha256:" + "a" * 64,
        "registry..example/folio-enrich@sha256:" + "a" * 64,
        "-registry.example:5000/folio-enrich@sha256:" + "a" * 64,
        "registry-.example:5000/folio-enrich@sha256:" + "a" * 64,
        "registry.example-:5000/folio-enrich@sha256:" + "a" * 64,
        "registry.example:5000/owner..name/folio-enrich@sha256:" + "a" * 64,
        "registry.example:5000/owner___name/folio-enrich@sha256:" + "a" * 64,
        "registry.example:5000/owner._name/folio-enrich@sha256:" + "a" * 64,
    ],
)
def test_folio_client_requires_digest_pinned_image(image_ref: str) -> None:
    with pytest.raises(ConversionError, match="digest"):
        FolioEnrichConverter(
            endpoint="http://127.0.0.1:8731",
            image_ref=image_ref,
            source_commit=FOLIO_ENRICH_COMMIT,
            runtime_mode=RuntimeMode.LOCAL,
        )


@pytest.mark.parametrize(
    ("registry", "port"),
    [
        ("127.0.0.1", 1),
        ("localhost", 5000),
        ("registry-1.example.com", 65535),
    ],
)
def test_folio_client_accepts_digest_pinned_image_from_registry_with_port(
    registry: str,
    port: int,
) -> None:
    image_ref = f"{registry}:{port}/folio-enrich@sha256:" + "a" * 64

    converter = FolioEnrichConverter(
        endpoint="http://127.0.0.1:8731",
        image_ref=image_ref,
        source_commit=FOLIO_ENRICH_COMMIT,
        runtime_mode=RuntimeMode.LOCAL,
    )

    assert converter.image_ref == image_ref


def test_conversion_promotes_text_and_persists_exact_receipt(tmp_path: Path) -> None:
    vault, doc_id = _reviewed_pdf(tmp_path)
    converter = FakeConverter()

    doc, receipt = convert_corpus_document(
        vault,
        doc_id,
        converter=converter,
        converted_at=NOW,
        actor="local-attorney",
    )

    assert converter.calls == 1
    assert converter.last_filename == f"{doc_id}.pdf"
    assert doc.run_visible is True
    assert doc.triage_issue is None
    assert receipt.source_matter_id == make_matter().matter_id
    assert receipt.doc_id == doc.doc_id
    assert receipt.input_format == "pdf"
    assert receipt.converter_image == IMAGE
    assert receipt.converter_commit == FOLIO_ENRICH_COMMIT
    assert receipt.output_sha256
    assert (vault / doc.normalized_path).read_text(encoding="utf-8") == "Converted evidence.\n"
    assert ingest_actions(vault) == []


@pytest.mark.parametrize("kind", ["needs_ocr", "password_protected", "corrupt"])
def test_unsafe_conversion_classes_remain_manual(tmp_path: Path, kind: str) -> None:
    vault = _vault(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    payload = {
        "needs_ocr": b"%PDF-1.7\nstream\n\x89PNG",
        "password_protected": b"%PDF-1.7\n/Encrypt 2 0 R",
        "corrupt": b"not a PDF",
    }[kind]
    (source / "blocked.pdf").write_bytes(payload)
    doc = ingest_folder(vault, source, now=NOW).entries[0].doc

    with pytest.raises(ConversionError, match="manual protected action"):
        convert_corpus_document(
            vault,
            str(doc.doc_id),
            converter=FakeConverter(),
            converted_at=NOW,
            actor="local-attorney",
        )


@pytest.mark.parametrize(
    "text",
    ["", "contains\x00nul", "x" * (8 * 1024 * 1024 + 1)],
    ids=["empty", "nul", "too-large"],
)
def test_invalid_converter_output_never_changes_manifest(tmp_path: Path, text: str) -> None:
    vault, doc_id = _reviewed_pdf(tmp_path)

    with pytest.raises(ConversionError, match="output"):
        convert_corpus_document(
            vault,
            doc_id,
            converter=FakeConverter(text),
            converted_at=NOW,
            actor="local-attorney",
        )

    doc = Manifest.load(vault).get(doc_id)
    assert doc is not None
    assert doc.ingest_status == "needs_conversion"
    assert doc.normalized_path is None


def test_newline_normalization_is_included_in_the_output_limit() -> None:
    with pytest.raises(ConversionError, match="output"):
        validate_converter_output("x" * MAX_CONVERTER_OUTPUT_BYTES)

    accepted = validate_converter_output("x" * (MAX_CONVERTER_OUTPUT_BYTES - 1))
    assert len(accepted.encode("utf-8")) == MAX_CONVERTER_OUTPUT_BYTES


def test_already_normalized_document_requires_an_existing_conversion_receipt(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "mail.eml").write_text("Subject: Test\n\nAlready normalized.\n")
    doc = ingest_folder(vault, source, now=NOW).entries[0].doc
    converter = FakeConverter()

    with pytest.raises(ConversionError, match="without a conversion receipt"):
        convert_corpus_document(
            vault,
            str(doc.doc_id),
            converter=converter,
            converted_at=NOW,
            actor="local-attorney",
        )

    assert converter.calls == 0
    assert Manifest.load(vault).get(str(doc.doc_id)) == doc


def test_same_bytes_under_a_different_parser_format_get_a_distinct_receipt(
    tmp_path: Path,
) -> None:
    vault, doc_id = _reviewed_pdf(tmp_path)
    first_converter = FakeConverter("PDF extraction.\n")
    _doc, first_receipt = convert_corpus_document(
        vault,
        doc_id,
        converter=first_converter,
        converted_at=NOW,
        actor="local-attorney",
    )
    original = next((vault / "corpus" / "originals").glob("*.pdf"))
    second_source = tmp_path / "second-source"
    second_source.mkdir()
    (second_source / "same-bytes.rtf").write_bytes(original.read_bytes())
    ingest_folder(vault, second_source, now=NOW)
    second_converter = FakeConverter("RTF extraction.\n")

    _doc, second_receipt = convert_corpus_document(
        vault,
        doc_id,
        converter=second_converter,
        converted_at=NOW,
        actor="local-attorney",
    )

    assert first_receipt.input_format == "pdf"
    assert second_receipt.input_format == "rtf"
    assert first_receipt.conversion_id != second_receipt.conversion_id
    assert second_converter.calls == 1


def test_symlinked_original_fails_closed_without_converter_call(tmp_path: Path) -> None:
    vault, doc_id = _reviewed_pdf(tmp_path)
    original = next((vault / "corpus" / "originals").iterdir())
    replacement = vault / "corpus" / "replacement.pdf"
    replacement.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(replacement)
    converter = FakeConverter()

    with pytest.raises(ConversionError, match="symlink"):
        convert_corpus_document(
            vault,
            doc_id,
            converter=converter,
            converted_at=NOW,
            actor="local-attorney",
        )

    assert converter.calls == 0


def test_noncanonical_doc_id_fails_before_touching_conversion_paths(tmp_path: Path) -> None:
    vault = _vault(tmp_path)

    with pytest.raises(ConversionError, match="canonical"):
        convert_corpus_document(
            vault,
            "../escape",
            converter=FakeConverter(),
            converted_at=NOW,
            actor="local-attorney",
        )


def test_receipt_recovers_crash_after_output_without_recalling_converter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mootloop.conversion as conversion

    vault, doc_id = _reviewed_pdf(tmp_path)
    converter = FakeConverter()
    original_promote = conversion.promote_converted_document
    monkeypatch.setattr(
        conversion,
        "promote_converted_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("crash")),
    )
    with pytest.raises(RuntimeError, match="crash"):
        convert_corpus_document(
            vault,
            doc_id,
            converter=converter,
            converted_at=NOW,
            actor="local-attorney",
        )
    assert converter.calls == 1
    monkeypatch.setattr(conversion, "promote_converted_document", original_promote)

    class NoRecall(FakeConverter):
        def convert(self, data: bytes, filename: str) -> str:
            raise AssertionError("durable receipt recovery must not reconvert")

    doc, _receipt = convert_corpus_document(
        vault,
        doc_id,
        converter=NoRecall(),
        converted_at=NOW,
        actor="local-attorney",
    )
    assert doc.run_visible is True


def test_tampered_receipt_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mootloop.conversion as conversion

    vault, doc_id = _reviewed_pdf(tmp_path)
    original_promote = conversion.promote_converted_document
    monkeypatch.setattr(
        conversion,
        "promote_converted_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("crash")),
    )
    with pytest.raises(RuntimeError):
        convert_corpus_document(
            vault,
            doc_id,
            converter=FakeConverter(),
            converted_at=NOW,
            actor="local-attorney",
        )
    monkeypatch.setattr(conversion, "promote_converted_document", original_promote)
    [receipt_path] = (vault / "corpus" / "conversions").glob("*.json")
    receipt_path.write_text(
        receipt_path.read_text().replace('"output_sha256": "', '"output_sha256": "0')
    )

    with pytest.raises(ConversionError, match="receipt"):
        convert_corpus_document(
            vault,
            doc_id,
            converter=FakeConverter(),
            converted_at=NOW,
            actor="local-attorney",
        )


def test_corpus_convert_cli_derives_trusted_actor_and_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, doc_id = _reviewed_pdf(tmp_path)
    converter = FakeConverter()

    class LocalUser:
        pw_name = "trusted-local-user"

    seen_modes: list[RuntimeMode] = []

    def from_env(mode: RuntimeMode) -> FakeConverter:
        seen_modes.append(mode)
        return converter

    monkeypatch.setenv("MOOTLOOP_RUNTIME_MODE", "local")
    monkeypatch.setattr("mootloop.cli.FolioEnrichConverter.from_env", from_env)
    monkeypatch.setattr("mootloop.cli.pwd.getpwuid", lambda _uid: LocalUser())

    result = runner.invoke(app, ["corpus", "convert", str(vault), doc_id])

    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt["actor"] == "trusted-local-user"
    assert seen_modes == [RuntimeMode.LOCAL]
