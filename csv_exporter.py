"""
Export stored OHLCV bars from the per-symbol SQLite databases to CSV.

- export_symbol_to_csv: one symbol -> one CSV (default name: <TABLE_NAME>.csv)
- export_all_to_csv: every <SYMBOL>.db in the DB folder -> <output_dir>/<SYMBOL>.csv

CSV columns: timestamp, datetime, open, high, low, close, volume, average, barCount.
Options left as None fall back to the first data_storage_type in config.json,
except timeframe, which defaults to '1 min'.
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Optional, Union

import pandas as pd

import sqlite_storage
from config_loader import AppConfig, StorageType
from time_utils import DateLike, normalize_bar_size, to_utc_datetime

DEFAULT_TIMEFRAME = "1 min"
DEFAULT_OUTPUT_DIR = "./csv_exports/"


def _storage_defaults(config: AppConfig) -> StorageType:
    return config.storage_types[0] if config.storage_types else StorageType()


def export_symbol_to_csv(
    config: AppConfig,
    symbol: str,
    from_date: DateLike,
    to_date: DateLike,
    output_file: Optional[str] = None,
    timeframe: Union[str, int, None] = None,
    use_rth: Optional[bool] = None,
    what_to_show: Optional[str] = None,
    exchange: Optional[str] = None,
    currency: Optional[str] = None,
) -> bool:
    """Export one symbol's bars in [from_date, to_date] to CSV. Returns True on success."""
    d = _storage_defaults(config)
    timeframe    = DEFAULT_TIMEFRAME if timeframe is None else timeframe
    use_rth      = d.useRTH if use_rth is None else use_rth
    what_to_show = d.whatToShow if what_to_show is None else what_to_show
    exchange     = d.exchange if exchange is None else exchange
    currency     = d.currency if currency is None else currency

    from_dt = to_utc_datetime(from_date)
    to_dt   = to_utc_datetime(to_date)
    if from_dt >= to_dt:
        raise ValueError("'from_date' must be earlier than 'to_date'")

    bar_size = normalize_bar_size(timeframe)
    tbl      = sqlite_storage.table_name(symbol, bar_size, use_rth, what_to_show, exchange, currency)
    db_path  = sqlite_storage.db_path_for_symbol(config.db_folder, symbol)

    # Generate output filename from table name if not provided
    if output_file is None:
        output_file = f"{tbl}.csv"

    if not os.path.isfile(db_path):
        print(f"[{symbol}] ERROR: Database not found: {db_path}")
        return False

    df = sqlite_storage.read_bars(db_path, tbl, from_dt, to_dt)
    if df is None:
        print(f"[{symbol}] ERROR: Table '{tbl}' not found in {db_path}")
        return False

    if df.empty:
        print(f"[{symbol}] ERROR: No data found in '{tbl}' for [{from_dt.date()} → {to_dt.date()}]")
        return False

    df["datetime"] = pd.to_datetime(df["datetime"])

    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_file, index=False)
    print(f"[{symbol}] SUCCESS: Exported {len(df)} rows to {output_file}")
    return True


def export_all_to_csv(
    config: AppConfig,
    from_date: DateLike,
    to_date: DateLike,
    output_dir: Optional[str] = None,
    timeframe: Union[str, int, None] = None,
    use_rth: Optional[bool] = None,
    what_to_show: Optional[str] = None,
    exchange: Optional[str] = None,
    currency: Optional[str] = None,
    db_folder: Optional[str] = None,
) -> dict[str, bool]:
    """Export every symbol found in the DB folder to <output_dir>/<SYMBOL>.csv. Returns {symbol: ok}."""
    output_dir = DEFAULT_OUTPUT_DIR if output_dir is None else output_dir
    if db_folder:
        config = dataclasses.replace(config, db_folder=db_folder)
    folder = config.db_folder

    if not os.path.isdir(folder):
        print(f"[scan] DB folder not found: {folder}")
        symbols = []
    else:
        symbols = sqlite_storage.list_symbols(folder)
        print(f"[scan] Found {len(symbols)} symbol DB(s) in {folder}")

    if not symbols:
        print("[export_all] No symbols found to export.")
        return {}

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    results = {}
    for i, symbol in enumerate(symbols, 1):
        output_file = os.path.join(output_dir, f"{symbol}.csv")
        print(f"\n[{i}/{len(symbols)}] Exporting {symbol}...")
        results[symbol] = export_symbol_to_csv(
            config, symbol, from_date, to_date,
            output_file=output_file,
            timeframe=timeframe,
            use_rth=use_rth,
            what_to_show=what_to_show,
            exchange=exchange,
            currency=currency,
        )

    success_count = sum(1 for v in results.values() if v)
    print(f"\n[export_all] Completed: {success_count}/{len(symbols)} symbols exported to {output_dir}")
    return results
