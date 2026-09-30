"""
db_to_csv.py
============
Export OHLCV data from SQLite databases to CSV files.

Reads historical data from per-symbol SQLite databases (configured in data-loader/config.json)
and converts to CSV format. Database files are stored in the folder specified by:
    config.json → config.database.folder_path (default: D:\\DB\\)

Each symbol's data is stored in: {database_folder}/{SYMBOL}.db

USAGE FROM PYTHON CODE
======================

    from data_loader.db_to_csv import export_symbol_to_csv, export_all_to_csv

    # Export single symbol to CSV (filename auto-generated from table name)
    export_symbol_to_csv(
        symbol="AAPL",
        from_date="2024-01-01",
        to_date="2024-12-31"
        # Output: AAPL_SMART_TRADES_USD_1_MIN_RTH_FALSE.csv
    )

    # Export with custom timeframe (1 hour)
    export_symbol_to_csv(
        symbol="AAPL",
        from_date="2024-01-01",
        to_date="2024-12-31",
        timeframe="1 hour"
        # Output: AAPL_SMART_TRADES_USD_1_HOUR_RTH_FALSE.csv
    )

    # Export all symbols in database to CSV files (one per symbol)
    results = export_all_to_csv(
        from_date="2024-01-01",
        to_date="2024-12-31",
        output_dir="./csv_exports/"
    )
    # Returns: {'AAPL': True, 'MSFT': True, ...} indicating success/failure per symbol

    # Export with custom parameters
    export_symbol_to_csv(
        symbol="NVDA",
        from_date="2026-01-01",
        to_date="2026-08-31",
        timeframe="1 hour",
        use_rth=False,  # Extended hours
        what_to_show="TRADES"
        # Output: NVDA_SMART_TRADES_USD_1_HOUR_RTH_FALSE.csv
    )

    # Specify custom output filename if desired
    export_symbol_to_csv(
        symbol="TSLA",
        from_date="2024-01-01",
        to_date="2024-12-31",
        output_file="my_custom_filename.csv"
    )


USAGE FROM COMMAND LINE
=======================

    # Export single symbol (filename auto-generated from table name)
    python data-loader/db_to_csv.py --symbol AAPL --from 2024-01-01 --to 2024-12-31
    # Output: AAPL_SMART_TRADES_USD_1_MIN_RTH_FALSE.csv

    # Export with custom output filename
    python data-loader/db_to_csv.py --symbol AAPL --from 2024-01-01 --to 2024-12-31 \\
        --output my_custom_name.csv

    # Export all symbols to directory (creates CSV file per symbol with table name)
    python data-loader/db_to_csv.py --all --from 2024-01-01 --to 2024-12-31 \\
        --output-dir ./csv_exports/

    # Export specific timeframe (1 hour, instead of default 1 min)
    python data-loader/db_to_csv.py --symbol AAPL --from 2024-01-01 --to 2024-12-31 \\
        --timeframe "1 hour"
    # Output: AAPL_SMART_TRADES_USD_1_HOUR_RTH_FALSE.csv

    # Export 5 minute bars
    python data-loader/db_to_csv.py --symbol MSFT --from 2024-01-01 --to 2024-12-31 \\
        --timeframe "5 mins"
    # Output: MSFT_SMART_TRADES_USD_5_MINS_RTH_FALSE.csv

    # Export with extended hours (RTH=False)
    python data-loader/db_to_csv.py --symbol AAPL --from 2024-01-01 --to 2024-12-31 \\
        --use-rth false
    # Output: AAPL_SMART_TRADES_USD_1_MIN_RTH_FALSE.csv

    # List available symbols in database
    python data-loader/db_reader.py --scan


OUTPUT CSV FORMAT
=================

Exported CSV files contain the following columns (in order):
    - timestamp        : Unix timestamp (seconds since epoch)
    - datetime         : ISO 8601 datetime (UTC)
    - open             : Opening price
    - high             : Highest price in bar
    - low              : Lowest price in bar
    - close            : Closing price
    - volume           : Trading volume
    - average          : Average price (VWAP)
    - barCount         : Number of trades in bar

Example output (AAPL_SMART_TRADES_USD_1_MIN_RTH_FALSE.csv):
    timestamp,datetime,open,high,low,close,volume,average,barCount
    1704067200,2024-01-01T00:00:00,150.45,150.89,150.23,150.67,10000,150.56,423
    1704067260,2024-01-01T00:01:00,150.67,150.92,150.44,150.82,9500,150.68,401
    1704067320,2024-01-01T00:02:00,150.81,151.23,150.65,150.95,12000,150.81,523


CONFIGURATION
=============

Database path is read from: data-loader/config.json
    → config.database.folder_path (currently: D:\\DB\\)

To change database location:
    1. Edit data-loader/config.json
    2. Update the "folder_path" value under config.database
    3. Ensure the folder exists and contains your symbol .db files

Default parameters:
    - timeframe        : 1 min (1 minute bars)
    - useRTH           : false (extended hours included)
    - whatToShow       : TRADES
    - exchange         : SMART
    - currency         : USD

These can be overridden via CLI arguments or function parameters.


EXAMPLES WITH ACTUAL DATA
==========================

    # Export all symbols at 1 hour timeframe (default is 1 min)
    python data-loader/db_to_csv.py --all --from 2024-01-01 --to 2024-12-31 \\
        --timeframe "1 hour" --output-dir ./tech_data_1h/

    # Export single day for day trading analysis (5 min bars)
    python data-loader/db_to_csv.py --symbol CLSK --from 2026-08-15 --to 2026-08-15 \\
        --timeframe "5 mins"
    # Output: CLSK_SMART_TRADES_USD_5_MINS_RTH_FALSE.csv

    # Export quarter of data for backtesting (default 1 min bars)
    python data-loader/db_to_csv.py --symbol TSLA --from 2026-01-01 --to 2026-03-31
    # Output: TSLA_SMART_TRADES_USD_1_MIN_RTH_FALSE.csv

    # Export with custom filename
    python data-loader/db_to_csv.py --symbol NVDA --from 2026-01-01 --to 2026-08-31 \\
        --output "NVDA_Q1_Q2_2026.csv"
"""
from __future__ import annotations

