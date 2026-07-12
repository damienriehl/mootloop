"""Smoke tests for the FE-1 API surface: SSE helpers + pause/resume + queue/next."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mootloop import orchestrator
from mootloop.engine.queue import Queue, WorkItem
from mootloop.errors import AccessAuthError
from mootloop.models.common import DocId
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestItem, RequestSet, RequestType
from mootloop.registry import MatterRegistry
from mootloop.web.api import create_matter_api
from mootloop.web.api.deps import get_internal_auth, get_queue, get_registry, get_verifier
from mootloop.web.api.sse import format_sse, iter_sse_lines
from mootloop.web.security import AccessPrincipal, InternalAuth

_PRINCIPAL = AccessPrincipal(email="attorney@example.com", subject="sub-1", claims={})
_AUTH = {"cf-access-jwt-assertion": "good"}
_INTERNAL = {"x-mootloop-internal": "s3cr3t"}
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
def queue(registry: MatterRegistry) -> Queue:
    return Queue(registry.root)


@pytest.fixture
def client(registry: MatterRegistry, queue: Queue) -> TestClient:
    app = create_matter_api()
    app.dependency_overrides[get_verifier] = _StubVerifier
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_queue] = lambda: queue
    app.dependency_overrides[get_internal_auth] = lambda: InternalAuth(secret="s3cr3t")
    return TestClient(app)


def _csrf(client: TestClient) -> dict[str, str]:
    issued = client.get("/api/csrf", headers=_AUTH)
    return {**_AUTH, "x-csrf-token": issued.json()["csrf_token"]}


def _started_running_run(vault: Path) -> str:
    """Seed a request + fact so the started run stays ``running`` (pausable)."""
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


# --- pure SSE helpers -------------------------------------------------------


def test_format_sse_frames_a_data_event() -> None:
    assert format_sse('{"a":1}') == 'data: {"a":1}\n\n'


def test_iter_sse_lines_yields_events(registry: MatterRegistry, matter: MatterConfig) -> None:
    vault = registry.resolve(matter.matter_id)
    run_id = orchestrator.start_run(vault, "discovery-responses", _NOW)
    frames = list(iter_sse_lines(vault, run_id))
    assert frames and all(f.startswith("data: ") for f in frames)


# --- pause / resume ---------------------------------------------------------


def test_human_pause_then_resume(client: TestClient, registry: MatterRegistry) -> None:
    matter_id = registry.list_matters()[0].matter_id
    vault = registry.resolve(matter_id)
    run_id = _started_running_run(vault)
    headers = _csrf(client)

    paused = client.post(f"/api/matters/{matter_id}/runs/{run_id}/pause", headers=headers)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/api/matters/{matter_id}/runs/{run_id}/resume", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"


def test_internal_pause_requires_internal_secret(
    client: TestClient, registry: MatterRegistry
) -> None:
    matter_id = registry.list_matters()[0].matter_id
    vault = registry.resolve(matter_id)
    run_id = _started_running_run(vault)

    assert (
        client.post(f"/internal/matters/{matter_id}/runs/{run_id}/pause", json={}).status_code
        == 401
    )
    ok = client.post(
        f"/internal/matters/{matter_id}/runs/{run_id}/pause", headers=_INTERNAL, json={}
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "paused"


# --- queue/next -------------------------------------------------------------


def test_internal_queue_next_claims_or_204(
    client: TestClient, queue: Queue, registry: MatterRegistry
) -> None:
    empty = client.get("/internal/queue/next?worker_id=w1", headers=_INTERNAL)
    assert empty.status_code == 204

    queue.enqueue(
        WorkItem.create(
            lane="run",
            matter_id=registry.list_matters()[0].matter_id,
            run_id="r1",
            kind="run_turn",
            now=datetime.fromisoformat(_NOW),
        )
    )
    claimed = client.get("/internal/queue/next?worker_id=w1", headers=_INTERNAL)
    assert claimed.status_code == 200
    assert claimed.json()["kind"] == "run_turn"


# --- SSE stream -------------------------------------------------------------


def test_stream_returns_event_stream(client: TestClient, registry: MatterRegistry) -> None:
    matter_id = registry.list_matters()[0].matter_id
    vault = registry.resolve(matter_id)
    run_id = orchestrator.start_run(vault, "discovery-responses", _NOW)
    # A started run with a single request finalizes to a terminal/blocked state; force a
    # terminal RunFinished so the stream drains and returns instead of streaming forever.
    from mootloop.journal import append
    from mootloop.models.events import RunFinished

    append(vault, run_id, RunFinished(status="finished"))

    resp = client.get(f"/api/matters/{matter_id}/runs/{run_id}/stream", headers=_AUTH)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "data: " in resp.text
