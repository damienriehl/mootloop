from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mootloop.cli import app
from mootloop.context import load_run_context
from mootloop.errors import OrchestratorError
from mootloop.journal import load_state, read_events
from mootloop.llm import FakeLLMProvider
from mootloop.models.replay import PreparedReplay, ReplayResponse
from mootloop.orchestrator import plan_next, record_turn, run_with_provider, start_run
from mootloop.replay import ReplayProvider
from tests.unit.test_document_tasks import NOW, TASKS, document_vault


def test_fresh_cli_manual_document_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("MOOTLOOP_MATTERS_ROOT", str(tmp_path / "empty-registry"))
    vault = document_vault(tmp_path, "complaint")
    runner = CliRunner()
    base = [str(vault), "manual"]
    launched = runner.invoke(
        app,
        [
            "run",
            "start",
            str(vault),
            "--task",
            "complaint",
            "--document-input",
            "motion-a",
            "--run-id",
            "manual",
            "--mode",
            "gated",
        ],
    )
    assert launched.exit_code == 0, launched.output
    first = plan_next(vault, "manual")[0]
    prompt = runner.invoke(app, ["run", "prompt", *base, first.turn_id])
    assert prompt.exit_code == 0 and "complaint" in prompt.output.lower()
    raw = tmp_path / "output.json"
    raw.write_text("{}")
    invalid = runner.invoke(app, ["run", "record-turn", *base, first.turn_id, "--input", str(raw)])
    assert "discarded" in invalid.output
    assert plan_next(vault, "manual")[0].attempt == 2
    raw.write_text(FakeLLMProvider().run_turn(first, prompt.output).text)
    corrected = runner.invoke(
        app, ["run", "record-turn", *base, first.turn_id, "--input", str(raw)]
    )
    assert corrected.exit_code == 0 and "recorded" in corrected.output
    for command in ("pause", "status", "resume"):
        result = runner.invoke(app, ["run", command, *base])
        assert result.exit_code == 0, result.output
    provider = FakeLLMProvider()
    continued = 0
    while not load_state(vault, "manual").is_terminal:
        specs = plan_next(vault, "manual")
        if not specs:
            result = runner.invoke(app, ["run", "continue", *base])
            assert result.exit_code == 0, result.output
            continued += 1
            continue
        for spec in specs:
            raw.write_text(provider.run_turn(spec, "").text)
            result = runner.invoke(
                app, ["run", "record-turn", *base, spec.turn_id, "--input", str(raw)]
            )
            assert result.exit_code == 0, result.output
    assert continued > 0
    assert load_state(vault, "manual").status == "finished"
    assert (vault / "deliverables" / "manual" / "master.md").is_file()
    assert not (tmp_path / "empty-home" / ".mootloop" / "secrets.env").exists()


def test_explicit_selection_and_retry_binding(tmp_path: Path) -> None:
    vault = document_vault(tmp_path, "motion")
    (vault / "documents" / "unrelated.json").write_text("{}")
    with pytest.raises(OrchestratorError):
        start_run(vault, "motion", NOW, run_id="unknown", document_input_refs=["missing"])
    assert not read_events(vault, "unknown")
    start_run(vault, "motion", NOW, run_id="selected", document_input_refs=["motion-a"])
    before = read_events(vault, "selected")
    assert (
        start_run(
            vault,
            "motion",
            NOW,
            run_id="selected",
            document_input_refs=["motion-a"],
            idempotent=True,
        )
        == "selected"
    )
    assert read_events(vault, "selected") == before
    with pytest.raises(OrchestratorError):
        start_run(
            vault,
            "motion",
            NOW,
            run_id="selected",
            document_input_refs=["unrelated"],
            idempotent=True,
        )