import json
import os
import sys
import re
import sqlite3
from datetime import datetime, timezone
from typing import Union, Optional
from pathlib import Path

import pandas as pd

# Add parent directory to path for imports
_data_loader_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_data_loader_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

# Load config
_config_path = os.path.join(_data_loader_dir, "config.json")
with open(_config_path, "r", encoding="utf-8-sig") as f:
    _config_data = json.load(f)
_cfg = _config_data.get("config", {})
_defaults = _config_data.get("defaults", {})

CONFIG_DB_FOLDER_PATH = _cfg.get("database", {}).get("folder_path", "D:\\DB\\")
DEFAULT_TIMEFRAME = "1 min"  # Changed from 10 minutes

DateLike = Union[str, datetime]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_utc_dt(value: DateLike) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        raise ValueError("Date value is empty")
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _normalize_bar_size(timeframe: Union[str, int]) -> str:
    """Accept int minutes or IBKR-style strings like '5 mins', '1 hour', '1 day'."""
    if isinstance(timeframe, int):
        if timeframe <= 0:
            raise ValueError("timeframe (minutes) must be > 0")
        return f"{timeframe} min" if timeframe == 1 else f"{timeframe} mins"
    tf = str(timeframe).strip().lower()
    if re.fullmatch(r"\d+", tf):
        n = int(tf)
        return f"{n} min" if n == 1 else f"{n} mins"
    return tf


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


def scan_available_symbols(db_folder: Optional[str] = None) -> list[str]:
    """Return sorted list of ticker symbols that have a .db file."""
    folder = db_folder or CONFIG_DB_FOLDER_PATH
    if not os.path.isdir(folder):
        print(f"[scan] DB folder not found: {folder}")
        return []
    symbols = sorted(
        f[:-3]  # strip ".db"
        for f in os.listdir(folder)
        if f.lower().endswith(".db") and not f.startswith("_")
    )
    print(f"[scan] Found {len(symbols)} symbol DB(s) in {folder}")
    return symbols


# ---------------------------------------------------------------------------
# Main export functions
# ---------------------------------------------------------------------------

