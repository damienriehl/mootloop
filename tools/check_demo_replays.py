"""Exercise every published strategy in a fresh external vault with no provider calls."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from mootloop.demo_inputs import import_bundle
from mootloop.models.demo import DemoSnapshot, LocalInputBundle
from mootloop.models.replay import PreparedReplay
from mootloop.orchestrator import run_with_provider, start_run
from mootloop.replay import ReplayProvider
from mootloop.vault import enclosing_git_repo
from mootloop.web.catalog import PublicationError, decode, read_regular
from mootloop.web.release import validate_collection


def check_replays(release: Path, work: Path) -> int:
    if enclosing_git_repo(work) is not None or work.exists():
        raise PublicationError("replay checks require a new external work directory")
    catalog = validate_collection(release)
    work.mkdir(parents=True)
    os.environ["MOOTLOOP_CANARY_REGISTRY"] = str(work / "canaries.json")
    os.environ["MOOTLOOP_MATTERS_ROOT"] = str(work / "matters")
    completed = 0
    now = datetime.now(UTC).isoformat()
    for entry in catalog.entries:
        base = (entry.descriptor.demo_id, entry.revision)
        snapshot = decode(
            DemoSnapshot, read_regular(release, *base, "snapshot.json"), entry.snapshot_sha256
        )
        bundle = decode(
            LocalInputBundle, read_regular(release, *base, "inputs.json"), entry.bundle_sha256
        )
        for strategy in snapshot.strategies:
            folder = work / entry.descriptor.demo_id / strategy.strategy_id
            vault = import_bundle(
                bundle,
                folder / "vault",
                matter_id="2026-09-20-" + entry.descriptor.demo_id + "-" + strategy.strategy_id,
                registry_path=work / "canaries.json",
            )
            script = next(
                f for f in bundle.files if f.name == f"replay-{strategy.strategy_id}.json"
            )
            replay = PreparedReplay.model_validate_json(script.text)
            path = folder / "replay.json"
            path.write_text(script.text)
            run_id = "validation"
            start_run(
                vault,
                bundle.task,
                now,
                run_id=run_id,
                document_input_refs=list(replay.document_input_sha256) or None,
            )
            state = run_with_provider(vault, run_id, ReplayProvider(vault, run_id, path), now)
            if state.status not in {"finished", "needs_decisions"} or state.discarded:
                raise PublicationError(
                    f"replay did not complete: {entry.descriptor.demo_id}/{strategy.strategy_id}"
                )
            completed += 1
            print(
                f"{entry.descriptor.demo_id}/{strategy.strategy_id}: "
                f"{state.status}; human gates retained"
            )
    return completed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("work", type=Path)
    args = parser.parse_args()
    print(
        f"{check_replays(args.release, args.work)} strategy replays completed without model calls"
    )


if __name__ == "__main__":
    main()
