from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Union

import pandas as pd
from ib_async import IB, Stock, util
from instruments import api_limitations_checker, get_group_stats
from e_logger import get_logger

logger = get_logger(__name__)

_config_path = os.path.join(os.path.dirname(__file__), "config.json")
with open(_config_path, "r", encoding="utf-8-sig") as f:
    _config_data = json.load(f)
_cfg = _config_data.get("config", {})
# Use first data_storage_type as defaults for backward compatibility with run() wrapper
_storage_types = _config_data.get("data_storage_types", [{}])
_defaults = _storage_types[0] if _storage_types else {}

CONFIG_DB_FOLDER_PATH = _cfg.get("database", {}).get("folder_path", "D:\\DB\\")
CONFIG_IBAPI_GROUP_STATS_HISTORICAL_NAME = _cfg.get("ibapi", {}).get("group_stats_historical_name", "IBAPI_HISTORICAL")
CONFIG_IBAPI_FORMAT_DATE = _cfg.get("ibapi", {}).get("format_date", 2)
CONFIG_IBAPI_REQ_HISTORY_DATA_TO = _cfg.get("ibapi", {}).get("request_history_data_timeout", 1000)
CONFIG_IBAPI_CONNECTION_HOST = _cfg.get("ibapi", {}).get("connection", {}).get("host", "127.0.0.1")
CONFIG_IBAPI_CONNECTION_PORT = _cfg.get("ibapi", {}).get("connection", {}).get("port", 4002)
CONFIG_IBAPI_CONNECTION_TO = _cfg.get("ibapi", {}).get("connection", {}).get("timeout", 10)
CONFIG_IBAPI_CONNECTION_RAISE_SYNC_ERR = _cfg.get("ibapi", {}).get("connection", {}).get("raise_sync_errors", True)
CONFIG_IBAPI_HISTORICAL_DATA_CLIENT_ID = _cfg.get("ibapi", {}).get("historical_data_client_id", 1)
CONFIG_MAX_BARS_PER_CALL = _cfg.get("ibapi", {}).get("max_bars_per_call", 15000)

# Parse rate limits config - convert string keys to ints
_rate_limits_cfg = _cfg.get("ibapi", {}).get("rate_limits", {})
_lims_raw = _rate_limits_cfg.get("lims", {1: 9, 60: 59})
CONFIG_IBAPI_LIMS = {int(k): v for k, v in _lims_raw.items()}

# ---------------------------------------------------------------------------
# Patch IB.reqHistoricalData with the same shared rate-limit group used by
# the rest of the project. Limits are read from config.json.
# Calling this once at import time patches the class globally.
# ---------------------------------------------------------------------------
api_limitations_checker(
    IB,
    ["reqHistoricalData"],
    group=CONFIG_IBAPI_GROUP_STATS_HISTORICAL_NAME,
    lims=CONFIG_IBAPI_LIMS,
)

DateLike = Union[str, datetime]


def _to_utc_dt(value: DateLike) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

    s = str(value).strip()
    if not s:
        raise ValueError("Date value is empty")

    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _calculate_duration_str(from_dt: datetime, to_dt: datetime, minutes_per_bar: float) -> str:
    """Calculate duration string based on gap width, respecting IBKR's max bars limit."""
    gap_days = (to_dt - from_dt).days + 1
    # For intra-day gaps, gap_days may be 0, so ensure it's at least 1
    max_chunk_days = max(1, math.ceil((minutes_per_bar * CONFIG_MAX_BARS_PER_CALL) / 1440))
    chunk_days = min(max_chunk_days, gap_days)

    if chunk_days > 365:
        chunk_years = max(1, round(chunk_days / 365))
        return f"{chunk_years} Y"
    else:
        return f"{chunk_days} D"


def _normalize_bar_size(timeframe: Union[str, int]) -> str:
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


def _minutes_per_bar(bar_size: str) -> float:
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


def _table_name(
    symbol: str,
    timeframe: str,
    use_rth: bool,
    what_to_show: str,
    exchange: str = "SMART",
    currency: str = "USD",
) -> str:
    raw = f"{symbol}_{exchange}_{what_to_show}_{currency}_{timeframe}_RTH_{str(use_rth).upper()}"
    return re.sub(r"[^A-Za-z0-9]", "_", raw).upper()


def _db_path_for_symbol(symbol: str) -> str:
    return os.path.join(CONFIG_DB_FOLDER_PATH, f"{symbol}.db")


