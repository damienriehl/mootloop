"""Read-only API contract over the session-scoped baked demo vault: shapes, 404s,
and the fail-closed deliverable-name handling (path traversal never escapes)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from mootloop.web.app import PUBLIC_ENV, app
from mootloop.web.bake import DEMO_RUN_ID
from mootloop.web.legacy_prepare import project_legacy
from tests.unit.test_demo_publication import prepared_release


@pytest.fixture(scope="module")
def legacy_public(demo_vault, tmp_path_factory):
    root = tmp_path_factory.mktemp("legacy-public")
    release = prepared_release(root / "releases/release-1")
    digest = project_legacy(demo_vault, release / "legacy.json")
    catalog = json.loads((release / "catalog.json").read_text())
    catalog["legacy_sha256"] = digest
    (release / "catalog.json").write_text(json.dumps(catalog))
    (root / "CURRENT").write_text("release-1\n")
    return root


@pytest.fixture
def client(legacy_public, monkeypatch):
    monkeypatch.setenv(PUBLIC_ENV, str(legacy_public))
    return TestClient(app)


def test_health_needs_no_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PUBLIC_ENV, "/nonexistent")
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok" and "version" in body


def test_homepage_survives_missing_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PUBLIC_ENV, "/nonexistent")
    client = TestClient(app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'href="/demos/"' in response.text
    for collection in ("synthetic", "public-record", "business"):
        assert f'href="/demos/?collection={collection}"' in response.text
    assert 'href="#local"' in response.text
    assert "https://github.com/damienriehl/mootloop" in response.text
    assert "<script" not in response.text
    assert "<form" not in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/demos").status_code == 503
    assert client.get("/ready").status_code == 503


def test_matter_is_sanitized(client: TestClient) -> None:
    body = client.get("/api/matter").json()
    assert body["matter_id"] == "northfield-widgets-v-granite-supply"
    assert body["synthetic"] is True
    assert set(body["attorney"]) == {"name", "firm"}  # never address/email/phone/bar
    assert {p["role"] for p in body["parties"]} == {"plaintiff", "defendant"}


def test_run_summary_shape(client: TestClient) -> None:
    body = client.get("/api/run").json()
    assert body["run_id"] == DEMO_RUN_ID
    assert body["status"] == "finished"
    assert body["requests"] == 18
    assert body["export_ready"] is True
    assert body["rubric_version"] == "discovery-responses-v1.0"
    assert body["spend_usd"] > 0
    assert "associate_draft" in body["stages"]
    assert body["persona_turns"]["judge"] > 0


def test_requests_carry_gate_states(client: TestClient) -> None:
    body = client.get("/api/requests").json()
    assert len(body) == 18
    assert body[0]["request_id"] == "ROG-1"  # family-ordered: ROG, RFP, RFA
    assert body[-1]["request_id"] == "RFA-5"
    for row in body:
        assert row["gates"]["attestation"] == "pass"
        assert row["gates"]["rubric"] == "pass"
        assert row["turns"] > 0
    restructured = {r["request_id"] for r in body if r["restructured"]}
    assert restructured == {"ROG-1", "RFP-2", "RFA-3"}


def test_request_turns_and_panel(client: TestClient) -> None:
    turns = client.get("/api/requests/ROG-1/turns").json()
    assert [t["stage"] for t in turns][:2] == ["associate_draft", "partner_loop"]
    assert turns[-1]["stage"] == "restructure"
    for turn in turns:
        assert {"turn_id", "persona", "stage", "attempt", "output"} <= set(turn)

    panel = client.get("/api/requests/ROG-1/panel").json()
    assert panel and panel[0]["total_votes"] == 3
    assert panel[0]["survival_rate"] < 0.5  # scripted weak objection

    response = client.get("/api/requests/RFA-3/response").json()
    assert response["rfa_disposition"] == "deny"


def test_decisions_and_gates(client: TestClient) -> None:
    decisions = client.get("/api/decisions").json()
    assert decisions and all(d["status"] != "open" for d in decisions)
    ledger = client.get("/api/gates").json()
    assert ledger["export_ready"] is True and ledger["run_id"] == DEMO_RUN_ID


def test_deliverables_list_and_fetch(client: TestClient) -> None:
    items = client.get("/api/deliverables").json()
    names = {i["name"] for i in items}
    assert {"master.md", "audit-log.json"} <= names
    assert any(n.startswith("sets/") for n in names)

    res = client.get("/api/deliverables/master.md")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    assert "RESPONSES AND OBJECTIONS" in res.text

    res = client.get("/api/deliverables/sets/rog-set1.md")
    assert res.status_code == 200


def test_unknown_request_and_deliverable_404(client: TestClient) -> None:
    assert client.get("/api/requests/ROG-99/turns").status_code == 404
    assert client.get("/api/requests/ROG-99/panel").status_code == 404
    assert client.get("/api/deliverables/nope.md").status_code == 404


@pytest.mark.parametrize(
    "name",
    [
        "../../matter.yaml",
        "..%2F..%2Fmatter.yaml",
        "sets/../../../matter.yaml",
        "%2e%2e/%2e%2e/matter.yaml",
        "/etc/passwd",
        "a\\b.md",
    ],
)
def test_deliverable_path_traversal_fails_closed(client: TestClient, name: str) -> None:
    res = client.get(f"/api/deliverables/{name}")
    assert res.status_code in (400, 404)
    # And under no circumstances did vault-internal non-deliverable content leak.
    assert "matter_id" not in res.text
    assert "root:" not in res.text
