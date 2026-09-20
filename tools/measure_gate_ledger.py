"""Measure ledger reads on disposable synthetic runs; never accepts a vault path.

Run from the repository root with `uv run python -m tools.measure_gate_ledger` in
the isolated environment documented in the accompanying measurement audit.
"""

from __future__ import annotations

import cProfile
import hashlib
import json
import platform
import pstats
import statistics
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any
from unittest.mock import patch

from tests.integration.test_pipeline import NOW, _build_matter_vault
from tests.unit.test_orchestrator_planning import _build_single_request_vault

from mootloop import gate_ledger, journal, orchestrator
from mootloop.decisions import DecisionStore
from mootloop.llm import FakeLLMProvider
from mootloop.models.events import GateEvaluated

REPEATS = 15


def fingerprint(vault: Path) -> dict[str, str]:
    return {
        str(path.relative_to(vault)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in vault.rglob("*")
        if path.is_file()
    }


def summary(values: list[float]) -> dict[str, float]:
    return {
        "median_ms": statistics.median(values),
        "min_ms": min(values),
        "max_ms": max(values),
    }


def measure(vault: Path, run_id: str) -> dict[str, object]:
    before = fingerprint(vault)
    reference_doc = gate_ledger.build_ledger(vault, run_id)
    reference = reference_doc.to_dict()
    samples: dict[str, list[dict[str, float]]] = {"warm": [], "parser_cold": []}
    timed_function = (
        "operative_draft_records"
        if hasattr(gate_ledger, "operative_draft_records")
        else "operative_drafts"
    )
    original = getattr(gate_ledger, timed_function)
    extra_ms = 0.0

    def timed_drafts(*args: Any, **kwargs: Any) -> Any:
        nonlocal extra_ms
        start = perf_counter()
        result = original(*args, **kwargs)
        extra_ms = (perf_counter() - start) * 1000
        return result

    with patch.object(gate_ledger, timed_function, timed_drafts):
        for iteration in range(REPEATS):
            # Alternate order to reduce systematic order bias. Warm means one
            # untimed ledger read; cold clears only the existing parser cache.
            modes = ("warm", "parser_cold") if iteration % 2 else ("parser_cold", "warm")
            for mode in modes:
                if mode == "parser_cold":
                    journal.clear_cache()
                else:
                    gate_ledger.build_ledger(vault, run_id)
                start = perf_counter()
                actual = gate_ledger.build_ledger(vault, run_id).to_dict()
                total_ms = (perf_counter() - start) * 1000
                assert actual == reference, "ledger changed during read-only measurement"
                samples[mode].append({"total_ms": total_ms, "selection_ms": extra_ms})

    profile = cProfile.Profile()
    profile.runcall(gate_ledger.build_ledger, vault, run_id)
    stats = pstats.Stats(profile)
    selected = {
        "load_state",
        "fold",
        "read_events",
        "load_run_context",
        "operative_drafts",
        "operative_draft_turn_ids",
        "operative_draft_records",
        "_context_for",
    }
    functions = [
        {
            "file": Path(filename).name,
            "function": name,
            "calls": record[1],
            "self_ms": record[2] * 1000,
            "inclusive_ms": record[3] * 1000,
        }
        for (filename, _line, name), record in stats.stats.items()
        if name in selected and "/mootloop/" in filename
    ]
    events = journal.read_events(vault, run_id)
    result: dict[str, object] = {
        "selection_function": timed_function,
        "requests": len(reference_doc.gates),
        "completed_turns": len(journal.load_state(vault, run_id).completed_turns),
        "decisions": len(DecisionStore(vault, run_id).list_all()),
        "events": len(events),
        "journal_bytes": journal.journal_path(vault, run_id).stat().st_size,
        "export_ready": reference["export_ready"],
        "samples": samples,
        "summary": {
            mode: {
                "total": summary([sample["total_ms"] for sample in rows]),
                "selection": summary([sample["selection_ms"] for sample in rows]),
                "median_selection_percent": statistics.median(
                    [sample["selection_ms"] / sample["total_ms"] * 100 for sample in rows]
                ),
            }
            for mode, rows in samples.items()
        },
        "warm_profile": functions,
        "warm_profile_top_self": [
            {
                "file": Path(filename).name,
                "function": name,
                "calls": record[1],
                "self_ms": record[2] * 1000,
                "inclusive_ms": record[3] * 1000,
            }
            for (filename, _line, name), record in sorted(
                stats.stats.items(), key=lambda item: item[1][2], reverse=True
            )[:12]
        ],
    }
    assert fingerprint(vault) == before, "measurement mutated synthetic vault files"
    result["vault_bytes_unchanged"] = True
    return result


def main() -> None:
    results: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="mootloop-ledger-measure-") as temporary:
        root = Path(temporary)
        for name, builder in (
            ("single_request", _build_single_request_vault),
            ("eighteen_requests", _build_matter_vault),
        ):
            parent = root / name
            parent.mkdir()
            vault = builder(parent)
            run_id = "ledger-measure"
            orchestrator.start_run(vault, "discovery-responses", NOW, run_id=run_id)
            if name == "eighteen_requests":
                results["eighteen_requests_unfinished_no_decisions"] = measure(vault, run_id)
            orchestrator.run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
            results[name] = measure(vault, run_id)
            if name != "eighteen_requests":
                continue
            original_events = journal.read_events(vault, run_id)
            gate = next(event for event in original_events if isinstance(event, GateEvaluated))
            # Valid repeated gate evaluations stress history length without
            # inventing turns, spend, requests, or provider work. Not a real run.
            for multiplier in (5, 20):
                target = len(original_events) * multiplier
                count = len(journal.read_events(vault, run_id))
                for _ in range(target - count):
                    journal.append(vault, run_id, gate)
                results[f"gate_history_{multiplier}x"] = measure(vault, run_id)
    print(
        json.dumps(
            {
                "schema_version": "1.1",
                "python": platform.python_version(),
                "platform": platform.system(),
                "repeats_per_mode": REPEATS,
                "actual_provider_spend_usd": 0,
                "cases": results,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
