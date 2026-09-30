"""
US stock exchange holidays and trading day validation.

Fetches US market holidays from randomapi.dev and caches them in the config DB.
Provides utilities to check trading days and correct dates to the nearest trading day.

US exchanges are closed on: weekends + holidays (Christmas, Thanksgiving, Independence Day, etc.).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from app_logging import module_logger

logger = module_logger(__name__)

# Holiday API
RANDOM_API_HOLIDAYS_URL = "https://randomapi.dev/api/holidays"


def ensure_holidays_table(conn: sqlite3.Connection, table: str = "us_holidays") -> None:
    """Create the US holidays table if it doesn't exist."""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            date          TEXT NOT NULL UNIQUE,
            name          TEXT,
            year          INTEGER,
            cached_at     TEXT
        )
    """)
    conn.commit()


def _fetch_holidays_from_api(year: int) -> list[dict]:
    """Fetch US market holidays for a given year from randomapi.dev."""
    try:
        params = {"country": "US", "year": str(year), "type": "any"}
        resp = requests.get(RANDOM_API_HOLIDAYS_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # Extract holidays from data field
        holidays_list = data.get("data", [])
        holidays = [{"date": h.get("date"), "name": h.get("name", "")} for h in holidays_list]
        logger.info(f"Fetched {len(holidays)} US holidays for {year}")
        return holidays
    except Exception as exc:
        logger.error(f"Failed to fetch holidays for {year}: {exc}")
        return []


def cache_holidays_for_range(conn: sqlite3.Connection, years_to_cache: int = 100,
                             table: str = "us_holidays") -> None:
    """
    Fetch and cache US market holidays for the specified number of years + current year.
    This should be called once at startup to pre-populate the holidays table.
    """
    current_year = datetime.now(timezone.utc).year
    years = list(range(current_year - years_to_cache, current_year + 1))
    logger.info(f"Pre-caching holidays for years {years[0]}-{years[-1]} ({len(years)} years)")
    cache_holidays(conn, years, table=table)


def cache_holidays(conn: sqlite3.Connection, years: list[int], table: str = "us_holidays") -> None:
    """
    Fetch and cache US market holidays for given years in the config DB.
    Uses randomapi.dev to fetch holidays.
    """
    ensure_holidays_table(conn, table)
    now = datetime.now(timezone.utc).isoformat()

    for year in years:
        # Check if year is already cached
        existing = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE year = ?", (year,)
        ).fetchone()[0]
        if existing > 0:
            logger.debug(f"Holidays for {year} already cached")
            continue

        holidays = _fetch_holidays_from_api(year)
        if not holidays:
            continue

        rows = [(h["date"], h.get("name", ""), year, now) for h in holidays]
        conn.executemany(
            f"INSERT OR IGNORE INTO {table} (date, name, year, cached_at) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()


def is_trading_day(
    conn: sqlite3.Connection,
    date: datetime,
    table: str = "us_holidays",
) -> bool:
    """
    Check if a date is a US market trading day (not a weekend or holiday).
    date should be a UTC-aware datetime.
    """
    if date.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    date_str = date.date().isoformat()
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE date = ?", (date_str,)
    ).fetchone()
    return row is None  # trading day if NOT in holidays table


def _find_working_day(
    conn: sqlite3.Connection,
    start: datetime,
    direction: int = -1,
    max_days: int = 30,
    table: str = "us_holidays",
) -> Optional[datetime]:
    """
    Find the nearest working day starting from `start`, going in `direction`
    (-1 = backward, +1 = forward). Returns None if no trading day found within max_days.
    """
    current = start
    for _ in range(max_days):
        if is_trading_day(conn, current, table):
            return current
        current += timedelta(days=direction)
    return None


def correct_date_to_trading_day(
    conn: sqlite3.Connection,
    date: datetime,
    direction: int = -1,
    table: str = "us_holidays",
) -> datetime:
    """
    Correct a date to the nearest trading day in the given direction.
    direction: -1 = backward (default), +1 = forward.
    Returns the corrected date, or the original if already a trading day.
    """
    if is_trading_day(conn, date, table):
        return date

    corrected = _find_working_day(conn, date, direction, table=table)
    if corrected is None:
        logger.warning(f"Could not find working day near {date.date()} in direction {direction}")
        return date
    return corrected


def correct_date_range(
    conn: sqlite3.Connection,
    from_date: datetime,
    to_date: datetime,
    table: str = "us_holidays",
) -> tuple[datetime, datetime]:
    """
    Correct from_date and to_date to the nearest trading days.
    from_date is moved forward to the nearest trading day (>= from_date).
    to_date is moved backward to the nearest trading day (<= to_date).
    Returns the corrected (from_date, to_date) tuple.
    """
    from_corrected = correct_date_to_trading_day(conn, from_date, direction=+1, table=table)
    to_corrected = correct_date_to_trading_day(conn, to_date, direction=-1, table=table)

    if from_corrected >= to_corrected:
        logger.warning(f"After holiday correction, from_date {from_corrected.date()} >= to_date "
                       f"{to_corrected.date()}. Range is invalid.")

    return from_corrected, to_corrected


def correct_to_date_for_daily_bars(
    conn: sqlite3.Connection,
    to_date: datetime,
    current_date: Optional[datetime] = None,
    table: str = "us_holidays",
) -> datetime:
    """
    For 1-day bars, to_date must not be today or in the future (data only available after market close).

    Returns:
    - datetime: corrected to_date (moved to previous trading day)

    If to_date is today or in the future, it's moved to the previous trading day.
    If to_date is a past date but falls on a holiday/weekend, it's moved to the previous trading day.

    current_date defaults to today (UTC).
    """
    if current_date is None:
        current_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # If to_date is today or in the future, use latest available (previous trading day)
    if to_date.date() >= current_date.date():
        logger.info(f"to_date {to_date.date()} is today or future; using latest available (previous trading day)")
        to_date = correct_date_to_trading_day(conn, current_date - timedelta(days=1), direction=-1, table=table)
        return to_date

    # If to_date is a past date but falls on a holiday/weekend, move to previous trading day
    if not is_trading_day(conn, to_date, table):
        corrected = correct_date_to_trading_day(conn, to_date, direction=-1, table=table)
        logger.info(f"to_date {to_date.date()} is holiday/weekend; adjusted to {corrected.date()}")
        return corrected

    return to_date
