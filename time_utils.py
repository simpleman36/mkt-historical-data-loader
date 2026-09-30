"""
Date and bar-size helpers shared by the loader and the CSV exporter.

- to_utc_datetime: parse ISO strings / datetimes into UTC-aware datetimes
- normalize_bar_size: turn int minutes or loose strings into IBKR bar sizes
- minutes_per_bar: bar size -> minutes (float)
- ibkr_duration_string: IBKR durationStr for one request, capped by max bars per call
  and by MAX_CHUNK_YEARS (10 Y)
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Union

DateLike = Union[str, datetime]

# Largest span requested in one reqHistoricalData call (e.g. 1 day bars are fetched 10 years at a time).
MAX_CHUNK_YEARS = 10


def to_utc_datetime(value: DateLike) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

    s = str(value).strip()
    if not s:
        raise ValueError("Date value is empty")

    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ibkr_duration_string(
    from_dt: datetime,
    to_dt: datetime,
    minutes_per_bar: float,
    max_bars_per_call: int,
) -> str:
    """
    Calculate duration string based on gap width, respecting IBKR's max bars limit
    and never exceeding MAX_CHUNK_YEARS per request.
    """
    gap_days = (to_dt - from_dt).days + 1
    # For intra-day gaps, gap_days may be 0, so ensure it's at least 1
    max_chunk_days = max(1, math.ceil((minutes_per_bar * max_bars_per_call) / 1440))
    chunk_days = min(max_chunk_days, gap_days, MAX_CHUNK_YEARS * 365)

    if chunk_days > 365:
        chunk_years = max(1, round(chunk_days / 365))
        return f"{chunk_years} Y"
    else:
        return f"{chunk_days} D"


def normalize_bar_size(timeframe: Union[str, int]) -> str:
    """Accept int minutes or IBKR-style strings like '5 secs', '5 mins', '1 hour', '1 day'."""
    if isinstance(timeframe, int):
        if timeframe <= 0:
            raise ValueError("timeframe (minutes) must be > 0")
        return f"{timeframe} min" if timeframe == 1 else f"{timeframe} mins"

    tf = str(timeframe).strip().lower()
    if re.fullmatch(r"\d+", tf):
        n = int(tf)
        return f"{n} min" if n == 1 else f"{n} mins"

    # Ensure proper formatting for seconds
    if re.search(r"(sec|second)", tf):
        match = re.fullmatch(r"(\d+)\s*(sec|secs|second|seconds)", tf)
        if match:
            n = int(match.group(1))
            return f"{n} secs"

    return tf


def minutes_per_bar(bar_size: str) -> float:
    """Return minutes per bar as a float (e.g., 30 secs = 0.5 minutes)."""
    m = re.fullmatch(
        r"(\d+)\s*(sec|secs|second|seconds|min|mins|minute|minutes|hour|hours|day|days|week|weeks)",
        bar_size.strip().lower(),
    )
    if not m:
        raise ValueError(f"Unsupported timeframe format: {bar_size!r}")

    n = int(m.group(1))
    unit = m.group(2)
    if unit.startswith("sec"):
        return n / 60.0
    if unit.startswith("min"):
        return float(n)
    if unit.startswith("hour"):
        return n * 60.0
    if unit.startswith("day"):
        return n * 1440.0
    if unit.startswith("week"):
        return n * 10080.0
    raise ValueError(f"Unsupported timeframe unit: {unit}")