def export_symbol_to_csv(
    symbol: str,
    from_date: DateLike,
    to_date: DateLike,
    output_file: str = None,
    timeframe: Union[str, int] = None,
    use_rth: bool = None,
    what_to_show: str = None,
    exchange: str = None,
    currency: str = None,
) -> bool:
    """
    Export OHLCV data for a single symbol from SQLite to CSV.

    Parameters
    ----------
    symbol          : Ticker, e.g. 'AAPL'
    from_date       : Start date (ISO string or datetime)
    to_date         : End date (ISO string or datetime)
    output_file     : Path to output CSV file (if None, uses table name: SYMBOL_EXCHANGE_WHAT_TO_SHOW_CURRENCY_TIMEFRAME_RTH_[TRUE|FALSE].csv)
    timeframe       : Bar size (int minutes or IBKR string; default: '1 min')
    use_rth         : Regular Trading Hours only (default: False)
    what_to_show    : 'TRADES', 'MIDPOINT', 'BID', 'ASK', etc. (default: 'TRADES')
    exchange        : Exchange name (default 'SMART')
    currency        : Currency code (default 'USD')

    Returns
    -------
    bool  – True if successful, False otherwise
    """
    if timeframe is None:
        timeframe = DEFAULT_TIMEFRAME
    if use_rth is None:
        use_rth = _defaults.get("useRTH", False)
    if what_to_show is None:
        what_to_show = _defaults.get("whatToShow", "TRADES")
    if exchange is None:
        exchange = _defaults.get("exchange", "SMART")
    if currency is None:
        currency = _defaults.get("currency", "USD")

    from_dt = _to_utc_dt(from_date)
    to_dt = _to_utc_dt(to_date)
    if from_dt >= to_dt:
        raise ValueError("'from_date' must be earlier than 'to_date'")

    bar_size = _normalize_bar_size(timeframe)
    tbl = _table_name(symbol, bar_size, use_rth, what_to_show, exchange, currency)
    db_path = _db_path_for_symbol(symbol)

    # Generate output filename from table name if not provided
    if output_file is None:
        output_file = f"{tbl}.csv"

    if not os.path.isfile(db_path):
        print(f"[{symbol}] ERROR: Database not found: {db_path}")
        return False

    from_ts = int(from_dt.timestamp())
    to_ts = int(to_dt.timestamp())

    conn = sqlite3.connect(db_path)
    try:
        # Verify table exists
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
        )
        if cur.fetchone() is None:
            print(f"[{symbol}] ERROR: Table '{tbl}' not found in {db_path}")
            return False

        query = f"""
            SELECT timestamp, datetime, open, high, low, close, volume, average, barCount
            FROM   {tbl}
            WHERE  timestamp >= ? AND timestamp <= ?
            ORDER  BY timestamp ASC
        """
        df = pd.read_sql_query(query, conn, params=(from_ts, to_ts))
    finally:
        conn.close()

    if df.empty:
        print(f"[{symbol}] ERROR: No data found in '{tbl}' for [{from_dt.date()} → {to_dt.date()}]")
        return False

    # Convert datetime column to proper dtype
    df["datetime"] = pd.to_datetime(df["datetime"])

    # Create output directory if needed
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Export to CSV
    df.to_csv(output_file, index=False)
    print(f"[{symbol}] SUCCESS: Exported {len(df)} rows to {output_file}")
    return True


