from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mootloop.models.demo import DemoCatalog
from mootloop.web.app import PUBLIC_ENV, app
from mootloop.web.catalog import PublicCatalog
from mootloop.web.publish import publish_release
from tests.unit.test_demo_publication import prepared_release


@pytest.fixture
def public_client(tmp_path, monkeypatch):
    source = prepared_release(tmp_path / "source")
    manifest = DemoCatalog.model_validate_json((source / "catalog.json").read_bytes())
    target = tmp_path / "public"
    publish_release(source, target, manifest=manifest)
    monkeypatch.setenv(PUBLIC_ENV, str(target))
    return TestClient(app), target


def test_library_and_revision_download(public_client):
    client, target = public_client
    catalog = client.get("/api/demos").json()
    assert len(catalog["entries"]) == 20
    assert client.get("/ready").json()["demos"] == 20
    snapshot = client.get("/api/demos/supplier-discovery/r1").json()
    assert snapshot == client.get("/api/demos/supplier-discovery").json()
    assert snapshot["descriptor"]["demo_id"] == "supplier-discovery"
    download = client.get("/api/demos/supplier-discovery/r1/inputs")
    assert download.content == PublicCatalog(target).bundle_bytes("supplier-discovery", "r1")
    assert "attachment" in download.headers["content-disposition"]
    assert client.get("/api/demos/supplier-discovery/r2").status_code == 404
    assert client.get("/api/demos/unknown").status_code == 404
    assert client.post("/api/demos").status_code == 405


def test_corruption_and_missing_catalog_never_fall_back(public_client, monkeypatch):
    client, target = public_client
    monkeypatch.setenv("MOOTLOOP_DEMO_VAULT", "/private/sentinel")
    path = target / "releases/release-1/supplier-discovery/r1/snapshot.json"
    path.write_text(path.read_text() + " ")
    assert client.get("/api/demos/supplier-discovery/r1").status_code == 503
    assert client.get("/ready").status_code == 503
    assert client.get("/health").status_code == 200
    (target / "CURRENT").unlink()
    assert client.get("/api/demos").status_code == 503


def test_pages_redirect_csp_and_deep_links(public_client):
    client, _ = public_client
    assert client.get("/", follow_redirects=False).headers["location"] == "/demos/"
    for path in ("/demos/", "/demos/epic-apple", "/legacy"):
        response = client.get(path)
        assert response.status_code == 200
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert "object-src 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
    assert "Demo library" in client.get("/demos/").text


def test_projection_does_not_render_source_markup(public_client):
    client, _ = public_client
    code = client.get("/static/library.js").text
    assert "textContent" in code
    assert "innerHTML" not in code
    assert "AbortController" in code
    assert "popstate" in code
    assert "https:" in code
    for path in (
        "/api/demos/..%2Foutside/r1",
        "/api/demos/supplier-discovery/r1/inputs/..%2F..%2Fsecret",
    ):
        assert client.get(path).status_code == 404
