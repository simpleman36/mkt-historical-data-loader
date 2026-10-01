"""
Business logic for downloading IBKR history into the per-symbol SQLite databases.

- find_missing_ranges: which parts of [from, to] are not stored yet (before / after existing data)
- load_symbol_history: download one symbol for one StorageType, starting no earlier than
  the earliest available data point (reqHeadTimeStamp, cached in the config DB)
- load_all_from_config: every stock x every StorageType in an AppConfig, with per-stock overrides
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import pandas as pd

import ibkr_client
import sqlite_storage
import holidays
from api_rate_limiter import get_group_stats
from app_logging import module_logger
from config_loader import AppConfig, StockEntry, StorageType
from holidays import correct_date_range, correct_to_date_for_daily_bars
from time_utils import ibkr_duration_string, minutes_per_bar, normalize_bar_size, to_utc_datetime

logger = module_logger(__name__)

# fetch(end_dt, duration) -> DataFrame with sqlite_storage.BAR_COLUMNS (empty when no data)
FetchPage = Callable[[datetime, str], pd.DataFrame]


def find_missing_ranges(
    bounds: Optional[tuple[datetime, datetime]],
    from_dt: datetime,
    to_dt: datetime,
    bar_delta: timedelta,
) -> list[tuple[datetime, datetime]]:
    """
    Sub-ranges of [from_dt, to_dt] still to download, given the (min, max) of the
    stored rows in that window (or None when nothing is stored). Only the gap
    before the first stored bar and after the last one are considered.
    """
    if bounds is None:
        return [(from_dt, to_dt)]

    existing_min, existing_max = bounds
    missing = []
    if from_dt < existing_min - bar_delta:
        missing.append((from_dt, existing_min - timedelta(seconds=1)))
    if existing_max + bar_delta < to_dt:
        missing.append((existing_max + timedelta(seconds=1), to_dt))
    return missing


def _download_range(
    fetch: FetchPage,
    conn,
    config: AppConfig,
    symbol: str,
    table: str,
    from_dt: datetime,
    to_dt: datetime,
    duration_str: str,
    bar_size: str,
    rate_limit_group: str,
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

        df = fetch(cursor_end, duration_str)
        if df.empty:
            logger.info(f"[{symbol}] No bars returned, stopping.")
            break

        # Clip to requested window
        from_naive = from_dt.replace(tzinfo=None)
        to_naive   = to_dt.replace(tzinfo=None)
        df = df[(df["datetime"] >= from_naive) & (df["datetime"] <= to_naive)]

        inserted = sqlite_storage.insert_bars(conn, table, df[sqlite_storage.BAR_COLUMNS])
        inserted_total += inserted
        logger.info(f"[{symbol}]  → {len(df)} bars received, {inserted} new rows inserted → table '{table}'")
        logger.info(f"[{symbol}]  PERFORMANCE {get_group_stats(rate_limit_group)}")


        # For 1-day bars with 10Y chunks: if fewer than ~2000 bars, no more data available
        if "10 Y" in duration_str:
            bar_minutes = minutes_per_bar(bar_size)
            if abs(bar_minutes - 1440.0) < 0.01:  # 1440 = 24 hours in minutes
                if len(df) < 2000:
                    logger.info(f"[{symbol}] Received {len(df)} bars (< 2000 expected for 10Y daily), "
                               f"earliest data reached. Marking in config DB and stopping.")
                    # Mark that earliest data has been reached for this stock
                    cfg_conn = sqlite_storage.open_connection(config.config_db_path)
                    try:
                        sqlite_storage.mark_earliest_reached(cfg_conn, config.equities_table, table)
                    finally:
                        cfg_conn.close()
                    break

        oldest = df["datetime"].min() if not df.empty else None
        if oldest is None:
            break

        oldest_utc = oldest.to_pydatetime().replace(tzinfo=timezone.utc)
        if oldest_utc <= from_dt:
            break

        cursor_end = oldest_utc - timedelta(seconds=1)

    return inserted_total, request_count


def _earliest_available(
    config: AppConfig,
    symbol: str,
    st: StorageType,
    table: str,
    mpb: float,
    request_head: Callable[[], Optional[datetime]],
) -> Optional[datetime]:
    """
    Earliest available data point for this series. Read from the config DB's
    equities table if stored; otherwise call *request_head* (reqHeadTimeStamp)
    and store the answer there. Returns None if neither source has it.
    """
    cfg_conn = sqlite_storage.open_connection(config.config_db_path)
    try:
        sqlite_storage.ensure_equities_table(cfg_conn, config.equities_table)
        head = sqlite_storage.get_head_timestamp(cfg_conn, config.equities_table, symbol,
                                                 st.exchange, st.whatToShow, st.currency, st.useRTH)
        if head is not None:
            logger.info(f"[{symbol}] Earliest available data point from config DB: {head.isoformat()}")
            return head

        head = request_head()
        if head is not None:
            sqlite_storage.save_head_timestamp(cfg_conn, config.equities_table, table, symbol,
                                               st.exchange, st.whatToShow, st.currency, st.useRTH,
                                               mpb, head)
            logger.info(f"[{symbol}] Stored earliest available data point in "
                        f"{config.config_db_path} ({config.equities_table}.{table})")
        return head
    finally:
        cfg_conn.close()


def load_symbol_history(
    config: AppConfig,
    symbol: str,
    storage_type: StorageType,
    client_id: Optional[int] = None,
) -> dict:
    """
    Download IBKR historical OHLCV bars for *symbol* over the StorageType's
    [from_date, to_date] and append them (deduped by timestamp) to the matching
    SQLite table. from_date is moved forward to the earliest available data point
    (config DB, or reqHeadTimeStamp on first use). Returns a summary dict.
    """
    ibkr = config.ibkr
    if client_id is None:
        client_id = ibkr.historical_data_client_id

    st = storage_type
    from_dt = to_utc_datetime(st.from_date)
    to_dt   = to_utc_datetime(st.to_date)
    if from_dt >= to_dt:
        raise ValueError("'from_date' must be earlier than 'to_date'")

    # Normalize bar size early for date correction
    bar_size = normalize_bar_size(st.timeframe)
    mpb      = minutes_per_bar(bar_size)

    # Ensure holidays table exists (pre-cached by run_history_load.py)
    cfg_conn_holidays = sqlite_storage.open_connection(config.config_db_path)
    try:
        holidays.ensure_holidays_table(cfg_conn_holidays)
    finally:
        cfg_conn_holidays.close()

    # Correct dates to trading days (skip weekends/holidays)
    cfg_conn = sqlite_storage.open_connection(config.config_db_path)
    try:
        from_dt, to_dt = correct_date_range(cfg_conn, from_dt, to_dt)
        # For daily bars, to_date must be before today (data only available after market close)
        if bar_size == "1 day":
            to_dt = correct_to_date_for_daily_bars(cfg_conn, to_dt)
    finally:
        cfg_conn.close()

    if from_dt >= to_dt:
        raise ValueError("After holiday correction, from_date >= to_date")

    table   = sqlite_storage.table_name(symbol, bar_size, st.useRTH, st.whatToShow, st.exchange, st.currency)
    db_path = sqlite_storage.db_path_for_symbol(config.db_folder, symbol)

    summary = {
        "symbol"        : symbol,
        "db_path"       : db_path,
        "table_name"    : table,
        "requests"      : 0,
        "rows_inserted" : 0,
        "from_date"     : from_dt.isoformat(),
        "to_date"       : to_dt.isoformat(),
        "timeframe"     : bar_size,
        "useRTH"        : st.useRTH,
        "whatToShow"    : st.whatToShow,
        "skipped"       : False,
        "head_timestamp": None,
    }

    conn = sqlite_storage.open_connection(db_path)
    ib = None
    try:
        sqlite_storage.ensure_table(conn, table)

        def ensure_connected():
            nonlocal ib
            if ib is None:
                logger.info(f"[{symbol}] Connecting to IBKR "
                            f"{ibkr.connection.host}:{ibkr.connection.port} ...")
                ib = ibkr_client.connect(ibkr, client_id)
            return ib

        contract = ibkr_client.stock_contract(symbol, st.exchange, st.currency)

        # Earliest available data point: config DB first, otherwise reqHeadTimeStamp (then cached).
        # Check if we've already reached the earliest data for this stock
        cfg_conn_earliest = sqlite_storage.open_connection(config.config_db_path)
        try:
            earliest_reached = sqlite_storage.is_earliest_reached(cfg_conn_earliest, config.equities_table, table)
        finally:
            cfg_conn_earliest.close()

        if earliest_reached:
            # Stock has no more historical data - only update latest available
            logger.info(f"[{symbol}] Earliest data already reached. Only updating latest available data "
                       f"from DB to previous working day.")
            bounds = sqlite_storage.existing_bounds(conn, table, from_dt, to_dt)
            if bounds:
                from_dt = bounds[1] + timedelta(seconds=1)
            missing_ranges = [(from_dt, to_dt)] if from_dt < to_dt else []
            summary["head_timestamp"] = None
        else:
            head_dt = _earliest_available(config, symbol, st, table, mpb,
                                          lambda: ibkr_client.fetch_head_timestamp(
                                              ensure_connected(), ibkr, contract, st.whatToShow, st.useRTH))
            summary["head_timestamp"] = head_dt.isoformat() if head_dt else None
            if head_dt is not None and head_dt > from_dt:
                if head_dt >= to_dt:
                    logger.info(f"[{symbol}] Earliest available data {head_dt.isoformat()} is after "
                                f"to_date {to_dt.date()}. Nothing to download.")
                    summary["skipped"] = True
                    return summary
                logger.info(f"[{symbol}] from_date {from_dt.date()} moved to earliest available "
                            f"data point {head_dt.isoformat()}")
                from_dt = head_dt

            # Gap detection: find which sub-ranges are NOT yet in the DB
            bounds = sqlite_storage.existing_bounds(conn, table, from_dt, to_dt)
            missing_ranges = find_missing_ranges(bounds, from_dt, to_dt, timedelta(seconds=mpb * 60))

        if bounds is None:
            logger.info(f"[{symbol}] No existing data found in [{from_dt.date()}, {to_dt.date()}]. "
                        f"Full download required.")
        else:
            for sub_from, sub_to in missing_ranges:
                where = "BEFORE" if sub_to < bounds[0] else "AFTER"
                logger.info(f"[{symbol}] Gap detected {where} existing data: "
                            f"[{sub_from.date()} → {sub_to.date()}]")

        if not missing_ranges:
            logger.info(f"[{symbol}] Data already fully covered "
                        f"[{from_dt.date()} → {to_dt.date()}]. Skipping download.")
            summary["skipped"] = True
            return summary

        # Download each missing sub-range, in chunks of at most `duration` per request
        def fetch(end_dt: datetime, duration: str) -> pd.DataFrame:
            return ibkr_client.fetch_bars(ensure_connected(), ibkr, contract, end_dt, duration,
                                          bar_size, st.whatToShow, st.useRTH)

        for sub_from, sub_to in missing_ranges:
            duration = ibkr_duration_string(sub_from, sub_to, mpb, ibkr.max_bars_per_call)
            logger.info(f"[{symbol}] Downloading sub-range [{sub_from.date()} → {sub_to.date()}] "
                        f"(chunk duration={duration}) ...")
            ins, reqs = _download_range(fetch, conn, config, symbol, table, sub_from, sub_to,
                                        duration, bar_size, ibkr.rate_limit_group)
            summary["rows_inserted"] += ins
            summary["requests"]      += reqs
    finally:
        if ib is not None:
            ibkr_client.disconnect(ib)
        conn.close()

    logger.info(f"\n[{symbol}] Done. {summary['rows_inserted']} total rows inserted "
                f"in {summary['requests']} request(s).")
    logger.info(f"  DB   : {db_path}")
    logger.info(f"  Table: {table}")
    return summary


def merge_storage_type(stock: StockEntry, storage_type: StorageType) -> StorageType:
    """Apply the stock's per-symbol overrides on top of a StorageType.

    NOTE: 'exchange' is never merged from stock overrides - IBKR requests always use
    data_storage_types exchange, even if stock has its own exchange field.
    """
    overrides = dict(stock.overrides)
    overrides.pop("exchange", None)  # Never override exchange from stock config
    return dataclasses.replace(storage_type, **overrides)


def load_all_from_config(config: AppConfig) -> list[dict]:
    """
    Process every stock with every StorageType. Per-task errors are logged and
    recorded in the result list; processing continues with the next task.

    Resumes from last processed stock (checkpoint) if available.
    """
    if not config.storage_types:
        logger.error("[config] No 'data_storage_types' defined in config. Nothing to process.")
        return []

    # Read checkpoint to resume from last processed stock
    cfg_conn = sqlite_storage.open_connection(config.config_db_path)
    try:
        checkpoint_symbol = sqlite_storage.get_checkpoint(cfg_conn)
        if checkpoint_symbol:
            logger.info(f"[config] Resuming from checkpoint: {checkpoint_symbol}")
    finally:
        cfg_conn.close()

    results = []
    total_tasks = len(config.stocks) * len(config.storage_types)
    task_num = 0
    skip_until_checkpoint = checkpoint_symbol is not None
    current_stock_complete = False

    for stock_idx, stock in enumerate(config.stocks, start=1):
        if not stock.symbol:
            logger.warning(f"[config] Skipping stock entry #{stock_idx}: missing 'symbol' key.")
            continue

        # Skip stocks until we reach checkpoint
        if skip_until_checkpoint and stock.symbol != checkpoint_symbol:
            continue
        skip_until_checkpoint = False

        for storage_type in config.storage_types:
            task_num += 1
            logger.info(
                f"\n[config] [{task_num}/{total_tasks}] {stock.symbol} "
                f"(timeframe={storage_type.timeframe}, "
                f"whatToShow={storage_type.whatToShow}) ..."
            )
            st = merge_storage_type(stock, storage_type)
            try:
                results.append(load_symbol_history(config, stock.symbol, st))
            except Exception as exc:
                logger.error(f"[config] ERROR for {stock.symbol} (timeframe={st.timeframe}): {exc}")
                results.append({
                    "symbol": stock.symbol,
                    "timeframe": st.timeframe,
                    "whatToShow": st.whatToShow,
                    "error": str(exc),
                })

        # Save checkpoint after stock is complete
        cfg_conn = sqlite_storage.open_connection(config.config_db_path)
        try:
            sqlite_storage.save_checkpoint(cfg_conn, stock.symbol)
        finally:
            cfg_conn.close()

    logger.info(f"\n[config] Batch complete. {len(results)}/{total_tasks} tasks processed.")
    return results
