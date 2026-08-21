from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from mootloop.context import load_run_context, load_run_corpus
from mootloop.context_assembly import assemble_context, items_for_turn
from mootloop.engine.launch import launch_run
from mootloop.errors import CitationError
from mootloop.judge_profiles import (
    JudgeProfileStore,
    build_assigned_judge_profile,
    calibrate_judge_profile,
    fetch_judge_opinions,
    profile_context_contribution,
)
from mootloop.models.citations import OpinionAuthorityStoreRecord
from mootloop.models.common import CitationId
from mootloop.models.matter import Caption, Jurisdiction
from mootloop.models.run import PersonaName
from mootloop.vault import init_vault
from tests.conftest import make_matter

NOW = "2026-08-21T18:00:00+00:00"


def _snapshot(cluster_id: int, disposition: str) -> OpinionAuthorityStoreRecord:
    text = f"After review, the motion to compel is {disposition}."
    return OpinionAuthorityStoreRecord(
        citation_id=CitationId(f"cit-judge-{cluster_id}"),
        cluster_id=cluster_id,
        opinion_ids=[cluster_id + 1000],
        source_url=f"https://www.courtlistener.com/opinion/{cluster_id}/",
        fetched_at=NOW,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        text=text,
    )


def test_profile_is_calibrated_only_after_heldout_error_is_measured() -> None:
    snapshots = [
        _snapshot(i, "denied" if i in {2, 3} else "granted") for i in range(1, 11)
    ]

    profile = calibrate_judge_profile(
        judge_name="Judge Ada Example",
        jurisdiction_state="MN",
        court_name="Minnesota District Court",
        snapshots=snapshots,
        built_at=NOW,
    )

    assert profile.calibration.calibrated is True
    assert profile.calibration.training_examples == 8
    assert profile.calibration.holdout_examples == 2
    assert profile.calibration.error_rate == 0.0
    assert profile.directional_only is True
    assert len(profile.prompt_text) <= 4000
    assert all(ref.content_sha256 for ref in profile.opinions)


def test_measured_high_error_remains_explicitly_uncalibrated() -> None:
    snapshots = [
        _snapshot(i, "denied" if i in {5, 10} else "granted") for i in range(1, 11)
    ]

    profile = calibrate_judge_profile(
        judge_name="Judge Ada Example",
        jurisdiction_state="MN",
        court_name="Minnesota District Court",
        snapshots=snapshots,
        built_at=NOW,
    )

    assert profile.calibration.error_rate == 1.0
    assert profile.calibration.calibrated is False
    assert "not calibrated" in profile.prompt_text.lower()


def test_non_us_jurisdiction_warns_and_opens_human_research_without_network(
    tmp_path: Path,
) -> None:
    matter = make_matter().model_copy(
        update={
            "caption": Caption(
                court_name="Ontario Superior Court",
                case_number="CV-1",
                county="Toronto",
                judge_name="Justice Example",
            ),
            "jurisdiction": Jurisdiction(state="Ontario", forum="state"),
        }
    )

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request to {request.url}")

    result = build_assigned_judge_profile(
        tmp_path,
        matter,
        NOW,
        transport=httpx.MockTransport(no_network),
    )

    assert result.profile is None
    assert "non-US" in result.warning
    assert result.research_request_id


def test_calibrated_profile_contribution_is_judge_scoped_and_latest_is_verified(
    tmp_path: Path,
) -> None:
    profile = calibrate_judge_profile(
        judge_name="Judge Ada Example",
        jurisdiction_state="MN",
        court_name="Minnesota District Court",
        snapshots=[_snapshot(i, "granted") for i in range(1, 11)],
        built_at=NOW,
    )
    store = JudgeProfileStore(tmp_path)
    store.publish(profile)

    loaded = store.latest()
    contribution = profile_context_contribution(loaded, "matter-1")

    assert loaded == profile
    assert contribution.persona_scope == (PersonaName.JUDGE,)
    assert contribution.approval_state == "approved"
    assert contribution.text == profile.prompt_text


def test_republishing_identical_evidence_preserves_the_content_addressed_record(
    tmp_path: Path,
) -> None:
    snapshots = [_snapshot(i, "granted") for i in range(1, 11)]
    first = calibrate_judge_profile(
        judge_name="Judge Ada Example",
        jurisdiction_state="MN",
        court_name="Minnesota District Court",
        snapshots=snapshots,
        built_at=NOW,
    )
    rebuilt = calibrate_judge_profile(
        judge_name="Judge Ada Example",
        jurisdiction_state="MN",
        court_name="Minnesota District Court",
        snapshots=snapshots,
        built_at="2026-08-22T18:00:00+00:00",
    )

    store = JudgeProfileStore(tmp_path)
    path = store.publish(first)
    original = path.read_bytes()
    store.publish(rebuilt)

    assert rebuilt.profile_id == first.profile_id
    assert path.read_bytes() == original
    assert store.latest() == first


