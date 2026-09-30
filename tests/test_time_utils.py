"""Characterisation tests: expected values were recorded from the pre-refactor history_range_loader.py."""
from datetime import datetime, timezone

import pytest

from time_utils import ibkr_duration_string, minutes_per_bar, normalize_bar_size, to_utc_datetime


@pytest.mark.parametrize("raw, expected", [
    (1, "1 min"), (5, "5 mins"), ("1", "1 min"), ("30", "30 mins"),
    ("5 secs", "5 secs"), ("5 sec", "5 secs"), ("30 seconds", "30 secs"),
    ("1 min", "1 min"), ("5 mins", "5 mins"), ("1 hour", "1 hour"),
    ("1 day", "1 day"), ("1 week", "1 week"), ("  5 MINS ", "5 mins"),
])
def test_normalize_bar_size(raw, expected):
    assert normalize_bar_size(raw) == expected


def test_normalize_bar_size_rejects_non_positive_int():
    with pytest.raises(ValueError):
        normalize_bar_size(0)


@pytest.mark.parametrize("bar, expected", [
    ("5 secs", 5 / 60), ("30 secs", 0.5), ("1 min", 1.0), ("5 mins", 5.0), ("30 mins", 30.0),
    ("1 hour", 60.0), ("1 day", 1440.0), ("1 week", 10080.0),
])
def test_minutes_per_bar(bar, expected):
    assert minutes_per_bar(bar) == pytest.approx(expected)


def test_minutes_per_bar_rejects_unknown():
    with pytest.raises(ValueError):
        minutes_per_bar("1 month")


@pytest.mark.parametrize("start, end, mpb, expected", [
    ("2024-01-01", "2024-01-01T12:00", 5 / 60, "1 D"),
    ("2024-01-01", "2024-01-01T12:00", 1440, "1 D"),
    ("2024-01-01", "2024-03-01", 1, "11 D"),
    ("2024-01-01", "2024-03-01", 5, "53 D"),
    ("2024-01-01", "2024-03-01", 60, "61 D"),
    ("2024-01-01", "2024-03-01", 1440, "61 D"),
    ("2000-01-01", "2026-01-01", 5 / 60, "1 D"),
    ("2000-01-01", "2026-01-01", 60, "2 Y"),
    ("2000-01-01", "2026-01-01", 1440, "10 Y"),   # capped at MAX_CHUNK_YEARS (was "26 Y" before the cap)
    ("2000-01-01", "2026-01-01", 10080, "10 Y"),
    ("2020-01-01", "2026-01-01", 1440, "6 Y"),
    ("2020-01-01", "2021-06-01", 60, "1 Y"),
    ("2020-01-01", "2021-06-01", 1440, "1 Y"),
])
def test_ibkr_duration_string(start, end, mpb, expected):
    # 15000 = max_bars_per_call in config.json when the baseline was recorded
    assert ibkr_duration_string(to_utc_datetime(start), to_utc_datetime(end), mpb, 15000) == expected


@pytest.mark.parametrize("raw, expected", [
    ("2024-01-01", "2024-01-01T00:00:00+00:00"),
    ("2024-01-01T08:00:00+02:00", "2024-01-01T06:00:00+00:00"),
    ("2024-01-01T08:00:00Z", "2024-01-01T08:00:00+00:00"),
])
def test_to_utc_datetime_strings(raw, expected):
    assert to_utc_datetime(raw).isoformat() == expected


def test_to_utc_datetime_naive_datetime_is_treated_as_utc():
    assert to_utc_datetime(datetime(2024, 1, 1, 8)) == datetime(2024, 1, 1, 8, tzinfo=timezone.utc)


def test_to_utc_datetime_rejects_empty():
    with pytest.raises(ValueError):
        to_utc_datetime("  ")