def export_all_to_csv(
    from_date: DateLike,
    to_date: DateLike,
    output_dir: str = None,
    timeframe: Union[str, int] = None,
    use_rth: bool = None,
    what_to_show: str = None,
    exchange: str = None,
    currency: str = None,
    db_folder: Optional[str] = None,
) -> dict[str, bool]:
    """
    Export OHLCV data for all available symbols to CSV files.

    Each symbol gets its own CSV file named {TABLE_NAME}.csv in output_dir,
    where TABLE_NAME corresponds to the database table name.

    Parameters
    ----------
    from_date       : Start date (ISO string or datetime)
    to_date         : End date (ISO string or datetime)
    output_dir      : Directory to write CSV files (default: './csv_exports/')
    timeframe       : Bar size (int minutes or IBKR string; default: '1 min')
    use_rth         : Regular Trading Hours only (default: False)
    what_to_show    : 'TRADES', 'MIDPOINT', 'BID', 'ASK', etc. (default: 'TRADES')
    exchange        : Exchange name (default 'SMART')
    currency        : Currency code (default 'USD')
    db_folder       : Override CONFIG_DB_FOLDER_PATH

    Returns
    -------
    dict[str, bool]  – {symbol: success_status} for each symbol
    """
    if output_dir is None:
        output_dir = "./csv_exports/"
    if timeframe is None:
        timeframe = DEFAULT_TIMEFRAME
    if use_rth is None:
        use_rth = _defaults.get("useRTH", False)
    if what_to_show is None:
        what_to_show = _defaults.get("whatToShow", "TRADES")
    if exchange is None:
        exchange = _defaults.get("exchange", "SMART")
    if currency is None:
        currency = _defaults.get("currency", "USD")

    symbols = scan_available_symbols(db_folder)
    if not symbols:
        print("[export_all] No symbols found to export.")
        return {}

    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    results = {}
    for i, symbol in enumerate(symbols, 1):
        output_file = os.path.join(output_dir, f"{symbol}.csv")
        print(f"\n[{i}/{len(symbols)}] Exporting {symbol}...")
        success = export_symbol_to_csv(
            symbol=symbol,
            from_date=from_date,
            to_date=to_date,
            output_file=output_file,
            timeframe=timeframe,
            use_rth=use_rth,
            what_to_show=what_to_show,
            exchange=exchange,
            currency=currency,
        )
        results[symbol] = success

    success_count = sum(1 for v in results.values() if v)
    print(f"\n[export_all] Completed: {success_count}/{len(symbols)} symbols exported to {output_dir}")
    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Export OHLCV data from SQLite databases to CSV.",
        epilog="Output filenames are generated from database table names. "
               "Table name format: SYMBOL_EXCHANGE_WHAT_TO_SHOW_CURRENCY_TIMEFRAME_RTH_[TRUE|FALSE]"
    )
    parser.add_argument("--symbol", default=None, help="Ticker symbol, e.g. AAPL (omit to use --all)")
    parser.add_argument("--all", action="store_true", help="Export all symbols to CSV files in --output-dir")
    parser.add_argument("--from", dest="from_date", default="2001-01-01", help="Start date (ISO 8601; default: 2001-01-01)")
    parser.add_argument("--to", dest="to_date", default="2026-08-16", help="End date (ISO 8601; default: 2026-08-16)")
    parser.add_argument("--output", default=None, help="Output CSV file (optional; if omitted, generated from table name)")
    parser.add_argument("--output-dir", default="./csv_exports/", help="Output directory for --all (default: ./csv_exports/)")
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME, help=f"Bar size: '1 min' / '5 mins' / '1 hour' / '1 day' (default: {DEFAULT_TIMEFRAME})")
    parser.add_argument("--use-rth", choices=["true", "false"], help="Regular Trading Hours only (default: false)")
    parser.add_argument("--what-to-show", default="TRADES", help="TRADES, MIDPOINT, BID, ASK … (default: TRADES)")
    parser.add_argument("--exchange", default="SMART", help="Exchange (default: SMART)")
    parser.add_argument("--currency", default="USD", help="Currency (default: USD)")
    args = parser.parse_args()

    use_rth = (args.use_rth.lower() == "true") if args.use_rth else False

    if args.all:
        export_all_to_csv(
            from_date=args.from_date,
            to_date=args.to_date,
            output_dir=args.output_dir,
            timeframe=args.timeframe,
            use_rth=use_rth,
            what_to_show=args.what_to_show,
            exchange=args.exchange,
            currency=args.currency,
        )

    else:
        if not args.symbol:
            parser.error("Provide --symbol TICKER or use --all")

        success = export_symbol_to_csv(
            symbol=args.symbol,
            from_date=args.from_date,
            to_date=args.to_date,
            output_file=args.output,  # None will generate filename from table name
            timeframe=args.timeframe,
            use_rth=use_rth,
            what_to_show=args.what_to_show,
            exchange=args.exchange,
            currency=args.currency,
        )
        sys.exit(0 if success else 1)