def test_current_profile_must_match_its_write_once_archive(tmp_path: Path) -> None:
    profile = calibrate_judge_profile(
        judge_name="Judge Ada Example",
        jurisdiction_state="MN",
        court_name="Minnesota District Court",
        snapshots=[_snapshot(i, "granted") for i in range(1, 11)],
        built_at=NOW,
    )
    store = JudgeProfileStore(tmp_path)
    store.publish(profile)
    current = tmp_path / "law" / "judge-profiles" / "current.json"
    current.write_text(
        profile.model_copy(update={"built_at": "2026-08-22T18:00:00+00:00"})
        .model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(CitationError, match="does not match its archive"):
        store.latest()


def test_courtlistener_search_uses_fixed_fielded_query_and_exact_cluster_ids() -> None:
    seen_query = ""
    heartbeats = 0

    def heartbeat() -> None:
        nonlocal heartbeats
        heartbeats += 1

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        if request.url.path == "/api/rest/v4/search/":
            seen_query = request.url.params["q"]
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "cluster_id": 101,
                            "absolute_url": "/opinion/101/example-v-example/",
                            "judge": "Judge Ada Example",
                            "panel_names": [],
                        }
                    ]
                },
            )
        if request.url.path == "/api/rest/v4/clusters/101/":
            return httpx.Response(
                200,
                json={"sub_opinions": ["/api/rest/v4/opinions/1101/"]},
            )
        assert request.url.path == "/api/rest/v4/opinions/1101/"
        return httpx.Response(
            200,
            json={"plain_text": "The motion to compel is granted."},
        )

    snapshots, warning = fetch_judge_opinions(
        "Judge Ada Example",
        NOW,
        transport=httpx.MockTransport(handler),
        heartbeat=heartbeat,
    )

    assert warning == ""
    assert seen_query == 'judge:"Judge Ada Example" AND "motion to compel"'
    assert [snapshot.cluster_id for snapshot in snapshots] == [101]
    assert heartbeats >= 4


def test_search_result_judge_match_normalizes_titles_but_rejects_similar_names() -> None:
    cluster_calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rest/v4/search/":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"cluster_id": 100, "judge": "Ada Example Jr."},
                        {"cluster_id": 101, "judge": "Hon. Ada Example"},
                    ]
                },
            )
        if request.url.path == "/api/rest/v4/clusters/101/":
            cluster_calls.append(101)
            return httpx.Response(
                200,
                json={"sub_opinions": ["/api/rest/v4/opinions/1101/"]},
            )
        if request.url.path == "/api/rest/v4/opinions/1101/":
            return httpx.Response(200, json={"plain_text": "Motion to compel granted."})
        raise AssertionError(f"unexpected request to {request.url}")

    snapshots, warning = fetch_judge_opinions(
        "Judge Ada Example",
        NOW,
        transport=httpx.MockTransport(handler),
    )

    assert warning == ""
    assert cluster_calls == [101]
    assert [snapshot.cluster_id for snapshot in snapshots] == [101]


def test_next_launch_snapshots_only_matching_calibrated_profile_for_judge_data(
    tmp_path: Path,
) -> None:
    base = make_matter()
    matter = base.model_copy(
        update={
            "caption": base.caption.model_copy(
                update={"judge_name": "  Judge   Ada   Example  "}
            ),
            "jurisdiction": base.jurisdiction.model_copy(update={"state": "  MN  "}),
        }
    )
    vault = tmp_path / "vault"
    init_vault(vault, matter, registry_path=tmp_path / "canaries.json")
    profile = calibrate_judge_profile(
        judge_name="Judge Ada Example",
        jurisdiction_state="MN",
        court_name=matter.caption.court_name,
        snapshots=[_snapshot(i, "granted") for i in range(1, 11)],
        built_at=NOW,
    )
    JudgeProfileStore(vault).publish(profile)

    run_id = launch_run(
        vault,
        "discovery-responses",
        NOW,
        run_id="judge-profile-next-run",
        idempotent=False,
    )
    context = load_run_context(vault, run_id)
    assembled = assemble_context(context.manifest, load_run_corpus(vault, context))

    assert [item.contribution_id for item in context.manifest.context_contributions] == [
        profile.profile_id
    ]
    judge_items = items_for_turn(
        assembled,
        task="discovery-responses",
        persona=PersonaName.JUDGE,
    )
    assert [item.text for item in judge_items] == [profile.prompt_text]
    assert items_for_turn(
        assembled,
        task="discovery-responses",
        persona=PersonaName.JUROR,
    ) == ()
