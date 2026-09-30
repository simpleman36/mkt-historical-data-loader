from datetime import datetime, timezone

import pandas as pd

import sqlite_storage as s


def _utc(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def make_bars(timestamps):
    return pd.DataFrame([
        {"timestamp": ts, "datetime": _utc(ts).replace(tzinfo=None),
         "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100, "average": 1.2, "barCount": 7}
        for ts in timestamps
    ], columns=s.BAR_COLUMNS)


def test_table_name_format():
    # Recorded from the pre-refactor history_range_loader._table_name
    assert s.table_name("BRK.B", "5 mins", False, "TRADES") == "BRK_B_SMART_TRADES_USD_5_MINS_RTH_FALSE"
    assert s.table_name("aapl", "1 day", True, "MIDPOINT", "ISLAND", "EUR") == "AAPL_ISLAND_MIDPOINT_EUR_1_DAY_RTH_TRUE"


def test_db_path_for_symbol(tmp_path):
    assert s.db_path_for_symbol(str(tmp_path), "AAPL") == str(tmp_path / "AAPL.db")


def test_create_insert_dedupe_bounds_read(tmp_path):
    db = str(tmp_path / "sub" / "AAPL.db")
    conn = s.open_connection(db)
    try:
        s.ensure_table(conn, "T")
        assert s.insert_bars(conn, "T", make_bars([100, 200, 300])) == 3
        assert s.insert_bars(conn, "T", make_bars([300, 400])) == 1   # 300 already stored

        assert s.existing_bounds(conn, "T", _utc(0), _utc(1000)) == (_utc(100), _utc(400))
        assert s.existing_bounds(conn, "T", _utc(500), _utc(1000)) is None
        assert s.existing_bounds(conn, "MISSING", _utc(0), _utc(1000)) is None
    finally:
        conn.close()

    df = s.read_bars(db, "T", _utc(150), _utc(400))
    assert list(df.columns) == s.BAR_COLUMNS
    assert df["timestamp"].tolist() == [200, 300, 400]
    assert s.read_bars(db, "MISSING", _utc(0), _utc(1000)) is None


def test_schema_unchanged(tmp_path):
    conn = s.open_connection(str(tmp_path / "X.db"))
    s.ensure_table(conn, "T")
    cols = [(r[1], r[2], r[5]) for r in conn.execute("PRAGMA table_info(T)")]
    conn.close()
    assert cols == [
        ("timestamp", "UNSIGNED BIG INT", 1), ("datetime", "TEXT", 0),
        ("open", "DECIMAL(10, 5)", 0), ("high", "DECIMAL(10, 5)", 0), ("low", "DECIMAL(10, 5)", 0),
        ("close", "DECIMAL(10, 5)", 0), ("volume", "INTEGER", 0), ("average", "DECIMAL(10, 5)", 0),
        ("barCount", "INTEGER", 0),
    ]


def test_list_symbols(tmp_path):
    for name in ["AAPL.db", "MSFT.DB", "_config.db", "notes.txt"]:
        (tmp_path / name).write_text("")
    assert s.list_symbols(str(tmp_path)) == ["AAPL", "MSFT"]
    assert s.list_symbols(str(tmp_path / "missing")) == []
