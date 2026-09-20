import pytest

from mootloop.demo_sources import validate_source_packet
from tests.unit.test_demo_models import source


def test_packet_needs_eligible_primary_record_not_just_outcome():
    from datetime import date

    with pytest.raises(ValueError, match="eligible"):
        validate_source_packet([source(classification="outcome")], date(2020, 2, 1))


def test_packet_rejects_duplicate_id():
    from datetime import date

    with pytest.raises(ValueError, match="duplicate"):
        validate_source_packet([source(), source()], date(2020, 2, 1))
