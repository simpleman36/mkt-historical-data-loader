"""
Tests for history_loader.py - verify exchange handling and table naming.
"""
from config_loader import StockEntry, StorageType
from history_loader import merge_storage_type


def test_merge_storage_type_preserves_exchange():
    """Exchange should NEVER be overridden by stock overrides."""
    storage_type = StorageType(
        from_date="2020-01-01",
        to_date="2026-09-30",
        timeframe="1 day",
        useRTH=False,
        whatToShow="TRADES",
        exchange="SMART",  # data_storage_types exchange
        currency="USD"
    )

    stock = StockEntry(
        symbol="COST",
        overrides={"exchange": "NASDAQ"}  # stock trying to override exchange
    )

    merged = merge_storage_type(stock, storage_type)

    # Exchange should remain SMART (from data_storage_types), NOT NASDAQ
    assert merged.exchange == "SMART", \
        f"Expected SMART but got {merged.exchange}. Stock exchange should never override data_storage_types."


def test_merge_storage_type_allows_timeframe_override():
    """Other fields like timeframe CAN be overridden by stock."""
    storage_type = StorageType(
        from_date="2020-01-01",
        to_date="2026-09-30",
        timeframe="1 day",
        useRTH=False,
        whatToShow="TRADES",
        exchange="SMART",
        currency="USD"
    )

    stock = StockEntry(
        symbol="AAPL",
        overrides={"timeframe": "1 hour"}  # stock wants different timeframe
    )

    merged = merge_storage_type(stock, storage_type)

    # Timeframe should be overridden to "1 hour"
    assert merged.timeframe == "1 hour", "Timeframe override should work"
    # Exchange should remain SMART
    assert merged.exchange == "SMART", "Exchange should never be overridden"


def test_merge_storage_type_no_overrides():
    """Stock with no overrides should use storage_type as-is."""
    storage_type = StorageType(
        from_date="2020-01-01",
        to_date="2026-09-30",
        timeframe="1 day",
        useRTH=False,
        whatToShow="TRADES",
        exchange="SMART",
        currency="USD"
    )

    stock = StockEntry(
        symbol="MSFT",
        overrides={}  # no overrides
    )

    merged = merge_storage_type(stock, storage_type)

    # Should be identical to storage_type
    assert merged.exchange == "SMART"
    assert merged.timeframe == "1 day"
    assert merged.from_date == "2020-01-01"


def test_stock_exchange_is_metadata_only():
    """Individual stock exchange is metadata and should not affect API requests."""
    # When stock has exchange="NYSE" but data_storage_types has exchange="SMART"
    storage_type = StorageType(
        from_date="2020-01-01",
        to_date="2026-09-30",
        timeframe="1 day",
        useRTH=False,
        whatToShow="TRADES",
        exchange="SMART",  # API will use this
        currency="USD"
    )

    stock_nyse = StockEntry(
        symbol="IBM",
        overrides={"exchange": "NYSE"}  # Even if in overrides, should be ignored
    )

    merged = merge_storage_type(stock_nyse, storage_type)

    # API requests must use SMART, not NYSE
    assert merged.exchange == "SMART", \
        "IBKR API requests must use data_storage_types exchange, never individual stock exchange"


if __name__ == "__main__":
    print("Running tests for history_loader.py...\n")

    tests = [
        ("test_merge_storage_type_preserves_exchange", test_merge_storage_type_preserves_exchange),
        ("test_merge_storage_type_allows_timeframe_override", test_merge_storage_type_allows_timeframe_override),
        ("test_merge_storage_type_no_overrides", test_merge_storage_type_no_overrides),
        ("test_stock_exchange_is_metadata_only", test_stock_exchange_is_metadata_only),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        try:
            test_func()
            print(f"[PASS] {test_name}")
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {test_name}: {e}")
            failed += 1
        except Exception as e:
            print(f"[FAIL] {test_name}: ERROR - {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