@pytest.mark.parametrize("task", (*TASKS, "discovery-responses"))
def test_prepared_replay_uses_fresh_run_identity(tmp_path: Path, task: str) -> None:
    if task == "discovery-responses":
        from mootloop.models.requests import RequestType
        from tests.unit.test_attest import _vault

        vault = _vault(tmp_path, RequestType.INTERROGATORY)
    else:
        vault = document_vault(tmp_path, task)
    start_run(vault, task, NOW, run_id="prepare")
    prepared = run_with_provider(vault, "prepare", FakeLLMProvider(), NOW)
    context = load_run_context(vault, "prepare")
    counts: dict[tuple[str, str], int] = {}
    responses = []
    for record in prepared.completed_turns.values():
        spec = record.spec
        key = (str(spec.request_id), spec.stage)
        counts[key] = counts.get(key, 0) + 1
        responses.append(
            ReplayResponse(
                unit_id=key[0],
                stage=key[1],
                occurrence=counts[key],
                output_schema_name=spec.output_schema_name,
                output=record.output,
            )
        )
    replay = PreparedReplay(
        task=task,
        document_input_sha256={
            Path(s.locator).stem: s.sha256
            for s in context.manifest.sources
            if s.kind == "document_input"
        },
        discovery_input_sha256={
            s.locator: s.sha256
            for s in context.manifest.sources
            if s.kind in {"request_set", "fact_repository", "corpus_content"}
        }
        if task == "discovery-responses"
        else {},
        responses=responses,
    )
    path = tmp_path / "replay.json"
    path.write_text(replay.model_dump_json())
    start_run(vault, task, NOW, run_id="local")
    provider = ReplayProvider(vault, "local", path)
    first = plan_next(vault, "local")[0]
    initial = provider.run_turn(first, "")
    assert initial.usage is None
    record_turn(vault, "local", first.turn_id, initial.text, None, NOW)
    provider = ReplayProvider(vault, "local", path)
    result = run_with_provider(vault, "local", provider, NOW)
    assert result.status == prepared.status
    assert len(result.completed_turns) == len(prepared.completed_turns)
    assert result.total_spend_usd == 0
    start_run(vault, task, NOW, run_id="missing")
    missing = replay.model_dump(mode="json")
    missing["responses"] = missing["responses"][1:]
    path.write_text(json.dumps(missing))
    with pytest.raises(OrchestratorError, match="no matching response"):
        run_with_provider(vault, "missing", ReplayProvider(vault, "missing", path), NOW)
    assert not load_state(vault, "missing").completed_turns
    if task == "discovery-responses":
        missing["discovery_input_sha256"] = {"wrong": "0" * 64}
    else:
        missing["document_input_sha256"] = {"wrong": "0" * 64}
    path.write_text(json.dumps(missing))
    with pytest.raises(OrchestratorError, match="frozen (document|discovery) inputs"):
        ReplayProvider(vault, "missing", path)


def test_document_selection_cannot_override_locked_spec(tmp_path: Path) -> None:
    from mootloop.context import load_document_inputs
    from mootloop.models.common import MatterId, TaskSpecId
    from mootloop.models.taskspec import TaskSpec
    from mootloop.taskspec import TaskSpecStore, lock_task_spec
    from mootloop.vault import load_matter

    vault = document_vault(tmp_path, "motion")
    _, sources = load_document_inputs(vault, "motion", ["motion-a"])
    matter_id = load_matter(vault).matter_id
    spec = TaskSpec(
        task_spec_id=TaskSpecId("approved"),
        matter_id=MatterId(matter_id),
        task="motion",
        source_lane="freeform",
        intent_text="Draft motion",
        created_at=NOW,
        document_input_refs=["motion-a"],
        document_input_sha256={"motion-a": sources[0].sha256},
    )
    TaskSpecStore(vault).append(spec)
    lock_task_spec(vault, matter_id, "approved", "Reviewer", NOW)
    with pytest.raises(OrchestratorError, match="differs from approved TaskSpec"):
        start_run(
            vault,
            "motion",
            NOW,
            run_id="mismatch",
            task_spec_id="approved",
            document_input_refs=["other"],
        )
    assert not read_events(vault, "mismatch")
    start_run(
        vault,
        "motion",
        NOW,
        run_id="approved-run",
        task_spec_id="approved",
        document_input_refs=["motion-a"],
    )
