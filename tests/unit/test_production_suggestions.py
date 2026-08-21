"""U-08 RFP production suggestions remain review-only and privilege-safe."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mootloop.cli import app
from mootloop.discovery_parser import save_requests
from mootloop.errors import ProductionSuggestionError
from mootloop.models.common import DocId, RequestId
from mootloop.models.corpus import CorpusDoc, DocRole, Manifest
from mootloop.models.requests import RequestItem, RequestSet, RequestType
from mootloop.orchestrator import start_run
from mootloop.production_suggestions import (
    ProductionSuggestionStore,
    build_production_suggestions,
    review_production_suggestion,
)
from mootloop.vault import init_vault
from tests.conftest import make_matter

NOW = "2026-08-21T20:00:00+00:00"
runner = CliRunner()


def _doc(
    vault: Path,
    suffix: str,
    text: str,
    *,
    privileged: bool | None,
    role: DocRole | None = DocRole.CLIENT_DOC,
) -> CorpusDoc:
    doc_id = DocId(f"doc-{suffix:0<16}"[:20])
    relative = f"corpus/normalized/{doc_id}.md"
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return CorpusDoc(
        doc_id=doc_id,
        original_name=f"{suffix}.md",
        media_type="text/markdown",
        role=role,
        privileged=privileged,
        ingest_status="ok",
        normalized_path=relative,
        ingested_at=NOW,
    )


def _vault(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
    save_requests(
        vault,
        RequestSet(
            request_type=RequestType.RFP,
            set_number=1,
            title="Requests for Production",
            items=[
                RequestItem(
                    request_id=RequestId("RFP-1"),
                    set_number=1,
                    number=1,
                    text="Produce the signed fertilizer service contract and invoices.",
                    source_doc=DocId("doc-servedrfp00001"),
                )
            ],
        ),
    )
    Manifest(
        docs=[
            _doc(
                vault,
                "relevant",
                "Signed fertilizer service contract and monthly invoices.",
                privileged=False,
            ),
            _doc(
                vault,
                "irrelevant",
                "Photographs of the north fence and survey markers.",
                privileged=False,
            ),
            _doc(
                vault,
                "privileged",
                "Attorney analysis of the fertilizer contract.",
                privileged=True,
            ),
            _doc(
                vault,
                "untriaged",
                "Potential fertilizer correspondence.",
                privileged=None,
                role=None,
            ),
        ]
    ).save(vault)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="production-review")
    return vault, run_id


def test_rfp_ranking_excludes_privileged_and_untriaged_documents(tmp_path: Path) -> None:
    vault, run_id = _vault(tmp_path)

    result = build_production_suggestions(vault, run_id, NOW)

    assert [(item.original_name, item.classification) for item in result.suggestions] == [
        ("relevant.md", "responsive"),
        ("irrelevant.md", "non_responsive"),
    ]
    assert result.suggestions[0].score > result.suggestions[1].score
    assert all(item.review_status == "needs_review" for item in result.suggestions)
    assert {(item.original_name, item.reason) for item in result.exclusions} == {
        ("privileged.md", "privileged"),
        ("untriaged.md", "untriaged"),
    }
    assert all(item.request_sha256 and item.document_sha256 for item in result.suggestions)


def test_classification_review_never_becomes_a_production_act(tmp_path: Path) -> None:
    vault, run_id = _vault(tmp_path)
    result = build_production_suggestions(vault, run_id, NOW)
    suggestion = next(item for item in result.suggestions if item.classification == "responsive")

    accepted = review_production_suggestion(
        vault,
        run_id,
        suggestion.suggestion_id,
        action="accept",
        actor="attorney@example.com",
        channel="api",
        recorded_at=NOW,
    )
    assert accepted.review_status == "accepted"
    assert accepted.production_disposition is None

    reviewed = review_production_suggestion(
        vault,
        run_id,
        suggestion.suggestion_id,
        action="production_review",
        production_disposition="produce",
        actor="attorney@example.com",
        channel="api",
        recorded_at=NOW,
    )
    assert reviewed.review_status == "accepted"
    assert reviewed.production_disposition == "produce"
    assert ProductionSuggestionStore(vault, run_id).get(suggestion.suggestion_id) == reviewed


def test_review_requires_human_provenance_and_explicit_production_choice(
    tmp_path: Path,
) -> None:
    vault, run_id = _vault(tmp_path)
    suggestion = build_production_suggestions(vault, run_id, NOW).suggestions[0]

    with pytest.raises(ProductionSuggestionError, match="actor"):
        review_production_suggestion(
            vault,
            run_id,
            suggestion.suggestion_id,
            action="accept",
            actor="",
            channel="api",
            recorded_at=NOW,
        )
    with pytest.raises(ProductionSuggestionError, match="disposition"):
        review_production_suggestion(
            vault,
            run_id,
            suggestion.suggestion_id,
            action="production_review",
            actor="attorney@example.com",
            channel="api",
            recorded_at=NOW,
        )


def test_generation_and_exact_review_retry_are_idempotent(tmp_path: Path) -> None:
    vault, run_id = _vault(tmp_path)
    first = build_production_suggestions(vault, run_id, NOW)
    second = build_production_suggestions(vault, run_id, "2026-08-21T20:05:00+00:00")
    assert second == first

    suggestion = first.suggestions[0]
    first_review = review_production_suggestion(
        vault,
        run_id,
        suggestion.suggestion_id,
        action="reject",
        actor="attorney@example.com",
        channel="cli",
        recorded_at=NOW,
        reason="Wrong document family.",
    )
    second_review = review_production_suggestion(
        vault,
        run_id,
        suggestion.suggestion_id,
        action="reject",
        actor="attorney@example.com",
        channel="cli",
        recorded_at=NOW,
        reason="Wrong document family.",
    )
    assert second_review == first_review
    assert len(ProductionSuggestionStore(vault, run_id).review_events()) == 1


def test_cli_generate_list_and_review_use_the_shared_durable_service(tmp_path: Path) -> None:
    vault, run_id = _vault(tmp_path)

    generated = runner.invoke(app, ["production", "generate", str(vault), "--run", run_id])
    assert generated.exit_code == 0, generated.output
    assert "generated 2 review-only suggestion(s)" in generated.output

    listed = runner.invoke(
        app, ["production", "list", str(vault), "--run", run_id, "--json"]
    )
    items = json.loads(listed.output)
    suggestion_id = items[0]["suggestion_id"]

    reviewed = runner.invoke(
        app,
        [
            "production",
            "review",
            str(vault),
            suggestion_id,
            "--run",
            run_id,
            "--action",
            "accept",
        ],
    )
    assert reviewed.exit_code == 0, reviewed.output
    assert json.loads(reviewed.output)["review_status"] == "accepted"
