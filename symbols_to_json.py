#!/usr/bin/env python3
"""
Parse stock symbol files and update config.json with symbols, descriptions, and exchange info.

Usage:
    python symbols_to_json.py [--stocklist-folder ./mkt-historical-data-loader/stocks_lists] [--config ./mkt-historical-data-loader/config.json]

Features:
    - Parses tab-separated files (Symbol, Description)
    - Replaces dots with underscores in symbols (BRK.B → BRK_B)
    - Extracts exchange name from filename
    - Backs up config.json before modifying
    - Validates descriptions (skips empty entries)
    - Merges with existing symbols (adds description + exchange to existing)
    - Updates source files after config.json is modified
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def normalize_symbol(symbol: str) -> str:
    """Replace dots with underscores in symbol (BRK.B → BRK_B)."""
    return symbol.replace(".", "_")


def extract_exchange_from_filename(filename: str) -> str:
    """Extract exchange name from filename (e.g., 'Symbols_AMEX.txt' → 'AMEX')."""
    name_without_ext = Path(filename).stem
    # Format: Symbols_EXCHANGE
    if "_" in name_without_ext:
        exchange = name_without_ext.split("_", 1)[1]
    else:
        exchange = name_without_ext
    return exchange.upper()


def parse_stocklist_file(filepath: str) -> list[dict[str, str]]:
    """
    Parse tab-separated stocklist file (Symbol<TAB>Description).

    Returns list of dicts with keys: symbol, description, exchange
    Skips entries with empty descriptions.
    """
    stocks = []
    exchange = extract_exchange_from_filename(filepath)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                parts = line.split("\t")
                if len(parts) < 2:
                    print(f"  ⚠ Line {line_num}: Invalid format (expected Symbol<TAB>Description)")
                    continue

                symbol = parts[0].strip()
                description = parts[1].strip()

                if not symbol:
                    print(f"  ⚠ Line {line_num}: Empty symbol, skipping")
                    continue

                if not description:
                    print(f"  ⚠ Line {line_num}: Empty description for {symbol}, skipping")
                    continue

                stocks.append({
                    "symbol": normalize_symbol(symbol),
                    "description": description,
                    "exchange": exchange,
                })
    except Exception as e:
        print(f"  ✗ Error reading {filepath}: {e}")
        return []

    return stocks


def load_config(config_path: str) -> dict[str, Any]:
    """Load config.json."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"✗ Config file not found: {config_path}")
        return {}
    except json.JSONDecodeError as e:
        print(f"✗ Invalid JSON in {config_path}: {e}")
        return {}


def save_config(config_path: str, config: dict[str, Any]) -> bool:
    """Save config.json."""
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"✗ Error writing {config_path}: {e}")
        return False


def backup_config(config_path: str) -> Optional[str]:
    """Create backup of config.json with timestamp."""
    if not os.path.exists(config_path):
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{config_path}.backup.{timestamp}"

    try:
        shutil.copy2(config_path, backup_path)
        print(f"✓ Backup created: {backup_path}")
        return backup_path
    except Exception as e:
        print(f"✗ Error creating backup: {e}")
        return None


def merge_stocks(config: dict[str, Any], new_stocks: list[dict[str, str]]) -> int:
    """
    Merge new stocks into config.

    - Add new stocks: {symbol, description, exchange}
    - Existing stocks: add description + exchange only

    Returns count of stocks added/updated.
    """
    if "stocks" not in config:
        config["stocks"] = []

    existing_symbols = {stock["symbol"] for stock in config["stocks"] if isinstance(stock, dict)}
    count = 0

    for new_stock in new_stocks:
        symbol = new_stock["symbol"]

        if symbol in existing_symbols:
            # Update existing stock with description and exchange
            for stock in config["stocks"]:
                if isinstance(stock, dict) and stock.get("symbol") == symbol:
                    stock["description"] = new_stock["description"]
                    stock["exchange"] = new_stock["exchange"]
                    print(f"  ↻ Updated {symbol}: {new_stock['description']}")
                    count += 1
                    break
        else:
            # Add new stock
            config["stocks"].append(new_stock)
            print(f"  + Added {symbol}: {new_stock['description']} ({new_stock['exchange']})")
            count += 1

    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse stock symbol files and update config.json"
    )
    parser.add_argument(
        "--stocklist-folder",
        default="./stocks_lists",
        help="Path to folder containing stocklist files (default: ./stocks_lists)"
    )
    parser.add_argument(
        "--config",
        default="./config.json",
        help="Path to config.json (default: ./config.json)"
    )
    args = parser.parse_args()

    stocklist_folder = Path(args.stocklist_folder)
    config_path = args.config

    print("="*80)
    print("Symbol Parser → config.json Updater")
    print("="*80)

    # Verify stocklist folder exists
    if not stocklist_folder.exists():
        print(f"✗ Stocklist folder not found: {stocklist_folder}")
        return

    # Find all .txt files in stocklist folder
    stocklist_files = list(stocklist_folder.glob("*.txt"))
    if not stocklist_files:
        print(f"✗ No .txt files found in {stocklist_folder}")
        return

    print(f"\nFound {len(stocklist_files)} stocklist file(s):")
    for f in sorted(stocklist_files):
        print(f"  - {f.name}")

    # Parse all files
    print(f"\nParsing files...")
    all_stocks = []
    for filepath in sorted(stocklist_files):
        print(f"  {filepath.name}:")
        stocks = parse_stocklist_file(str(filepath))
        all_stocks.extend(stocks)
        print(f"    ✓ Parsed {len(stocks)} stocks")

    if not all_stocks:
        print("✗ No valid stocks parsed from files")
        return

    print(f"\nTotal stocks parsed: {len(all_stocks)}")

    # Load config
    print(f"\nLoading config: {config_path}")
    config = load_config(config_path)
    if not config:
        return

    # Backup config
    print("\nBacking up config.json...")
    backup_path = backup_config(config_path)
    if not backup_path:
        print("⚠ Warning: Backup failed, but continuing...")

    # Merge stocks
    print(f"\nMerging stocks into config...")
    count = merge_stocks(config, all_stocks)
    print(f"✓ Merged {count} stocks")

    # Save config
    print(f"\nSaving config...")
    if save_config(config_path, config):
        print(f"✓ Config saved: {config_path}")
    else:
        print("✗ Failed to save config")
        if backup_path:
            print(f"  Backup preserved at: {backup_path}")
        return

    # Summary
    print("\n" + "="*80)
    print(f"✓ Success! Updated {len(config.get('stocks', []))} total stocks in config.json")
    if backup_path:
        print(f"✓ Backup: {backup_path}")
    print("="*80)


if __name__ == "__main__":
    main()
