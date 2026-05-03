from __future__ import annotations

import pytest

from pravohelp.utils.rate_limit import check_and_record, reset_all


@pytest.fixture(autouse=True)
def _clean():
    reset_all()
    yield
    reset_all()


def test_allows_under_limit():
    for _ in range(5):
        allowed, retry = check_and_record(123, max_per_hour=5)
        assert allowed
        assert retry == 0


def test_blocks_over_limit():
    for _ in range(5):
        check_and_record(123, max_per_hour=5)

    allowed, retry = check_and_record(123, max_per_hour=5)
    assert not allowed
    assert retry > 0


def test_per_user_isolation():
    for _ in range(5):
        check_and_record(111, max_per_hour=5)

    allowed, _ = check_and_record(222, max_per_hour=5)
    assert allowed
