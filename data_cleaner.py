"""
Data Cleaner — Validate OHLCV data for impossible or suspicious values.

Checks:
  - Prices <= 0
  - high < low (inverted)
  - open/close outside [low, high]
  - negative volume
  - sudden 50–90% moves (potential splits or bad ticks)
"""
from __future__ import annotations

import os
import sys
import json
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional

# Add strategies directory to path for db_reader import
_STRATEGIES_PATH = os.path.join(os.path.dirname(__file__), "..", "strategies")
if _STRATEGIES_PATH not in sys.path:
    sys.path.insert(0, os.path.abspath(_STRATEGIES_PATH))


@dataclass
class ValidationIssue:
    """Record of a single validation issue."""
    index: int | pd.Timestamp
    issue_type: str
    symbol: Optional[str] = None
    details: str = ""
    row_data: Optional[dict] = None


class DataValidator:
    """Validate and report OHLCV data quality issues."""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.issues: list[ValidationIssue] = []

    def validate_ohlcv(
        self,
        df: pd.DataFrame,
        symbol: str = "UNKNOWN",
        max_move_pct: float = 0.50,
    ) -> tuple[pd.DataFrame, list[ValidationIssue]]:
        """
        Validate OHLCV DataFrame and return cleaned data + issue list.

        Args:
            df: DataFrame with columns: open, high, low, close, volume
            symbol: Symbol name for reporting
            max_move_pct: Threshold for suspicious moves (0.50 = 50%)

        Returns:
            (cleaned_df, issues_list)
        """
        self.issues = []
        df = df.copy()

        # Ensure required columns exist
        required_cols = {"open", "high", "low", "close", "volume"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Missing columns: {missing}")

        # Validate each row
        for idx, row in df.iterrows():
            self._validate_row(idx, row, symbol, max_move_pct)

        # Mark bad rows
        bad_indices = [issue.index for issue in self.issues if issue.issue_type != "unusual_move"]
        if bad_indices:
            df = df.drop(index=bad_indices)

        if self.verbose and self.issues:
            self._print_summary(symbol)

        return df, self.issues

    def _validate_row(
        self,
        idx: int | pd.Timestamp,
        row: pd.Series,
        symbol: str,
        max_move_pct: float,
    ) -> None:
        """Validate a single OHLC row."""
        o, h, l, c, v = row.get("open"), row.get("high"), row.get("low"), row.get("close"), row.get("volume")

        # Check for NaN
        if pd.isna(o) or pd.isna(h) or pd.isna(l) or pd.isna(c) or pd.isna(v):
            self.issues.append(ValidationIssue(
                index=idx,
                issue_type="missing_data",
                symbol=symbol,
                details="NaN in OHLCV",
                row_data=row.to_dict(),
            ))
            return

        # Check for non-numeric
        try:
            o, h, l, c, v = float(o), float(h), float(l), float(c), float(v)
        except (ValueError, TypeError):
            self.issues.append(ValidationIssue(
                index=idx,
                issue_type="non_numeric",
                symbol=symbol,
                details="Non-numeric value in OHLCV",
                row_data=row.to_dict(),
            ))
            return

        # Check prices <= 0
        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            self.issues.append(ValidationIssue(
                index=idx,
                issue_type="zero_negative_price",
                symbol=symbol,
                details=f"Zero or negative price: O={o}, H={h}, L={l}, C={c}",
                row_data=row.to_dict(),
            ))

        # Check high < low
        if h < l:
            self.issues.append(ValidationIssue(
                index=idx,
                issue_type="inverted_hl",
                symbol=symbol,
                details=f"High ({h}) < Low ({l})",
                row_data=row.to_dict(),
            ))

        # Check open outside [low, high]
        if not (l <= o <= h):
            self.issues.append(ValidationIssue(
                index=idx,
                issue_type="open_outside_range",
                symbol=symbol,
                details=f"Open ({o}) outside [{l}, {h}]",
                row_data=row.to_dict(),
            ))

        # Check close outside [low, high]
        if not (l <= c <= h):
            self.issues.append(ValidationIssue(
                index=idx,
                issue_type="close_outside_range",
                symbol=symbol,
                details=f"Close ({c}) outside [{l}, {h}]",
                row_data=row.to_dict(),
            ))

        # Check negative volume
        if v < 0:
            self.issues.append(ValidationIssue(
                index=idx,
                issue_type="negative_volume",
                symbol=symbol,
                details=f"Negative volume: {v}",
                row_data=row.to_dict(),
            ))

    def detect_suspicious_moves(
        self,
        df: pd.DataFrame,
        symbol: str = "UNKNOWN",
        max_move_pct: float = 0.50,
        lookback: int = 1,
    ) -> list[ValidationIssue]:
        """
        Detect sudden large moves (potential splits, bad ticks, or gaps).

        Args:
            df: DataFrame with close prices
            symbol: Symbol name for reporting
            max_move_pct: Threshold (0.50 = 50%)
            lookback: Compare current to N bars back

        Returns:
            List of suspicious move issues
        """
        moves = []

        if "close" not in df.columns:
            return moves

        close = df["close"].astype(float)

        for i in range(lookback, len(close)):
            curr = close.iloc[i]
            prev = close.iloc[i - lookback]

            if prev <= 0:
                continue

            move_pct = abs(curr - prev) / prev

            if max_move_pct <= move_pct <= 0.90:
                idx = df.index[i]
                moves.append(ValidationIssue(
                    index=idx,
                    issue_type="unusual_move",
                    symbol=symbol,
                    details=f"{move_pct*100:.1f}% move: {prev:.4f} → {curr:.4f} (possible split or bad tick)",
                    row_data={"close": curr, "prev_close": prev},
                ))

        return moves

    def _print_summary(self, symbol: str) -> None:
        """Print validation summary."""
        by_type = {}
        for issue in self.issues:
            by_type.setdefault(issue.issue_type, []).append(issue)

        print(f"\n[{symbol}] Data validation issues found:")
        for issue_type, issues in by_type.items():
            print(f"  {issue_type}: {len(issues)} row(s)")
            for issue in issues[:5]:  # Show first 5
                print(f"    {issue.index}: {issue.details}")
            if len(issues) > 5:
                print(f"    ... and {len(issues) - 5} more")


def clean_historical_data(
    df: pd.DataFrame,
    symbol: str = "UNKNOWN",
    max_move_pct: float = 0.50,
    remove_bad_rows: bool = True,
    report_suspicious: bool = True,
    verbose: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """
    Clean and validate historical OHLCV data.

    Args:
        df: Input DataFrame with OHLCV columns
        symbol: Symbol name for reporting
        max_move_pct: Threshold for suspicious moves (0.50 = 50%)
        remove_bad_rows: Remove rows with validation errors
        report_suspicious: Report but don't remove suspicious moves
        verbose: Print summary

    Returns:
        (cleaned_df, stats_dict)
    """
    validator = DataValidator(verbose=verbose)

    # Validate and get issues
    df_cleaned, issues = validator.validate_ohlcv(df, symbol, max_move_pct)

    # Optionally detect suspicious moves
    suspicious_moves = []
    if report_suspicious:
        suspicious_moves = validator.detect_suspicious_moves(df_cleaned, symbol, max_move_pct)

    # Compile statistics
    stats = {
        "total_rows_input": len(df),
        "total_rows_output": len(df_cleaned),
        "rows_removed": len(df) - len(df_cleaned),
        "validation_issues": len(issues),
        "suspicious_moves": len(suspicious_moves),
        "issues_by_type": {},
    }

    for issue in issues:
        stats["issues_by_type"].setdefault(issue.issue_type, 0)
        stats["issues_by_type"][issue.issue_type] += 1

    if verbose:
        print(f"\n[{symbol}] Data cleaning summary:")
        print(f"  Input rows: {stats['total_rows_input']}")
        print(f"  Output rows: {stats['total_rows_output']}")
        print(f"  Rows removed: {stats['rows_removed']}")
        print(f"  Validation issues: {stats['validation_issues']}")
        print(f"  Suspicious moves: {stats['suspicious_moves']}")
        if stats["issues_by_type"]:
            print(f"  Issues by type:")
            for issue_type, count in stats["issues_by_type"].items():
                print(f"    - {issue_type}: {count}")

    return df_cleaned, stats


def scan_all_stocks(
    config_path: Optional[str] = None,
    max_move_pct: float = 0.50,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    timeframe: str = "1 hour",
) -> dict:
    """
    Scan all stocks in database and validate their data.

    Args:
        config_path: Path to config.json (uses default if None)
        max_move_pct: Suspicious move threshold
        from_date: Optional date range filter
        to_date: Optional date range filter
        timeframe: Timeframe to validate

    Returns:
        Dictionary with validation results for all symbols
    """
    # Load config
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8-sig") as f:
        config_data = json.load(f)

    db_folder = config_data.get("config", {}).get("database", {}).get("folder_path", "D:\\DB\\")

    # Import db_reader
    try:
        from db_reader import scan_available_symbols, read_symbol
    except ImportError as e:
        raise ImportError(f"Could not import db_reader: {e}")

    # Get all available symbols
    print(f"\nScanning database at: {db_folder}")
    print(f"Timeframe: {timeframe}")
    if from_date:
        print(f"Date range: {from_date} → {to_date}")

    symbols = scan_available_symbols(db_folder)
    if not symbols:
        print("No symbols found in database.")
        return {}

    print(f"Found {len(symbols)} symbol(s): {symbols}\n")

    # Validate each symbol
    results = {}
    for sym in symbols:
        print(f"\n{'='*70}")
        print(f"Validating {sym}")
        print(f"{'='*70}")

        try:
            # Read data from database
            df = read_symbol(
                symbol=sym,
                from_date=from_date or "2001-01-01",
                to_date=to_date or "2099-12-31",
                timeframe=timeframe,
                useRTH=False,
                whatToShow="TRADES",
                exchange="SMART",
                currency="USD",
            )

            if df.empty:
                print(f"[{sym}] No data found for this timeframe.")
                results[sym] = {"status": "no_data"}
                continue

            # Clean and validate
            df_clean, stats = clean_historical_data(
                df,
                symbol=sym,
                max_move_pct=max_move_pct,
                remove_bad_rows=True,
                report_suspicious=True,
                verbose=True,
            )

            results[sym] = {
                "status": "validated",
                "stats": stats,
            }

        except Exception as e:
            print(f"[{sym}] Error validating: {e}")
            results[sym] = {"status": "error", "error": str(e)}

    # Print overall summary
    print(f"\n\n{'='*70}")
    print("OVERALL VALIDATION SUMMARY")
    print(f"{'='*70}\n")

    total_symbols = len(symbols)
    validated = sum(1 for r in results.values() if r.get("status") == "validated")
    errors = sum(1 for r in results.values() if r.get("status") == "error")
    no_data = sum(1 for r in results.values() if r.get("status") == "no_data")

    print(f"Total symbols: {total_symbols}")
    print(f"  ✓ Validated: {validated}")
    print(f"  ⚠ No data: {no_data}")
    print(f"  ✗ Errors: {errors}\n")

    # Print per-symbol stats
    print("Per-symbol summary:")
    for sym, result in results.items():
        if result.get("status") == "validated":
            stats = result.get("stats", {})
            rows_removed = stats.get("rows_removed", 0)
            validation_issues = stats.get("validation_issues", 0)
            suspicious = stats.get("suspicious_moves", 0)

            status_str = "✓"
            if rows_removed > 0:
                status_str = "⚠"

            print(
                f"  {status_str} {sym}: "
                f"{stats.get('total_rows_output', 0)} rows "
                f"({rows_removed} removed, {validation_issues} issues, {suspicious} suspicious)"
            )
        elif result.get("status") == "no_data":
            print(f"  - {sym}: No data for timeframe")
        else:
            print(f"  ✗ {sym}: Error - {result.get('error', 'Unknown')}")

    return results


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate and clean OHLCV data from database or CSV."
    )
    parser.add_argument(
        "--mode",
        choices=["csv", "database"],
        default="database",
        help="Validation mode: 'database' scans all stocks, 'csv' validates single file",
    )
    parser.add_argument("--file", help="Path to CSV file (for csv mode)")
    parser.add_argument("--symbol", default="AAPL", help="Symbol name for reporting")
    parser.add_argument("--config", help="Path to config.json (for database mode)")
    parser.add_argument("--max-move", type=float, default=0.50, help="Max move threshold (0.50 = 50%)")
    parser.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD) for database scan")
    parser.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD) for database scan")
    parser.add_argument("--timeframe", default="1 min", help="Timeframe to validate (database mode)")
    args = parser.parse_args()

    if args.mode == "database":
        # Scan all stocks in database
        results = scan_all_stocks(
            config_path=args.config,
            max_move_pct=args.max_move,
            from_date=args.from_date,
            to_date=args.to_date,
            timeframe=args.timeframe,
        )
    else:
        # Validate single CSV file
        if not args.file:
            print("Error: --file is required for csv mode")
            exit(1)

        try:
            df = pd.read_csv(args.file, index_col=0, parse_dates=True)
            print(f"Loaded {len(df)} rows from {args.file}")
        except Exception as e:
            print(f"Error loading file: {e}")
            exit(1)

        df_clean, stats = clean_historical_data(
            df,
            symbol=args.symbol,
            max_move_pct=args.max_move,
            report_suspicious=True,
            verbose=True,
        )
