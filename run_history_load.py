"""
Command line: download IBKR historical bars into the per-symbol SQLite databases.

Single symbol (runs once):
    python run_history_load.py --symbol AAPL --from 2024-01-01 --to 2024-12-31 --timeframe "5 mins"

Batch mode (no --symbol): loads config once, pre-caches holidays, then downloads
every stock x data_storage_type continuously until each stock reaches its earliest
available data point. No config reload is needed. Ctrl+C exits.
    python run_history_load.py --config config.json
"""
from __future__ import annotations

import argparse
import time

import ibkr_client
from app_logging import get_logger
from config_loader import DEFAULT_CONFIG_PATH, StorageType, load_config
from history_loader import load_all_from_config, load_symbol_history
from holidays import cache_holidays_for_range
import sqlite_storage

CYCLE_SLEEP_SECONDS = 60


def _parse_args() -> argparse.Namespace:
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
    parser.add_argument("--config",     default=DEFAULT_CONFIG_PATH,
                        help="Path to config.json (used when --symbol is omitted)")
    return parser.parse_args()


def _run_single(args: argparse.Namespace, logger) -> None:
    config = load_config(args.config)
    ibkr_client.install_rate_limits(config.ibkr)
    storage_type = StorageType(
        from_date  = args.from_date  or "2001-01-01",
        to_date    = args.to_date    or "2026-08-16",
        timeframe  = args.timeframe  or "1 hour",
        useRTH     = (args.useRTH.lower() == "true") if args.useRTH else False,
        whatToShow = args.whatToShow or "TRADES",
        exchange   = args.exchange,
        currency   = args.currency,
    )
    result = load_symbol_history(config, args.symbol, storage_type)
    logger.info(f"\nSummary: {result}")


def _run_batch(args: argparse.Namespace, logger) -> None:
    try:
        logger.info("="*80)
        logger.info("Batch mode: Loading config (once)...")
        logger.info("="*80)

        config = load_config(args.config)
        ibkr_client.install_rate_limits(config.ibkr)

        # Pre-cache holidays
        cfg_conn = sqlite_storage.open_connection(config.config_db_path)
        try:
            cache_holidays_for_range(cfg_conn, years_to_cache=config.holiday_years)
        finally:
            cfg_conn.close()

        # Download all stocks until earliest data point is reached
        iteration = 0
        while True:
            iteration += 1
            logger.info(f"\n{'='*80}")
            logger.info(f"ITERATION #{iteration}: Downloading all stocks to earliest data point...")
            logger.info(f"{'='*80}")

            try:
                results = load_all_from_config(config)
                logger.info(f"\nIteration #{iteration} summary: {len(results)} tasks processed.")

                # Check if any tasks had data inserted (still downloading)
                any_inserted = any(r.get("rows_inserted", 0) > 0 for r in results if isinstance(r, dict))
                if not any_inserted:
                    logger.info("\n" + "="*80)
                    logger.info("All stocks downloaded to earliest available data point.")
                    logger.info("Download complete. Exiting.")
                    logger.info("="*80)
                    break
            except Exception as iter_exc:
                logger.error(f"Iteration #{iteration} failed with error: {iter_exc}")

    except KeyboardInterrupt:
        logger.info("\n" + "="*80)
        logger.info("Script interrupted by user. Exiting.")
        logger.info("="*80)


def main() -> None:
    args = _parse_args()
    logger = get_logger("run_history_load")
    if args.symbol:
        _run_single(args, logger)
    else:
        _run_batch(args, logger)


if __name__ == "__main__":
    main()
