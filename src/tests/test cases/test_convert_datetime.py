import argparse
from datetime import datetime, timedelta, timezone

import pytest

from util.convert import datetime_to_utc, parse_utc_datetime


@pytest.mark.parametrize("value", [
    "2026-10-24T11:25:50Z",
    "2026-10-24T11:25:50",
    "2026-10-24 11:25:50",
    "2026-10-24T12:25:50+01:00",
    "2026-10-24T06:25:50-05:00",
])
def test_parse_datetime_normalises_to_utc(value):
    assert parse_utc_datetime(value) == datetime(2026, 10, 24, 11, 25, 50, tzinfo=timezone.utc)
    assert parse_utc_datetime(value).tzinfo is timezone.utc


def test_parse_preserves_fractional_seconds_and_crosses_date_boundary():
    assert parse_utc_datetime("2026-10-25T00:30:00.123456+01:00") == datetime(
        2026, 10, 24, 23, 30, 0, 123456, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", ["", "2026-10-24", "invalid", "2026-02-30T12:00:00", "2026-10-24T25:00:00"])
def test_invalid_datetime_raises_general_value_error(value):
    with pytest.raises(ValueError, match="ISO datetime") as exc:
        parse_utc_datetime(value)
    assert not isinstance(exc.value, argparse.ArgumentTypeError)


def test_utc_conversion_handles_naive_and_aware_datetimes():
    expected = datetime(2026, 10, 24, 11, 25, tzinfo=timezone.utc)
    assert datetime_to_utc(expected.replace(tzinfo=None)) == expected
    assert datetime_to_utc(datetime(2026, 10, 24, 13, 25, tzinfo=timezone(timedelta(hours=2)))) == expected


def test_parser_can_be_used_by_other_clis():
    parser = argparse.ArgumentParser()
    parser.add_argument("--observed-at", type=parse_utc_datetime)
    assert parser.parse_args(["--observed-at", "2026-10-24T11:00:00Z"]).observed_at.tzinfo is timezone.utc
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--observed-at", "bad"])
    assert exc.value.code == 2
