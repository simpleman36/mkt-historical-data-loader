"""Test US market holidays and trading day logic."""
from datetime import datetime, timezone

import pytest

import holidays
import sqlite_storage

UTC = timezone.utc


def utc_date(*a):
    return datetime(*a, tzinfo=UTC)


@pytest.fixture
def cfg_db(tmp_path):
    """Config DB with holidays table."""
    conn = sqlite_storage.open_connection(str(tmp_path / "__config.db"))
    holidays.ensure_holidays_table(conn)
    yield conn
    conn.close()


def test_ensure_holidays_table_creates_if_missing(tmp_path):
    conn = sqlite_storage.open_connection(str(tmp_path / "X.db"))
    holidays.ensure_holidays_table(conn, "holidays")
    rows = conn.execute("PRAGMA table_info(holidays)").fetchall()
    conn.close()
    assert len(rows) == 4
    assert rows[0][1] == "date"


def test_is_trading_day_rejects_weekends(cfg_db):
    # 2024-01-06 is Saturday
    assert not holidays.is_trading_day(cfg_db, utc_date(2024, 1, 6))
    # 2024-01-07 is Sunday
    assert not holidays.is_trading_day(cfg_db, utc_date(2024, 1, 7))
    # 2024-01-05 is Friday
    assert holidays.is_trading_day(cfg_db, utc_date(2024, 1, 5))


def test_is_trading_day_rejects_holidays(cfg_db):
    # Insert a holiday
    cfg_db.execute("INSERT INTO us_holidays (date, name, year) VALUES (?, ?, ?)",
                   ("2024-12-25", "Christmas", 2024))
    cfg_db.commit()
    assert not holidays.is_trading_day(cfg_db, utc_date(2024, 12, 25))
    # Day after Christmas
    assert holidays.is_trading_day(cfg_db, utc_date(2024, 12, 26))


def test_correct_date_to_trading_day_backward(cfg_db):
    # 2024-01-06 is Saturday; backward should give Friday 2024-01-05
    sat = utc_date(2024, 1, 6)
    corrected = holidays.correct_date_to_trading_day(cfg_db, sat, direction=-1)
    assert corrected.date().isoformat() == "2024-01-05"


def test_correct_date_to_trading_day_forward(cfg_db):
    # 2024-01-06 is Saturday; forward should give Monday 2024-01-08
    sat = utc_date(2024, 1, 6)
    corrected = holidays.correct_date_to_trading_day(cfg_db, sat, direction=+1)
    assert corrected.date().isoformat() == "2024-01-08"


def test_correct_date_range(cfg_db):
    # Insert Christmas 2024 holiday
    cfg_db.execute("INSERT INTO us_holidays (date, name, year) VALUES (?, ?, ?)",
                   ("2024-12-25", "Christmas", 2024))
    cfg_db.commit()

    from_date = utc_date(2024, 12, 21)  # Saturday
    to_date = utc_date(2024, 12, 27)    # Friday after Christmas
    from_corrected, to_corrected = holidays.correct_date_range(cfg_db, from_date, to_date)

    # Saturday 12-21 -> Monday 12-23
    assert from_corrected.date().isoformat() == "2024-12-23"
    # Friday 12-27 -> Thursday 12-26 (skip Christmas 12-25)
    assert to_corrected.date().isoformat() == "2024-12-26"


def test_correct_to_date_for_daily_bars_moves_today_backward(cfg_db):
    today = utc_date(2024, 1, 5, 12)  # Friday
    # Insert Monday as a holiday so backward search lands on Friday of previous week
    cfg_db.execute("INSERT INTO us_holidays (date, name, year) VALUES (?, ?, ?)",
                   ("2024-01-01", "New Year", 2024))
    cfg_db.commit()

    corrected = holidays.correct_to_date_for_daily_bars(cfg_db, today, current_date=today)
    # Today is 2024-01-05, should move to 2024-01-04 (Thursday)
    assert corrected.date().isoformat() == "2024-01-04"


def test_correct_to_date_for_daily_bars_accepts_past_date(cfg_db):
    today = utc_date(2024, 1, 5)
    past = utc_date(2024, 1, 3)  # Wednesday, well in the past
    corrected = holidays.correct_to_date_for_daily_bars(cfg_db, past, current_date=today)
    assert corrected == past  # No change, it's in the past


def test_correct_to_date_for_daily_bars_rejects_future_date(cfg_db):
    today = utc_date(2024, 1, 5)
    future = utc_date(2024, 1, 10)  # Wednesday next week
    corrected = holidays.correct_to_date_for_daily_bars(cfg_db, future, current_date=today)
    # Should move to a past trading day
    assert corrected < future
