"""Write-tier API extension (plan FE-2 unit 1): the new single-run read views
(status / gates / decisions / requests), the run lifecycle write wrappers
(start / continue / raise-cap), and the ``mootloop api export-openapi`` CLI.

Drives `create_matter_api` through the FastAPI TestClient with a STUBBED verifier
(via ``dependency_overrides``), plus a CliRunner pass over the OpenAPI export. The
OpenAPI-structure test asserts the schema yields real discriminated unions for the
run/gate/decision status envelopes (plan FD-8).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mootloop import orchestrator
from mootloop.cli import app as cli_app
from mootloop.engine.queue import Queue
from mootloop.errors import AccessAuthError
from mootloop.models.common import DocId
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestItem, RequestSet, RequestType
from mootloop.registry import MatterRegistry
from mootloop.web.api import create_matter_api
from mootloop.web.api.deps import get_queue, get_registry, get_verifier
from mootloop.web.security import AccessPrincipal

_PRINCIPAL = AccessPrincipal(email="attorney@example.com", subject="sub-1", claims={})
_AUTH = {"cf-access-jwt-assertion": "good"}
_NOW = "2026-07-12T00:00:00+00:00"


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
    app.dependency_overrides[get_queue] = lambda: Queue(registry.root)
    return TestClient(app)


def _csrf(client: TestClient) -> dict[str, str]:
    issued = client.get("/api/csrf", headers=_AUTH)
    return {**_AUTH, "x-csrf-token": issued.json()["csrf_token"]}


def _seed_running_run(vault: Path) -> str:
    """Seed a request + fact so the started run stays ``running`` (schedulable)."""
    from mootloop.discovery_parser import save_requests
    from mootloop.facts import FactStore

    save_requests(
        vault,
        RequestSet(
            request_type=RequestType.INTERROGATORY,
            set_number=1,
            title="Interrogatories Set 1",
            items=[
                RequestItem(
                    request_id="ROG-1",  # type: ignore[arg-type]
                    set_number=1,
                    number=1,
                    text="Identify every person with knowledge of the contract.",
                    source_doc=DocId("doc-servedservedserv"),
                )
            ],
        ),
    )
    FactStore(vault).add_fact("The contract price was $148,500.", confidence=1.0)
    return orchestrator.start_run(vault, "discovery-responses", _NOW)


# --- single-run read views require Access -----------------------------------


@pytest.mark.parametrize("suffix", ["", "/gates", "/decisions", "/requests"])
def test_run_read_views_require_access(
    client: TestClient, matter: MatterConfig, suffix: str
) -> None:
    resp = client.get(f"/api/matters/{matter.matter_id}/runs/r1{suffix}")
    assert resp.status_code == 401


def test_run_read_views_return_folded_views(
    client: TestClient, registry: MatterRegistry, matter: MatterConfig
) -> None:
    vault = registry.resolve(matter.matter_id)
    run_id = _seed_running_run(vault)

    status = client.get(f"/api/matters/{matter.matter_id}/runs/{run_id}", headers=_AUTH)
    assert status.status_code == 200
    body = status.json()
    assert body["kind"] == "run_status"
    assert body["run_id"] == run_id
    assert body["status"]  # a RunStatus Literal

    gates = client.get(f"/api/matters/{matter.matter_id}/runs/{run_id}/gates", headers=_AUTH)
    assert gates.status_code == 200
    assert gates.json()["kind"] == "gate_ledger"
    assert "export_ready" in gates.json()

    decisions = client.get(
        f"/api/matters/{matter.matter_id}/runs/{run_id}/decisions", headers=_AUTH
    )
    assert decisions.status_code == 200
    assert decisions.json()["kind"] == "decisions"
    assert isinstance(decisions.json()["decisions"], list)

    requests = client.get(
        f"/api/matters/{matter.matter_id}/runs/{run_id}/requests", headers=_AUTH
    )
    assert requests.status_code == 200
    payload = requests.json()
    assert payload["kind"] == "requests"
    assert [r["request_id"] for r in payload["requests"]] == ["ROG-1"]


# --- run lifecycle write wrappers -------------------------------------------


def test_start_run_wrapper_creates_a_run(
    client: TestClient, registry: MatterRegistry, matter: MatterConfig
) -> None:
    vault = registry.resolve(matter.matter_id)
    from mootloop.discovery_parser import save_requests
    from mootloop.facts import FactStore

    save_requests(
        vault,
        RequestSet(
            request_type=RequestType.INTERROGATORY,
            set_number=1,
            title="Set 1",
            items=[
                RequestItem(
                    request_id="ROG-1",  # type: ignore[arg-type]
                    set_number=1,
                    number=1,
                    text="Identify witnesses.",
                    source_doc=DocId("doc-servedservedserv"),
                )
            ],
        ),
    )
    FactStore(vault).add_fact("A fact.", confidence=1.0)

    headers = _csrf(client)
    resp = client.post(
        f"/api/matters/{matter.matter_id}/runs",
        headers=headers,
        json={"task": "discovery-responses"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "run_status"
    assert body["run_id"]
    # The started run is now discoverable in the run listing.
    listed = client.get(f"/api/matters/{matter.matter_id}/runs", headers=_AUTH)
    assert body["run_id"] in [r["run_id"] for r in listed.json()]


def test_start_run_requires_csrf(client: TestClient, matter: MatterConfig) -> None:
    resp = client.post(
        f"/api/matters/{matter.matter_id}/runs", headers=_AUTH, json={"task": "discovery-responses"}
    )
    assert resp.status_code == 403


def test_raise_cap_wrapper_absolute(
    client: TestClient, registry: MatterRegistry, matter: MatterConfig
) -> None:
    vault = registry.resolve(matter.matter_id)
    run_id = _seed_running_run(vault)
    headers = _csrf(client)
    resp = client.post(
        f"/api/matters/{matter.matter_id}/runs/{run_id}/raise-cap",
        headers=headers,
        json={"to_usd": 25.0},
    )
    assert resp.status_code == 200
    assert resp.json()["kind"] == "cap_raised"
    # The cap now shows on the status view.
    status = client.get(f"/api/matters/{matter.matter_id}/runs/{run_id}", headers=_AUTH)
    assert status.json()["hard_cap_usd"] == 25.0


def test_raise_cap_rejects_both_or_neither(
    client: TestClient, registry: MatterRegistry, matter: MatterConfig
) -> None:
    vault = registry.resolve(matter.matter_id)
    run_id = _seed_running_run(vault)
    headers = _csrf(client)
    both = client.post(
        f"/api/matters/{matter.matter_id}/runs/{run_id}/raise-cap",
        headers=headers,
        json={"to_usd": 25.0, "delta_usd": 5.0},
    )
    assert both.status_code == 422


def test_continue_wrapper_rejects_non_checkpoint_run(
    client: TestClient, registry: MatterRegistry, matter: MatterConfig
) -> None:
    vault = registry.resolve(matter.matter_id)
    run_id = _seed_running_run(vault)
    headers = _csrf(client)
    # A running (non-checkpoint) run cannot be continued -> OrchestratorError -> 409.
    resp = client.post(
        f"/api/matters/{matter.matter_id}/runs/{run_id}/continue", headers=headers
    )
    assert resp.status_code == 409


# --- OpenAPI export CLI + discriminated-union structure ---------------------


def test_export_openapi_cli_writes_valid_json_with_new_paths(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    result = CliRunner().invoke(cli_app, ["api", "export-openapi", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()
    schema = json.loads(out.read_text(encoding="utf-8"))
    assert schema["openapi"].startswith("3.")
    paths = schema["paths"]
    assert "/api/matters/{matter_id}/runs/{run_id}" in paths
    assert "/api/matters/{matter_id}/runs/{run_id}/gates" in paths
    assert "/api/matters/{matter_id}/runs/{run_id}/decisions" in paths
    assert "/api/matters/{matter_id}/runs/{run_id}/requests" in paths
    # POST wrappers present.
    assert "post" in paths["/api/matters/{matter_id}/runs"]
    assert "post" in paths["/api/matters/{matter_id}/runs/{run_id}/raise-cap"]


def test_openapi_components_show_discriminated_unions() -> None:
    schema = create_matter_api().openapi()
    schemas = schema["components"]["schemas"]

    # 1. run status: the RunStatusSummary envelope exposes the RunStatus Literal as an
    #    enum, and carries a Literal `kind` discriminator.
    run_status = schemas["RunStatusSummary"]["properties"]["status"]
    assert "running" in run_status["enum"] and "paused" in run_status["enum"]
    assert schemas["RunStatusSummary"]["properties"]["kind"]["const"] == "run_status"

    # 2. gate status: turn_gates is a real discriminated union (oneOf + discriminator on
    #    ``status``) over the GatePass/GateFail/GatePending variants.
    turn_gates = schemas["GateLedgerResponse"]["properties"]["turn_gates"]["items"]
    assert "oneOf" in turn_gates
    assert turn_gates["discriminator"]["propertyName"] == "status"
    variants = {ref["$ref"].rsplit("/", 1)[-1] for ref in turn_gates["oneOf"]}
    assert variants == {"GatePass", "GateFail", "GatePending"}

    # 3. decision status: Decision.status is the DecisionStatus Literal (enum).
    decision_status = schemas["Decision"]["properties"]["status"]
    assert "open" in decision_status["enum"] and "approved" in decision_status["enum"]