def _get_existing_bounds(
    conn: sqlite3.Connection,
    table_name: str,
    from_dt: datetime,
    to_dt: datetime,
) -> tuple[datetime, datetime] | None:
    """
    Return (min_datetime, max_datetime) of rows already stored in *table_name*
    within [from_dt, to_dt], or None if the table has no rows in that window.
    Datetimes are returned as UTC-aware.
    """
    # Check table exists first
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    )
    if cur.fetchone() is None:
        return None

    from_ts = int(from_dt.timestamp())
    to_ts   = int(to_dt.timestamp())
    cur = conn.execute(
        f"SELECT MIN(timestamp), MAX(timestamp) FROM {table_name} "
        f"WHERE timestamp >= ? AND timestamp <= ?",
        (from_ts, to_ts),
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        return None

    min_dt = datetime.fromtimestamp(row[0], tz=timezone.utc)
    max_dt = datetime.fromtimestamp(row[1], tz=timezone.utc)
    return min_dt, max_dt


def _ensure_table(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            timestamp  UNSIGNED BIG INT NOT NULL PRIMARY KEY,
            datetime   TEXT,
            open       DECIMAL(10, 5),
            high       DECIMAL(10, 5),
            low        DECIMAL(10, 5),
            close      DECIMAL(10, 5),
            volume     INTEGER,
            average    DECIMAL(10, 5),
            barCount   INTEGER
        )
    """)
    conn.commit()


def _insert_rows(conn: sqlite3.Connection, table_name: str, df) -> int:
    if df.empty:
        return 0

    rows = [
        (
            int(r.timestamp),
            str(r.datetime),
            float(r.open),
            float(r.high),
            float(r.low),
            float(r.close),
            int(r.volume)   if r.volume   is not None else None,
            float(r.average) if r.average is not None else None,
            int(r.barCount) if r.barCount is not None else None,
        )
        for r in df.itertuples(index=False)
    ]

    before = conn.total_changes
    conn.executemany(
        f"""
        INSERT OR IGNORE INTO {table_name}
            (timestamp, datetime, open, high, low, close, volume, average, barCount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def _download_range(
    ib: "IB",
    conn: sqlite3.Connection,
    symbol: str,
    contract,
    table_name: str,
    from_dt: datetime,
    to_dt: datetime,
    bar_size: str,
    duration_str: str,
    whatToShow: str,
    useRTH: bool,
) -> tuple[int, int]:
    """
    Download bars backward from *to_dt* to *from_dt* and insert into DB.
    Returns (rows_inserted, request_count).
    """
    inserted_total = 0
    request_count  = 0
    cursor_end     = to_dt

    while cursor_end > from_dt:
        request_count += 1
        logger.info(f"[{symbol}] REQ #{request_count}: endDateTime={cursor_end.isoformat()} "
                    f"duration={duration_str} barSize={bar_size!r}")

        bars = ib.reqHistoricalData(
            contract       = contract,
            endDateTime    = cursor_end,
            durationStr    = duration_str,
            barSizeSetting = bar_size,
            whatToShow     = whatToShow,
            useRTH         = useRTH,
            formatDate     = CONFIG_IBAPI_FORMAT_DATE,
            timeout        = CONFIG_IBAPI_REQ_HISTORY_DATA_TO,
        )

        if not bars:
            logger.info(f"[{symbol}] No bars returned, stopping.")
            break

        df = util.df(bars)
        if df.empty:
            logger.info(f"[{symbol}] Empty DataFrame, stopping.")
            break

        # Normalize datetime column
        df["date"] = pd.to_datetime(df["date"])
        df["date"] = df["date"].dt.tz_localize(None) if df["date"].dt.tz is not None else df["date"]
        df = df.rename(columns={"date": "datetime"})
        df["datetime"]  = df["datetime"].astype("datetime64[ns]")
        df["timestamp"] = (df["datetime"].astype("int64") // 10 ** 9).astype("int64")

        # Clip to requested window
        from_naive = from_dt.replace(tzinfo=None)
        to_naive   = to_dt.replace(tzinfo=None)
        df = df[(df["datetime"] >= from_naive) & (df["datetime"] <= to_naive)]

        cols = ["timestamp", "datetime", "open", "high", "low", "close", "volume", "average", "barCount"]
        inserted = _insert_rows(conn, table_name, df[cols])
        inserted_total += inserted
        logger.info(f"[{symbol}]  → {len(df)} bars received, {inserted} new rows inserted → table '{table_name}'")
        logger.info(f"[{symbol}]  PERFORMANCE {get_group_stats(CONFIG_IBAPI_GROUP_STATS_HISTORICAL_NAME)}")

        oldest = df["datetime"].min() if not df.empty else None
        if oldest is None:
            break

        oldest_utc = oldest.to_pydatetime().replace(tzinfo=timezone.utc)
        if oldest_utc <= from_dt:
            break

        cursor_end = oldest_utc - timedelta(seconds=1)

    return inserted_total, request_count


def load_historical_range_to_sql(
    from_date: DateLike,
    to_date: DateLike,
    symbol: str,
    timeframe: Union[str, int],
    useRTH: bool,
    whatToShow: str,
    *,
    exchange: str = "SMART",
    currency: str = "USD",
    client_id: int = CONFIG_IBAPI_HISTORICAL_DATA_CLIENT_ID,
) -> dict:
    """
    Download IBKR historical OHLCV bars for the given [from_date, to_date] range
    and append them (deduped by timestamp) to a corresponding SQLite table.

    Args:
        from_date   : Start of the range – ISO string or datetime.
        to_date     : End of the range   – ISO string or datetime.
        symbol      : Ticker, e.g. 'AAPL'.
        timeframe   : Bar size – int minutes or IBKR string ('5 secs', '5 mins', '1 hour', '1 day').
        useRTH      : True = Regular Trading Hours only.
        whatToShow  : 'TRADES', 'MIDPOINT', 'BID', 'ASK', etc.
        exchange    : Default 'SMART'.
        currency    : Default 'USD'.
        client_id   : IBKR client id.

    Returns:
        dict with symbol, db_path, table_name, requests, rows_inserted, etc.
    """
    from_dt = _to_utc_dt(from_date)
    to_dt   = _to_utc_dt(to_date)
    if from_dt >= to_dt:
        raise ValueError("'from_date' must be earlier than 'to_date'")

    bar_size        = _normalize_bar_size(timeframe)
    minutes_per_bar = _minutes_per_bar(bar_size)

    table_name = _table_name(symbol, bar_size, useRTH, whatToShow, exchange=exchange, currency=currency)
    db_path    = _db_path_for_symbol(symbol)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    _ensure_table(conn, table_name)

    # ------------------------------------------------------------------
    # Gap detection: find which sub-ranges are NOT yet in the DB
    # ------------------------------------------------------------------
    bar_delta   = timedelta(seconds=minutes_per_bar * 60)
    bounds      = _get_existing_bounds(conn, table_name, from_dt, to_dt)

    if bounds is None:
        # No data at all in the requested window
        missing_ranges = [(from_dt, to_dt)]
        logger.info(f"[{symbol}] No existing data found in [{from_dt.date()}, {to_dt.date()}]. "
                    f"Full download required.")
    else:
        existing_min, existing_max = bounds
        missing_ranges = []

        # Gap before existing data
        if from_dt < existing_min - bar_delta:
            missing_ranges.append((from_dt, existing_min - timedelta(seconds=1)))
            logger.info(f"[{symbol}] Gap detected BEFORE existing data: "
                        f"[{from_dt.date()} → {(existing_min - timedelta(seconds=1)).date()}]")

        # Gap after existing data
        if existing_max + bar_delta < to_dt:
            missing_ranges.append((existing_max + timedelta(seconds=1), to_dt))
            logger.info(f"[{symbol}] Gap detected AFTER existing data: "
                        f"[{(existing_max + timedelta(seconds=1)).date()} → {to_dt.date()}]")

        if not missing_ranges:
            conn.close()
            logger.info(f"[{symbol}] Data already fully covered "
                        f"[{from_dt.date()} → {to_dt.date()}]. Skipping download.")
            return {
                "symbol"        : symbol,
                "db_path"       : db_path,
                "table_name"    : table_name,
                "requests"      : 0,
                "rows_inserted" : 0,
                "from_date"     : from_dt.isoformat(),
                "to_date"       : to_dt.isoformat(),
                "timeframe"     : bar_size,
                "useRTH"        : useRTH,
                "whatToShow"    : whatToShow,
                "skipped"       : True,
            }

    # ------------------------------------------------------------------
    # Download each missing sub-range
    # ------------------------------------------------------------------
    ib = IB()
    inserted_total = 0
    request_count  = 0

    logger.info(f"[{symbol}] Connecting to IBKR "
                f"{CONFIG_IBAPI_CONNECTION_HOST}:{CONFIG_IBAPI_CONNECTION_PORT} ...")

    try:
        ib.connect(
            host            = CONFIG_IBAPI_CONNECTION_HOST,
            port            = CONFIG_IBAPI_CONNECTION_PORT,
            clientId        = client_id,
            timeout         = CONFIG_IBAPI_CONNECTION_TO,
            raiseSyncErrors = CONFIG_IBAPI_CONNECTION_RAISE_SYNC_ERR,
        )

        contract = Stock(symbol, exchange, currency)

        for sub_from, sub_to in missing_ranges:
            gap_duration_str = _calculate_duration_str(sub_from, sub_to, minutes_per_bar)
            logger.info(f"[{symbol}] Downloading sub-range [{sub_from.date()} → {sub_to.date()}] (duration={gap_duration_str}) ...")
            ins, reqs = _download_range(
                ib, conn, symbol, contract, table_name,
                sub_from, sub_to, bar_size, gap_duration_str, whatToShow, useRTH,
            )
            inserted_total += ins
            request_count  += reqs

    finally:
        if ib.isConnected():
            ib.disconnect()
        conn.close()

    summary = {
        "symbol"        : symbol,
        "db_path"       : db_path,
        "table_name"    : table_name,
        "requests"      : request_count,
        "rows_inserted" : inserted_total,
        "from_date"     : from_dt.isoformat(),
        "to_date"       : to_dt.isoformat(),
        "timeframe"     : bar_size,
        "useRTH"        : useRTH,
        "whatToShow"    : whatToShow,
        "skipped"       : False,
    }
    logger.info(f"\n[{symbol}] Done. {inserted_total} total rows inserted in {request_count} request(s).")
    logger.info(f"  DB   : {db_path}")
    logger.info(f"  Table: {table_name}")
    return summary


# ---------------------------------------------------------------------------
# Convenient run() wrapper – callable directly from code
# ---------------------------------------------------------------------------
def run(
    from_date: DateLike,
    to_date: DateLike,
    symbol: str,
    timeframe: Union[str, int]  = None,
    useRTH: bool                = None,
    whatToShow: str             = None,
    *,
    exchange: str               = None,
    currency: str               = None,
    client_id: int              = None,
) -> dict:
    """
    Thin wrapper around load_historical_range_to_sql with config-based defaults.

    Example (from another module)::

        from history_range_loader import run
        result = run("2025-01-01", "2025-12-31", "AAPL", timeframe="5 secs", useRTH=True)
        result = run("2025-01-01", "2025-12-31", "AAPL", timeframe="5 mins", useRTH=True)
    """
    return load_historical_range_to_sql(
        from_date  = from_date,
        to_date    = to_date,
        symbol     = symbol,
        timeframe  = timeframe if timeframe is not None else _defaults.get("timeframe", 10),
        useRTH     = useRTH if useRTH is not None else _defaults.get("useRTH", False),
        whatToShow = whatToShow if whatToShow is not None else _defaults.get("whatToShow", "TRADES"),
        exchange   = exchange if exchange is not None else _defaults.get("exchange", "SMART"),
        currency   = currency if currency is not None else _defaults.get("currency", "USD"),
        client_id  = client_id if client_id is not None else CONFIG_IBAPI_HISTORICAL_DATA_CLIENT_ID,
    )


# ---------------------------------------------------------------------------
# Batch loader – reads config.json and downloads data for every symbol
# ---------------------------------------------------------------------------
_DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def run_from_config(config_path: str = _DEFAULT_CONFIG_PATH) -> list[dict]:
    """
    Load all symbols defined in a JSON config file, processing each symbol with all data_storage_types.

    The JSON schema is::

        {
            "data_storage_types": [
                {
                    "from_date":  "2024-01-03",
                    "to_date":    "2026-08-14",
                    "timeframe":  "5 secs",
                    "useRTH":     false,
                    "whatToShow": "TRADES",
                    "exchange":   "SMART",
                    "currency":   "USD"
                },
                {
                    "from_date":  "2024-01-03",
                    "to_date":    "2026-08-14",
                    "timeframe":  "1 hour",
                    "useRTH":     false,
                    "whatToShow": "TRADES",
                    "exchange":   "SMART",
                    "currency":   "USD"
                }
            ],
            "stocks": [
                { "symbol": "AAPL" },
                { "symbol": "MSFT", "from_date": "2010-01-01" },
                ...
            ]
        }

    Each stock is processed with every data_storage_type configuration.
    Per-symbol keys override the data_storage_type defaults.

    Returns a list of summary dicts (one per symbol-storage_type combination).
    """
    with open(config_path, "r", encoding="utf-8-sig") as fh:
        cfg_data = json.load(fh)

    storage_types = cfg_data.get("data_storage_types", [])
    stocks        = cfg_data.get("stocks", [])

    if not storage_types:
        logger.error("[config] No 'data_storage_types' defined in config. Nothing to process.")
        return []

    results = []
    total_tasks = len(stocks) * len(storage_types)
    task_num = 0

    for stock_idx, stock_entry in enumerate(stocks, start=1):
        symbol = stock_entry.get("symbol")
        if not symbol:
            logger.warning(f"[config] Skipping stock entry #{stock_idx}: missing 'symbol' key.")
            continue

        for storage_idx, storage_type in enumerate(storage_types, start=1):
            task_num += 1
            logger.info(
                f"\n[config] [{task_num}/{total_tasks}] {symbol} "
                f"(timeframe={storage_type.get('timeframe')}, "
                f"whatToShow={storage_type.get('whatToShow')}) ..."
            )

            # Merge stock-level overrides with storage_type defaults
            kwargs = {
                "from_date" : stock_entry.get("from_date",  storage_type.get("from_date",  "2001-01-01")),
                "to_date"   : stock_entry.get("to_date",    storage_type.get("to_date",    "2026-08-16")),
                "timeframe" : stock_entry.get("timeframe",  storage_type.get("timeframe",  "1 hour")),
                "useRTH"    : stock_entry.get("useRTH",     storage_type.get("useRTH",     False)),
                "whatToShow": stock_entry.get("whatToShow", storage_type.get("whatToShow", "TRADES")),
                "exchange"  : stock_entry.get("exchange",   storage_type.get("exchange",   "SMART")),
                "currency"  : stock_entry.get("currency",   storage_type.get("currency",   "USD")),
            }

            try:
                result = run(symbol=symbol, **kwargs)
                results.append(result)
            except Exception as exc:
                logger.error(f"[config] ERROR for {symbol} (timeframe={kwargs['timeframe']}): {exc}")
                results.append({
                    "symbol": symbol,
                    "timeframe": kwargs["timeframe"],
                    "whatToShow": kwargs["whatToShow"],
                    "error": str(exc)
                })

    logger.info(f"\n[config] Batch complete. {len(results)}/{total_tasks} tasks processed.")
    return results


def run_all(config_path: str = _DEFAULT_CONFIG_PATH) -> list[dict]:
    """Alias for run_from_config – download all stocks listed in config.json."""
    return run_from_config(config_path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Load IBKR historical data range into a per-symbol SQLite table."
    )
    parser.add_argument("--from",       dest="from_date",  help="Start date (ISO 8601)")
    parser.add_argument("--to",         dest="to_date",    help="End date   (ISO 8601)")
    parser.add_argument("--symbol",                        help="Ticker symbol, e.g. AAPL")
    parser.add_argument("--timeframe",                     help="Bar size: '5 secs' / '1 min' / '5 mins' / '1 hour' / '1 day'")
    parser.add_argument("--useRTH",     choices=["true", "false"], help="Regular Trading Hours only")
    parser.add_argument("--whatToShow",                    help="TRADES, MIDPOINT, BID, ASK …")
    parser.add_argument("--exchange",   default="SMART",   help="Exchange (default: SMART)")
    parser.add_argument("--currency",   default="USD",     help="Currency  (default: USD)")
    parser.add_argument("--config",     default=_DEFAULT_CONFIG_PATH,
                        help="Path to config.json (used when --symbol is omitted)")
    args = parser.parse_args()

    if args.symbol:
        # Single-symbol mode
        result = run(
            from_date   = args.from_date  or "2001-01-01",
            to_date     = args.to_date    or "2026-08-16",
            symbol      = args.symbol,
            timeframe   = args.timeframe  or "1 hour",
            useRTH      = (args.useRTH.lower() == "true") if args.useRTH else False,
            whatToShow  = args.whatToShow or "TRADES",
            exchange    = args.exchange,
            currency    = args.currency,
        )
        logger.info(f"\nSummary: {result}")
    else:
        # Batch mode – continuous loop, reloading config and processing all stocks
        cycle_num = 0

        try:
            while True:
                cycle_num += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"CYCLE #{cycle_num}: Loading config and starting batch download...")
                logger.info(f"{'='*80}")

                try:
                    results = run_from_config(args.config)
                    logger.info(f"\nCycle #{cycle_num} summary: {len(results)} tasks processed.")
                except Exception as cycle_exc:
                    logger.error(f"Cycle #{cycle_num} failed with error: {cycle_exc}")

                logger.info(f"Cycle #{cycle_num} complete. Waiting 60 seconds before reloading config...")
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("\n" + "="*80)
            logger.info("Script interrupted by user. Exiting.")
            logger.info("="*80)
