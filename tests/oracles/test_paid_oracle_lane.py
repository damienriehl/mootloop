"""Self-test for the explicit paid-oracle lane; it uses no live provider."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.paid_oracle


def test_paid_oracle_lane_requires_explicit_authorization(
    paid_oracles_authorized: bool,
) -> None:
    assert paid_oracles_authorized
