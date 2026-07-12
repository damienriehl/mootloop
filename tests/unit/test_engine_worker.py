"""Light smoke test for the driver `Worker` loop (thorough set in Unit 3).

Drives a real (fake-provider) run end-to-end through a claimed queue item: the worker
resolves the vault under the matters-root, writes TurnIntents, records turns, and
completes the queue item when the run stops being schedulable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mootloop.engine.queue import Queue, WorkItem
from mootloop.engine.worker import Worker
from mootloop.journal import load_state
from mootloop.llm import FakeLLMProvider, LLMProvider
from mootloop.models.common import DocId
from mootloop.models.requests import RequestItem, RequestSet, RequestType
from mootloop.registry import MatterRegistry

NOW = datetime(2026, 7, 12, tzinfo=UTC)
MATTER_ID = "acme-v-widgets"


def _fake_factory(vault_root: Path, run_dir: Path, billing_mode: str) -> LLMProvider:
    return FakeLLMProvider()


def _build_matters_root(tmp_path: Path) -> tuple[Path, str]:
    from mootloop.discovery_parser import save_requests
    from mootloop.facts import FactStore
    from mootloop.orchestrator import start_run
    from tests.conftest import make_matter

    root = tmp_path / "matters"
    registry = MatterRegistry(root=root)
    vault = registry.create(make_matter(MATTER_ID))
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
    run_id = start_run(vault, "discovery-responses", NOW.isoformat(), run_id="drive-0001")
    return root, run_id


def test_worker_drains_a_run_and_completes_item(tmp_path: Path) -> None:
    root, run_id = _build_matters_root(tmp_path)
    queue = Queue(root)
    queue.enqueue(
        WorkItem.create(
            lane="run",
            matter_id=MATTER_ID,
            run_id=run_id,
            kind="run_turn",
            now=NOW,
            item_id="wi-1",
        )
    )
    worker = Worker(root, "w1", queue, _fake_factory)

    did_work = worker.run_once(NOW)
    assert did_work is True

    vault = MatterRegistry(root=root).resolve(MATTER_ID)
    state = load_state(vault, run_id)
    assert len(state.completed_turns) > 0
    assert state.status != "running"  # finished or blocked on a gate, not still ticking
    assert queue.snapshot() == []  # the item was completed


def test_worker_idle_returns_false(tmp_path: Path) -> None:
    root = tmp_path / "matters"
    root.mkdir()
    worker = Worker(root, "w1", Queue(root), _fake_factory)
    assert worker.run_once(NOW) is False
