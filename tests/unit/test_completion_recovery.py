from pathlib import Path

import pytest

from mootloop import orchestrator
from mootloop.decisions import DecisionStore
from mootloop.engine.queue import Queue
from mootloop.engine.worker import Worker
from mootloop.journal import load_state, read_events
from mootloop.llm import FakeLLMProvider
from mootloop.models.events import GateEvaluated, JournalEvent, TurnCompleted
from mootloop.orchestrator import assemble_prompt, plan_next, record_turn, start_run
from tests.unit.test_engine_worker import (
    MATTER_ID,
    _build_matters_root,
    _enqueue_run_turn,
    _fake_factory,
)
from tests.unit.test_engine_worker import (
    NOW as WORKER_NOW,
)
from tests.unit.test_orchestrator_planning import NOW, _build_single_request_vault
from tests.unit.test_orchestrator_spend import EXPECTED_USD, USAGE


def test_completion_is_after_decisions_and_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="completion-crash")
    spec = plan_next(vault, run_id)[0]
    turn = FakeLLMProvider().run_turn(spec, assemble_prompt(vault, run_id, spec.turn_id))
    real_append = orchestrator.append

    def crash_after_completion(root: Path | str, run: str, event: JournalEvent) -> None:
        real_append(root, run, event)
        if isinstance(event, TurnCompleted):
            raise OSError("interrupted after completion")

    monkeypatch.setattr(orchestrator, "append", crash_after_completion)
    with pytest.raises(OSError, match="interrupted"):
        record_turn(vault, run_id, spec.turn_id, turn.text, USAGE, NOW, provider_call_id="call-1")
    assert DecisionStore(vault, run_id).list_all()
    assert load_state(vault, run_id).total_spend_usd == EXPECTED_USD
    monkeypatch.setattr(orchestrator, "append", real_append)
    record_turn(vault, run_id, spec.turn_id, turn.text, USAGE, NOW, provider_call_id="call-1")
    assert load_state(vault, run_id).total_spend_usd == EXPECTED_USD


def test_worker_recovers_final_rubric_and_run_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, run_id = _build_matters_root(tmp_path)
    vault = root / MATTER_ID
    provider = FakeLLMProvider()
    real_append = orchestrator.append

    def interrupt_aggregation(root: Path | str, run: str, event: JournalEvent) -> None:
        if isinstance(event, GateEvaluated) and event.result.gate == "rubric":
            raise OSError("interrupted aggregation")
        real_append(root, run, event)

    monkeypatch.setattr(orchestrator, "append", interrupt_aggregation)
    with pytest.raises(OSError, match="interrupted aggregation"):
        for _ in range(30):
            specs = plan_next(vault, run_id)
            assert specs
            for spec in specs:
                result = provider.run_turn(spec, assemble_prompt(vault, run_id, spec.turn_id))
                record_turn(vault, run_id, spec.turn_id, result.text, result.usage, NOW)
    assert plan_next(vault, run_id) == []
    assert load_state(vault, run_id).status == "running"
    monkeypatch.setattr(orchestrator, "append", real_append)
    queue = Queue(root)
    _enqueue_run_turn(queue, run_id, "recover-finalization")
    worker = Worker(root, "recover", queue, _fake_factory)
    assert worker.run_once(WORKER_NOW)
    assert load_state(vault, run_id).status == "finished"
    gates = [
        e
        for e in read_events(vault, run_id)
        if isinstance(e, GateEvaluated) and e.result.gate == "rubric"
    ]
    assert len(gates) == 1
    assert queue.snapshot() == []


@pytest.mark.parametrize("worker_id", ["../escape", "/tmp/escape", "a/b", "..", ""])
def test_worker_rejects_unsafe_identity(tmp_path: Path, worker_id: str) -> None:
    from mootloop.errors import MootloopError

    with pytest.raises(MootloopError):
        Worker(tmp_path, worker_id, Queue(tmp_path), _fake_factory)
