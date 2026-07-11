"""GateResult discriminated union + GateLedger helpers."""

from __future__ import annotations

from pydantic import TypeAdapter

from mootloop.models.gates import (
    GateFail,
    GateFinding,
    GateLedger,
    GatePass,
    GatePending,
    GateResult,
)

_ADAPTER: TypeAdapter[GateResult] = TypeAdapter(GateResult)


def test_status_discriminates_the_union() -> None:
    # Round-tripping a concrete result through JSON carries the discriminator.
    reparsed = _ADAPTER.validate_json(GatePass(gate="degeneracy").model_dump_json())
    assert isinstance(reparsed, GatePass)
    fail = _ADAPTER.validate_python(
        {"status": "fail", "gate": "degeneracy", "findings": [{"code": "empty", "message": "x"}]}
    )
    assert isinstance(fail, GateFail)
    assert fail.findings[0].code == "empty"
    pending = _ADAPTER.validate_python({"status": "pending", "gate": "citation"})
    assert isinstance(pending, GatePending)


def test_ledger_all_pass() -> None:
    passing = GateLedger(results=[GatePass(gate="degeneracy"), GatePass(gate="fabrication")])
    assert passing.all_pass()
    assert passing.blocking() == []

    mixed = GateLedger(
        results=[
            GatePass(gate="degeneracy"),
            GateFail(gate="citation", findings=[GateFinding(code="404", message="not found")]),
        ]
    )
    assert not mixed.all_pass()
    assert len(mixed.blocking()) == 1


def test_empty_ledger_passes_vacuously() -> None:
    assert GateLedger().all_pass()
