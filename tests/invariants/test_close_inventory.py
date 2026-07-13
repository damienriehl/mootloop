"""FD-6 close-inventory gate: the load-bearing CI invariant.

Every concrete `VersionedModel` defined under ``mootloop`` MUST be either registered as
a matter-scoped store (so `mootloop close` purges it) or explicitly exempt with a
reason. A new matter-scoped model that is neither fails this test — which is the whole
point: the guarantee that ``close`` misses nothing is enforced here, not by memory.
"""

from __future__ import annotations

import pytest

from mootloop.close import (
    EXEMPT_MODELS,
    MATTER_SCOPED_MODELS,
    concrete_versioned_models,
    is_registered,
    unregistered_models,
)
from mootloop.models.common import VersionedModel

pytestmark = pytest.mark.invariant


def test_every_versioned_model_is_registered_or_exempt() -> None:
    missing = unregistered_models()
    assert not missing, (
        "these VersionedModel subclasses are neither registered as a matter-scoped "
        f"close store nor exempt: {sorted(m.__name__ for m in missing)}. Register the "
        "store in close.MATTER_SCOPED_STORES or add it to close.EXEMPT_MODELS with a "
        "reason."
    )


def test_registered_and_exempt_sets_are_disjoint() -> None:
    overlap = MATTER_SCOPED_MODELS & set(EXEMPT_MODELS)
    assert not overlap, f"models both scoped and exempt: {sorted(m.__name__ for m in overlap)}"


def test_exempt_reasons_are_nonempty() -> None:
    for model, reason in EXEMPT_MODELS.items():
        assert reason.strip(), f"exempt model {model.__name__} has no reason"


def test_enumeration_finds_known_models() -> None:
    names = {m.__name__ for m in concrete_versioned_models()}
    # A representative matter-scoped model and the exempt registry view must both appear,
    # proving the walk imports lazily-loaded model modules.
    assert {"Fact", "AccessAuditEntry", "MatterSummary", "CloseRecord"} <= names


def test_gate_flags_an_unregistered_model() -> None:
    """A brand-new VersionedModel that nobody registered is caught by the gate.

    Defining it here (module ``tests.*``) keeps it out of the product invariant above
    (which filters to ``mootloop``), but the classifier still rejects it — demonstrating
    that a real unregistered ``mootloop`` model would fail the gate.
    """

    class _UnregisteredDummy(VersionedModel):
        schema_version: str = "1.0"

    assert not is_registered(_UnregisteredDummy)
