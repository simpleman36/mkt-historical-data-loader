"""
Command line: export stored bars from the per-symbol SQLite databases to CSV.

    python run_csv_export.py --symbol AAPL --from 2024-01-01 --to 2024-12-31
        -> AAPL_SMART_TRADES_USD_1_MIN_RTH_FALSE.csv
    python run_csv_export.py --symbol AAPL --timeframe "1 hour" --output my_file.csv
    python run_csv_export.py --all --from 2024-01-01 --to 2024-12-31 --output-dir ./csv_exports/
        -> ./csv_exports/<SYMBOL>.csv for every <SYMBOL>.db in the DB folder
"""
from __future__ import annotations

import argparse
import sys

from config_loader import DEFAULT_CONFIG_PATH, load_config
from csv_exporter import DEFAULT_OUTPUT_DIR, DEFAULT_TIMEFRAME, export_all_to_csv, export_symbol_to_csv


def main() -> None:
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
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Output directory for --all (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME, help=f"Bar size: '1 min' / '5 mins' / '1 hour' / '1 day' (default: {DEFAULT_TIMEFRAME})")
    parser.add_argument("--use-rth", choices=["true", "false"], help="Regular Trading Hours only (default: false)")
    parser.add_argument("--what-to-show", default="TRADES", help="TRADES, MIDPOINT, BID, ASK … (default: TRADES)")
    parser.add_argument("--exchange", default="SMART", help="Exchange (default: SMART)")
    parser.add_argument("--currency", default="USD", help="Currency (default: USD)")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to config.json (for the DB folder)")
    args = parser.parse_args()

    if not args.all and not args.symbol:
        parser.error("Provide --symbol TICKER or use --all")

    config = load_config(args.config)
    use_rth = (args.use_rth.lower() == "true") if args.use_rth else False
    options = dict(
        timeframe=args.timeframe,
        use_rth=use_rth,
        what_to_show=args.what_to_show,
        exchange=args.exchange,
        currency=args.currency,
    )

    if args.all:
        export_all_to_csv(config, args.from_date, args.to_date, output_dir=args.output_dir, **options)
    else:
        ok = export_symbol_to_csv(config, args.symbol, args.from_date, args.to_date,
                                  output_file=args.output, **options)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
