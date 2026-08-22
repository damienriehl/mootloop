"""Derived trace trees and immutable run evidence packs."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mootloop.cli import app
from mootloop.errors import OrchestratorError
from mootloop.evidence import (
    build_evidence_pack,
    evidence_pack_path,
    list_evidence_packs,
    load_trace_tree,
)
from mootloop.journal import append
from mootloop.models.events import RunPaused
from mootloop.models.evidence import RunEvidencePack, TraceTree
from mootloop.orchestrator import start_run
from mootloop.persistence import sha256_file
from mootloop.vault import init_vault
from tests.conftest import make_matter

NOW = "2026-08-21T00:00:00+00:00"


def _run(tmp_path: Path, *, matter_id: str = "acme-v-widgets") -> Path:
    vault = tmp_path / "vault"
    init_vault(vault, make_matter(matter_id), registry_path=tmp_path / "canaries.json")
    start_run(vault, "discovery-responses", NOW, run_id="trace-run-0001")
    append(vault, "trace-run-0001", RunPaused(reason="operator checkpoint"))
    return vault


def test_builds_content_free_trace_and_numbered_evidence_pack(tmp_path: Path) -> None:
    vault = _run(tmp_path)

    first = build_evidence_pack(
        vault, "trace-run-0001", NOW, generated_by="Attorney", channel="cli"
    )
    second = build_evidence_pack(
        vault,
        "trace-run-0001",
        "2026-08-21T00:01:00+00:00",
        generated_by="Attorney",
        channel="cli",
    )

    assert isinstance(first, RunEvidencePack)
    assert first.evidence_pack_id == "EP-mootloop-trace-run-0001-001"
    assert (first.generated_by, first.channel) == ("Attorney", "cli")
    assert second.evidence_pack_id == "EP-mootloop-trace-run-0001-002"
    assert first.pack_sha256 == first.expected_pack_sha256()
    assert sha256_file(evidence_pack_path(vault, "trace-run-0001", 1))
    assert [pack.evidence_pack_id for pack in list_evidence_packs(vault, "trace-run-0001")] == [
        first.evidence_pack_id,
        second.evidence_pack_id,
    ]

    trace = load_trace_tree(vault, "trace-run-0001")
    assert isinstance(trace, TraceTree)
    assert trace.nodes[0].kind == "run"
    assert [node.kind for node in trace.nodes[1:]] == [
        "run_started",
        "run_finished",
        "run_paused",
    ]
    assert all(node.label is None for node in trace.nodes)
    first_line = (vault / "runs" / "trace-run-0001" / "journal.jsonl").read_bytes().splitlines(
        keepends=True
    )[0]
    assert trace.nodes[1].source_sha256 == hashlib.sha256(first_line).hexdigest()
    assert second.trace_tree_sha256 == trace.expected_tree_sha256()
    assert first.trace_tree_sha256 == first.trace_tree.expected_tree_sha256()


def test_evidence_pack_binds_fixed_source_paths_without_work_product(tmp_path: Path) -> None:
    vault = _run(tmp_path)
    pack = build_evidence_pack(
        vault, "trace-run-0001", NOW, generated_by="Attorney", channel="cli"
    )

    paths = {item.path for item in pack.commitments}
    assert "runs/trace-run-0001/journal.jsonl" in paths
    assert "runs/trace-run-0001/context/manifest.json" in paths
    assert all("operator checkpoint" not in item.path for item in pack.commitments)


def test_cli_builds_lists_and_reads_trace(tmp_path: Path) -> None:
    vault = _run(tmp_path)
    runner = CliRunner()

    built = runner.invoke(app, ["run", "evidence-build", str(vault), "trace-run-0001"])
    listed = runner.invoke(app, ["run", "evidence-list", str(vault), "trace-run-0001"])
    traced = runner.invoke(app, ["run", "trace", str(vault), "trace-run-0001"])

    assert built.exit_code == 0, built.output
    assert "EP-mootloop-trace-run-0001-001" in built.output
    assert listed.exit_code == 0 and "EP-mootloop-trace-run-0001-001" in listed.output
    assert traced.exit_code == 0 and '"tree_sha256"' in traced.output


def test_list_rejects_self_consistent_pack_copied_from_another_matter(tmp_path: Path) -> None:
    source = _run(tmp_path / "source", matter_id="source-matter")
    target = _run(tmp_path / "target", matter_id="target-matter")
    build_evidence_pack(
        source, "trace-run-0001", NOW, generated_by="Attorney", channel="cli"
    )
    source_pack = evidence_pack_path(source, "trace-run-0001", 1)
    target_pack = evidence_pack_path(target, "trace-run-0001", 1)
    target_pack.parent.mkdir(parents=True, exist_ok=True)
    target_pack.write_bytes(source_pack.read_bytes())

    with pytest.raises(OrchestratorError, match="another matter"):
        list_evidence_packs(target, "trace-run-0001")
