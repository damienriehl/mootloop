"""Hard-human TaskSpec lock contracts at service, launch, CLI, and API boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mootloop import taskspec as taskspec_svc
from mootloop.cli import app as cli_app
from mootloop.context import context_manifest_path, load_run_context
from mootloop.errors import AccessAuthError, OrchestratorError, TaskSpecError
from mootloop.journal import clear_cache, read_events
from mootloop.models.events import RunStarted
from mootloop.orchestrator import start_run
from mootloop.registry import MatterRegistry
from mootloop.taskspec import create_freeform
from mootloop.web.api import create_matter_api
from mootloop.web.api.deps import get_registry, get_verifier
from mootloop.web.security import AccessPrincipal
from tests.unit.test_taskspec import MATTER, NOW, _build_single_request_vault


def _lock(vault: Path, task_spec_id: str, *, actor: str = "attorney@example.com"):
    return taskspec_svc.lock_task_spec(vault, MATTER, task_spec_id, actor, NOW)


def test_unlocked_task_spec_fails_before_run_context_is_written(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    spec = create_freeform(vault, MATTER, "answer the discovery", NOW)

    with pytest.raises(OrchestratorError, match="human lock.*re-lock"):
        start_run(
            vault,
            "discovery-responses",
            NOW,
            run_id="unlocked-spec",
            task_spec_id=spec.task_spec_id,
        )

    assert not context_manifest_path(vault, "unlocked-spec").exists()
    assert read_events(vault, "unlocked-spec") == []


def test_lock_is_append_only_and_exact_retry_is_idempotent(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    spec = create_freeform(vault, MATTER, "answer the discovery", NOW)

    first = _lock(vault, str(spec.task_spec_id))
    retried = taskspec_svc.lock_task_spec(
        vault,
        MATTER,
        str(spec.task_spec_id),
        "attorney@example.com",
        "2026-07-11T00:00:05+00:00",
    )

    assert retried == first
    assert first.source == "human"
    assert first.locked_by == "attorney@example.com"
    assert first.lock_version == 1
    lines = (vault / "tasks" / "locks.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_lock_rejects_unresolved_and_wrong_matter_specs(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    unresolved = create_freeform(vault, MATTER, "draft an appellate brief", NOW)
    resolved = create_freeform(vault, MATTER, "answer the discovery", NOW)

    with pytest.raises(TaskSpecError, match="not resolved"):
        _lock(vault, str(unresolved.task_spec_id))
    with pytest.raises(TaskSpecError, match="matter identity"):
        taskspec_svc.lock_task_spec(
            vault, "different-matter", str(resolved.task_spec_id), "attorney", NOW
        )


def test_locked_start_snapshots_exact_lock_identity(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    spec = create_freeform(vault, MATTER, "answer the discovery", NOW)
    lock = _lock(vault, str(spec.task_spec_id))

    run_id = start_run(
        vault,
        "discovery-responses",
        NOW,
        run_id="locked-spec",
        task_spec_id=spec.task_spec_id,
    )

    event = next(event for event in read_events(vault, run_id) if isinstance(event, RunStarted))
    manifest = load_run_context(vault, run_id).manifest
    assert event.task_spec_lock_id == lock.task_spec_lock_id
    assert event.task_spec_lock_sha256 == lock.record_sha256
    assert manifest.task_spec_lock == lock


def test_adapter_drift_requires_relock_and_relock_appends_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _build_single_request_vault(tmp_path)
    spec = create_freeform(vault, MATTER, "answer the discovery", NOW)
    original = Path("config/tasks/discovery-responses.yaml").resolve()
    adapter = tmp_path / "discovery-responses.yaml"
    adapter.write_bytes(original.read_bytes())
    monkeypatch.setattr("mootloop.tasks.task_config_path", lambda _task: adapter)
    monkeypatch.setattr("mootloop.taskspec.task_config_path", lambda _task: adapter)
    monkeypatch.setattr("mootloop.context.task_config_path", lambda _task: adapter)

    first = _lock(vault, str(spec.task_spec_id))
    adapter.write_bytes(adapter.read_bytes() + b"\n# approved source revision\n")

    with pytest.raises(OrchestratorError, match="adapter.*changed.*re-lock"):
        start_run(
            vault,
            "discovery-responses",
            NOW,
            run_id="stale-lock",
            task_spec_id=spec.task_spec_id,
        )
    assert not context_manifest_path(vault, "stale-lock").exists()

    second = _lock(vault, str(spec.task_spec_id))
    assert second.lock_version == 2
    assert second.task_spec_lock_id != first.task_spec_lock_id
    assert second.adapter_sha256 != first.adapter_sha256
    assert (
        start_run(
            vault,
            "discovery-responses",
            NOW,
            run_id="relocked-spec",
            task_spec_id=spec.task_spec_id,
        )
        == "relocked-spec"
    )


def test_taskspec_content_drift_requires_relock(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    spec = create_freeform(vault, MATTER, "answer the discovery", NOW)
    first = _lock(vault, str(spec.task_spec_id))
    path = vault / "tasks" / "specs.jsonl"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["intent_text"] = "answer the revised discovery"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(OrchestratorError, match="TaskSpec source.*changed.*re-lock"):
        start_run(
            vault,
            "discovery-responses",
            NOW,
            run_id="stale-spec-content",
            task_spec_id=spec.task_spec_id,
        )

    second = _lock(vault, str(spec.task_spec_id))
    assert second.lock_version == 2
    assert second.task_spec_sha256 != first.task_spec_sha256


def test_rubric_and_sidecar_drift_require_relock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mootloop.resources import rubric_path

    vault = _build_single_request_vault(tmp_path)
    spec = create_freeform(vault, MATTER, "answer the discovery", NOW)
    source = rubric_path("discovery-responses-v1.0")
    rubric = tmp_path / source.name
    sidecar = rubric.with_suffix(".sha256")
    rubric.write_bytes(source.read_bytes())
    sidecar.write_bytes(source.with_suffix(".sha256").read_bytes())
    for target in ("mootloop.tasks", "mootloop.taskspec", "mootloop.context"):
        monkeypatch.setattr(f"{target}.rubric_path", lambda _rubric_id: rubric)

    first = _lock(vault, str(spec.task_spec_id))
    changed = rubric.read_bytes() + b"\n# human-reviewed rubric revision\n"
    rubric.write_bytes(changed)
    sidecar.write_text(f"{hashlib.sha256(changed).hexdigest()}  {rubric.name}\n", encoding="utf-8")

    with pytest.raises(OrchestratorError, match="rubric source.*changed.*re-lock"):
        start_run(
            vault,
            "discovery-responses",
            NOW,
            run_id="stale-rubric-content",
            task_spec_id=spec.task_spec_id,
        )

    second = _lock(vault, str(spec.task_spec_id))
    assert second.lock_version == 2
    assert second.rubric_sha256 != first.rubric_sha256
    assert second.rubric_lock_sha256 != first.rubric_lock_sha256


def test_tampered_lock_record_fails_closed(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    spec = create_freeform(vault, MATTER, "answer the discovery", NOW)
    _lock(vault, str(spec.task_spec_id))
    path = vault / "tasks" / "locks.jsonl"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["locked_by"] = "attacker@example.com"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises((TaskSpecError, OrchestratorError), match="digest|integrity"):
        start_run(
            vault,
            "discovery-responses",
            NOW,
            run_id="tampered-lock",
            task_spec_id=spec.task_spec_id,
        )


def test_replayed_old_lock_record_fails_closed(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    spec = create_freeform(vault, MATTER, "answer the discovery", NOW)
    _lock(vault, str(spec.task_spec_id))
    path = vault / "tasks" / "locks.jsonl"
    first = path.read_text(encoding="utf-8")
    path.write_text(first + first, encoding="utf-8")

    with pytest.raises(OrchestratorError, match="integrity.*version"):
        start_run(
            vault,
            "discovery-responses",
            NOW,
            run_id="replayed-lock",
            task_spec_id=spec.task_spec_id,
        )
    assert not context_manifest_path(vault, "replayed-lock").exists()


def test_run_started_lock_identity_drift_is_rejected(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    spec = create_freeform(vault, MATTER, "answer the discovery", NOW)
    _lock(vault, str(spec.task_spec_id))
    run_id = start_run(
        vault,
        "discovery-responses",
        NOW,
        run_id="lock-event-drift",
        task_spec_id=spec.task_spec_id,
    )
    journal = vault / "runs" / run_id / "journal.jsonl"
    event = json.loads(journal.read_text(encoding="utf-8"))
    event["task_spec_lock_id"] = "taskspeclock-replayed-v1"
    journal.write_text(json.dumps(event) + "\n", encoding="utf-8")
    clear_cache()

    with pytest.raises(OrchestratorError, match="TaskSpec lock does not match"):
        load_run_context(vault, run_id)


def test_direct_start_without_task_spec_remains_supported(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    assert start_run(vault, "discovery-responses", NOW, run_id="direct-start") == "direct-start"


def test_cli_lock_derives_actor_and_exposes_no_by_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _build_single_request_vault(tmp_path)
    spec = create_freeform(vault, MATTER, "answer the discovery", NOW)
    fake_pw = type("Pw", (), {"pw_name": "local-lawyer"})()
    monkeypatch.setattr("mootloop.cli.pwd.getpwuid", lambda _uid: fake_pw)

    result = CliRunner().invoke(
        cli_app, ["tasks", "lock", str(vault), str(spec.task_spec_id), "--json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["locked_by"] == "local-lawyer"
    help_result = CliRunner().invoke(cli_app, ["tasks", "lock", "--help"])
    assert "--by" not in help_result.stdout


class _StubVerifier:
    def verify(self, token: str | None) -> AccessPrincipal:
        if token != "good":
            raise AccessAuthError("denied")
        return AccessPrincipal(email="verified@example.com", subject="sub", claims={})


def test_api_lock_derives_principal_and_accepts_no_actor_body(
    tmp_path: Path, matter
) -> None:
    registry = MatterRegistry(root=tmp_path / "matters")
    vault = registry.create(matter)
    spec = create_freeform(vault, str(matter.matter_id), "answer the discovery", NOW)
    app = create_matter_api()
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_verifier] = _StubVerifier
    client = TestClient(app)
    auth = {"cf-access-jwt-assertion": "good"}
    issued = client.get("/api/csrf", headers=auth)
    headers = {**auth, "x-csrf-token": issued.json()["csrf_token"]}

    response = client.post(
        f"/api/matters/{matter.matter_id}/tasks/{spec.task_spec_id}/lock",
        headers=headers,
        json={"actor": "spoofed@example.com", "source": "policy"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["task_spec_lock"]["locked_by"] == "verified@example.com"
    assert response.json()["task_spec_lock"]["source"] == "human"
    operation = app.openapi()["paths"][
        "/api/matters/{matter_id}/tasks/{task_spec_id}/lock"
    ]["post"]
    assert "requestBody" not in operation


def test_api_lock_requires_access_and_csrf(tmp_path: Path, matter) -> None:
    registry = MatterRegistry(root=tmp_path / "matters")
    vault = registry.create(matter)
    spec = create_freeform(vault, str(matter.matter_id), "answer the discovery", NOW)
    app = create_matter_api()
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_verifier] = _StubVerifier
    client = TestClient(app)
    path = f"/api/matters/{matter.matter_id}/tasks/{spec.task_spec_id}/lock"

    assert client.post(path).status_code == 401
    assert client.post(path, headers={"cf-access-jwt-assertion": "good"}).status_code == 403
