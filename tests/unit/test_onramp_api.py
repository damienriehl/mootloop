"""Write-tier on-ramp + export routes (plan FE-2.5): the freeform TaskSpec lane, the
deliverable listing + signed-link mint (typed 403 when a clean file is not export-ready),
and the AUDIT-FAIL-CLOSED ``/api/download`` contract — the access audit records FIRST and
if that write fails, not a byte streams.

Drives `create_matter_api` through the TestClient with a stubbed Access verifier and an
overridden registry / link signer (the FE-2 unit-1 pattern).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mootloop.errors import AccessAuthError, AuditWriteError
from mootloop.export.link import LinkSigner
from mootloop.models.matter import MatterConfig
from mootloop.registry import MatterRegistry
from mootloop.web.api import create_matter_api
from mootloop.web.api.deps import get_link_signer, get_registry, get_verifier
from mootloop.web.security import AccessPrincipal

_PRINCIPAL = AccessPrincipal(email="attorney@example.com", subject="sub-1", claims={})
_AUTH = {"cf-access-jwt-assertion": "good"}
_SIGNER = LinkSigner("onramp-test-signing-key-0123456789ab")


class _StubVerifier:
    def verify(self, token: str | None) -> AccessPrincipal:
        if token == "good":
            return _PRINCIPAL
        raise AccessAuthError("stub rejects token")


@pytest.fixture
def registry(tmp_path: Path, matter: MatterConfig) -> MatterRegistry:
    reg = MatterRegistry(root=tmp_path / "matters")
    reg.create(matter)
    return reg


@pytest.fixture
def client(registry: MatterRegistry) -> TestClient:
    app = create_matter_api()
    app.dependency_overrides[get_verifier] = _StubVerifier
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_link_signer] = lambda: _SIGNER
    return TestClient(app)


def _csrf(client: TestClient) -> dict[str, str]:
    issued = client.get("/api/csrf", headers=_AUTH)
    return {**_AUTH, "x-csrf-token": issued.json()["csrf_token"]}


def _seed_deliverables(vault: Path, run_id: str, *names: str) -> None:
    base = vault / "deliverables" / run_id
    base.mkdir(parents=True, exist_ok=True)
    for name in names:
        (base / name).write_bytes(b"court-formatted work product")


# --- freeform on-ramp lane ---------------------------------------------------


def test_freeform_resolves_to_runnable_spec(
    client: TestClient, matter: MatterConfig
) -> None:
    headers = _csrf(client)
    resp = client.post(
        f"/api/matters/{matter.matter_id}/tasks/freeform",
        headers=headers,
        json={"intent_text": "answer the discovery served on us"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "task_spec"
    assert body["runnable"] is True
    assert body["task_spec"]["task"] == "discovery-responses"
    assert body["task_spec"]["source_lane"] == "freeform"


def test_freeform_unmapped_intent_is_recorded_not_runnable(
    client: TestClient, matter: MatterConfig
) -> None:
    headers = _csrf(client)
    resp = client.post(
        f"/api/matters/{matter.matter_id}/tasks/freeform",
        headers=headers,
        json={"intent_text": "draft an appellate brief about nothing"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["runnable"] is False
    assert body["task_spec"]["task"] is None


def test_freeform_requires_csrf(client: TestClient, matter: MatterConfig) -> None:
    resp = client.post(
        f"/api/matters/{matter.matter_id}/tasks/freeform",
        headers=_AUTH,
        json={"intent_text": "answer the discovery"},
    )
    assert resp.status_code == 403


def test_task_list_returns_recorded_specs(
    client: TestClient, matter: MatterConfig
) -> None:
    headers = _csrf(client)
    for intent in ("answer the discovery", "draft a nonexistent thing"):
        client.post(
            f"/api/matters/{matter.matter_id}/tasks/freeform",
            headers=headers,
            json={"intent_text": intent},
        )
    listed = client.get(f"/api/matters/{matter.matter_id}/tasks", headers=_AUTH)
    assert listed.status_code == 200
    body = listed.json()
    assert body["kind"] == "task_specs"
    assert [s["task"] for s in body["specs"]] == ["discovery-responses", None]


# --- deliverable listing + signed-link mint (export gate) --------------------


def test_deliverables_list_marks_gating(
    client: TestClient,
    registry: MatterRegistry,
    matter: MatterConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = registry.resolve(matter.matter_id)
    _seed_deliverables(vault, "r1", "responses.docx", "responses.DRAFT.docx", "master.md")
    monkeypatch.setattr("mootloop.gate_ledger.export_ready", lambda v, r: (False, ["attestation"]))

    resp = client.get(f"/api/matters/{matter.matter_id}/runs/r1/deliverables", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["export_ready"] is False
    by_name = {d["name"]: d for d in body["deliverables"]}
    # Clean docx: gated (not downloadable until export-ready). DRAFT + md: always ok.
    assert by_name["responses.docx"]["requires_export_ready"] is True
    assert by_name["responses.docx"]["downloadable"] is False
    assert by_name["responses.DRAFT.docx"]["is_draft"] is True
    assert by_name["responses.DRAFT.docx"]["downloadable"] is True
    assert by_name["master.md"]["downloadable"] is True


def test_mint_clean_file_not_ready_returns_typed_403(
    client: TestClient,
    registry: MatterRegistry,
    matter: MatterConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = registry.resolve(matter.matter_id)
    _seed_deliverables(vault, "r1", "responses.docx")
    monkeypatch.setattr(
        "mootloop.gate_ledger.export_ready", lambda v, r: (False, ["attestation", "citations"])
    )
    headers = _csrf(client)
    resp = client.post(
        f"/api/matters/{matter.matter_id}/runs/r1/deliverables/responses.docx/link",
        headers=headers,
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"] == "export_not_ready"
    assert body["blockers"] == ["attestation", "citations"]
    # Never optimistic: no token / url in a blocked response.
    assert "token" not in body and "url" not in body


def test_mint_draft_file_is_allowed_when_not_ready(
    client: TestClient,
    registry: MatterRegistry,
    matter: MatterConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = registry.resolve(matter.matter_id)
    _seed_deliverables(vault, "r1", "responses.DRAFT.docx")
    monkeypatch.setattr("mootloop.gate_ledger.export_ready", lambda v, r: (False, ["attestation"]))
    headers = _csrf(client)
    resp = client.post(
        f"/api/matters/{matter.matter_id}/runs/r1/deliverables/responses.DRAFT.docx/link",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "signed_link"
    assert body["is_draft"] is True
    assert body["token"] and body["url"].startswith("/api/download?token=")


def test_mint_clean_file_when_ready_succeeds(
    client: TestClient,
    registry: MatterRegistry,
    matter: MatterConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = registry.resolve(matter.matter_id)
    _seed_deliverables(vault, "r1", "responses.docx")
    monkeypatch.setattr("mootloop.gate_ledger.export_ready", lambda v, r: (True, []))
    headers = _csrf(client)
    resp = client.post(
        f"/api/matters/{matter.matter_id}/runs/r1/deliverables/responses.docx/link",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["is_draft"] is False


# --- /api/download: audit-append FIRST, fail closed --------------------------


def _mint_token(client: TestClient, matter_id: str, run_id: str, name: str) -> str:
    resp = client.post(
        f"/api/matters/{matter_id}/runs/{run_id}/deliverables/{name}/link",
        headers=_csrf(client),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_download_streams_after_audit_records(
    client: TestClient,
    registry: MatterRegistry,
    matter: MatterConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = registry.resolve(matter.matter_id)
    _seed_deliverables(vault, "r1", "responses.DRAFT.docx")
    monkeypatch.setattr("mootloop.gate_ledger.export_ready", lambda v, r: (True, []))
    token = _mint_token(client, matter.matter_id, "r1", "responses.DRAFT.docx")

    from mootloop.web import audit

    resp = client.get("/api/download", params={"token": token}, headers=_AUTH)
    assert resp.status_code == 200
    assert resp.content == b"court-formatted work product"
    # The access audit recorded the download (one hash-chained entry, verifies intact).
    assert audit.verify_chain(vault) is True
    entries = audit.audit_path(vault).read_text(encoding="utf-8").splitlines()
    assert any('"action":"download"' in e for e in entries)


def test_download_fails_closed_when_audit_write_fails(
    client: TestClient,
    registry: MatterRegistry,
    matter: MatterConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = registry.resolve(matter.matter_id)
    _seed_deliverables(vault, "r1", "responses.DRAFT.docx")
    monkeypatch.setattr("mootloop.gate_ledger.export_ready", lambda v, r: (True, []))
    token = _mint_token(client, matter.matter_id, "r1", "responses.DRAFT.docx")

    def _boom(*args: object, **kwargs: object) -> None:
        raise AuditWriteError("simulated audit-store failure")

    # The download route records the audit FIRST; if that raises, nothing streams.
    monkeypatch.setattr("mootloop.web.audit.record_download_audit", _boom)
    resp = client.get("/api/download", params={"token": token}, headers=_AUTH)
    assert resp.status_code == 500
    assert resp.json()["error"] == "audit_write_failed"
    # Fail closed: not a byte of the file was returned.
    assert b"court-formatted work product" not in resp.content


def test_download_requires_access(
    client: TestClient,
    registry: MatterRegistry,
    matter: MatterConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = registry.resolve(matter.matter_id)
    _seed_deliverables(vault, "r1", "responses.DRAFT.docx")
    monkeypatch.setattr("mootloop.gate_ledger.export_ready", lambda v, r: (True, []))
    token = _mint_token(client, matter.matter_id, "r1", "responses.DRAFT.docx")
    resp = client.get("/api/download", params={"token": token})  # no auth header
    assert resp.status_code == 401


def test_download_rejects_tampered_token(client: TestClient, matter: MatterConfig) -> None:
    resp = client.get("/api/download", params={"token": "garbage.token"}, headers=_AUTH)
    assert resp.status_code == 400
