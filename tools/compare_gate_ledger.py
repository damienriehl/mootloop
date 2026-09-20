"""Compare the repaired ledger with its pre-optimization implementation offline."""

from __future__ import annotations

import cProfile
import json
import pstats
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from types import ModuleType

from tests.integration.test_pipeline import NOW, _build_matter_vault
from tests.unit.test_orchestrator_planning import _build_single_request_vault
from tools.measure_gate_ledger import fingerprint, summary

from mootloop import gate_ledger, journal, orchestrator
from mootloop.llm import FakeLLMProvider
from mootloop.models.events import GateEvaluated

BASELINE = "73833b4613ebdab9941044ec8aa13e39b81bb1d0"
REPEATS = 15


def baseline_module() -> ModuleType:
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE}:src/mootloop/gate_ledger.py"], text=True
    )
    module = ModuleType("_baseline_gate_ledger")
    sys.modules[module.__name__] = module
    exec(compile(source, "baseline_gate_ledger.py", "exec"), module.__dict__)
    return module


def compare(vault: Path, run_id: str, baseline: ModuleType) -> dict[str, object]:
    before = fingerprint(vault)
    reference = baseline.build_ledger(vault, run_id).to_dict()
    readers = {"baseline": baseline.build_ledger, "candidate": gate_ledger.build_ledger}
    samples: dict[str, dict[str, list[float]]] = {
        mode: {name: [] for name in readers} for mode in ("warm", "parser_cold")
    }
    for iteration in range(REPEATS):
        names = list(readers) if iteration % 2 else list(reversed(readers))
        modes = list(samples) if iteration % 2 else list(reversed(samples))
        for mode in modes:
            for name in names:
                reader = readers[name]
                if mode == "parser_cold":
                    journal.clear_cache()
                else:
                    reader(vault, run_id)
                start = perf_counter()
                actual = reader(vault, run_id).to_dict()
                elapsed = (perf_counter() - start) * 1000
                assert actual == reference, f"ledger semantics changed: {name}"
                samples[mode][name].append(elapsed)
    profiles = {}
    for name, reader in readers.items():
        profile = cProfile.Profile()
        profile.runcall(reader, vault, run_id)
        profiles[name] = [
            {"function": function, "calls": record[1]}
            for (_file, _line, function), record in pstats.Stats(profile).stats.items()
            if function in {"fold", "load_run_context", "_context_for"}
        ]
    assert fingerprint(vault) == before, "ledger reads changed vault contents"
    return {
        "events": len(journal.read_events(vault, run_id)),
        "samples_ms": samples,
        "summary": {
            mode: {name: summary(values) for name, values in by_reader.items()}
            for mode, by_reader in samples.items()
        },
        "profiles": profiles,
        "outputs_match_baseline": True,
        "vault_bytes_unchanged": True,
    }


def main() -> None:
    baseline = baseline_module()
    results = {}
    with tempfile.TemporaryDirectory(prefix="mootloop-ledger-compare-") as temporary:
        root = Path(temporary)
        for name, builder in (
            ("single_request", _build_single_request_vault),
            ("eighteen_requests", _build_matter_vault),
        ):
            parent = root / name
            parent.mkdir()
            vault = builder(parent)
            run_id = "ledger-compare"
            orchestrator.start_run(vault, "discovery-responses", NOW, run_id=run_id)
            if name == "eighteen_requests":
                results["eighteen_requests_unfinished_no_decisions"] = compare(
                    vault, run_id, baseline
                )
            orchestrator.run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
            results[name] = compare(vault, run_id, baseline)
            if name != "eighteen_requests":
                continue
            original_events = journal.read_events(vault, run_id)
            gate = next(event for event in original_events if isinstance(event, GateEvaluated))
            for multiplier in (5, 20):
                target = len(original_events) * multiplier
                count = len(journal.read_events(vault, run_id))
                for _ in range(target - count):
                    journal.append(vault, run_id, gate)
                results[f"gate_history_{multiplier}x"] = compare(vault, run_id, baseline)
    print(
        json.dumps(
            {
                "baseline_commit": BASELINE,
                "baseline_note": (
                    "Baseline ledger uses current public orchestrator wrappers; both select "
                    "through the same unchanged StageContext semantics. "
                    "No legacy code is installed."
                ),
                "repeats_per_reader_per_mode": REPEATS,
                "actual_provider_spend_usd": 0,
                "cases": results,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
