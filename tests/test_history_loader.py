from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import history_loader
import sqlite_storage
from config_loader import StockEntry, StorageType, parse_config
from history_loader import _download_range, find_missing_ranges, merge_storage_type

UTC = timezone.utc
HOUR = timedelta(hours=1)


def dt(*a):
    return datetime(*a, tzinfo=UTC)


# ---------------------------------------------------------------- find_missing_ranges

def test_nothing_stored_means_full_range():
    assert find_missing_ranges(None, dt(2024, 1, 1), dt(2024, 2, 1), HOUR) == [(dt(2024, 1, 1), dt(2024, 2, 1))]


def test_gap_before_and_after():
    got = find_missing_ranges((dt(2024, 1, 10), dt(2024, 1, 20)), dt(2024, 1, 1), dt(2024, 2, 1), HOUR)
    one = timedelta(seconds=1)
    assert got == [(dt(2024, 1, 1), dt(2024, 1, 10) - one), (dt(2024, 1, 20) + one, dt(2024, 2, 1))]


def test_fully_covered_within_one_bar():
    bounds = (dt(2024, 1, 1, 0, 30), dt(2024, 1, 31, 23, 30))
    assert find_missing_ranges(bounds, dt(2024, 1, 1), dt(2024, 2, 1), HOUR) == []


def test_only_after():
    got = find_missing_ranges((dt(2024, 1, 1), dt(2024, 1, 15)), dt(2024, 1, 1), dt(2024, 2, 1), HOUR)
    assert got == [(dt(2024, 1, 15) + timedelta(seconds=1), dt(2024, 2, 1))]


# ---------------------------------------------------------------- paging loop

def _hourly_bars(end, n):
    """n hourly bars ending at or before *end* (naive UTC datetimes), oldest first."""
    last = end.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    times = [last - HOUR * i for i in reversed(range(n))]
    return pd.DataFrame({
        "timestamp": [int(t.replace(tzinfo=UTC).timestamp()) for t in times],
        "datetime": pd.to_datetime(times),
        "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10, "average": 1.1, "barCount": 3,
    })[sqlite_storage.BAR_COLUMNS]


def test_download_range_pages_backward_and_stops_at_from(tmp_path):
    conn = sqlite_storage.open_connection(str(tmp_path / "X.db"))
    sqlite_storage.ensure_table(conn, "T")
    calls = []

    def fake_fetch(end_dt, duration):
        calls.append(end_dt)
        return _hourly_bars(end_dt, 24)

    from_dt, to_dt = dt(2024, 1, 1), dt(2024, 1, 3, 23)
    inserted, requests = _download_range(fake_fetch, conn, "X", "T", from_dt, to_dt, "1 D", "1 hour", "g")
    conn.close()

    assert requests == 3
    assert calls[0] == to_dt
    assert calls[1] == dt(2024, 1, 3) - timedelta(seconds=1)   # oldest bar of page 1 minus 1s
    assert inserted == 3 * 24                                    # 2024-01-01 00:00 .. 2024-01-03 23:00


def test_download_range_stops_on_empty_page(tmp_path):
    conn = sqlite_storage.open_connection(str(tmp_path / "X.db"))
    sqlite_storage.ensure_table(conn, "T")
    pages = [_hourly_bars(dt(2024, 1, 3, 23), 24), pd.DataFrame(columns=sqlite_storage.BAR_COLUMNS)]
    inserted, requests = _download_range(lambda e, d: pages.pop(0), conn, "X", "T",
                                         dt(2024, 1, 1), dt(2024, 1, 3, 23), "1 D", "1 hour", "g")
    conn.close()
    assert (inserted, requests) == (24, 2)


# ---------------------------------------------------------------- symbol + batch

def _config(tmp_path, stocks, storage_types):
    return parse_config({
        "config": {"database": {"folder_path": str(tmp_path),
                                "config_file_path": str(tmp_path / "__config.db")}},
        "data_storage_types": storage_types,
        "stocks": stocks,
    })


class FakeIb:
    """Patches ibkr_client so load_symbol_history runs without IB Gateway."""

    def __init__(self, monkeypatch, head=None, max_pages=None):
        self.connects, self.head_calls, self.bar_calls = [], 0, []
        self.max_pages = max_pages
        self.head = head
        c = history_loader.ibkr_client
        monkeypatch.setattr(c, "connect", lambda ibkr, cid: self.connects.append(cid) or object())
        monkeypatch.setattr(c, "disconnect", lambda ib: None)
        monkeypatch.setattr(c, "fetch_head_timestamp", self._head)
        monkeypatch.setattr(c, "fetch_bars", self._bars)

    def _head(self, ib, ibkr, contract, what, rth):
        self.head_calls += 1
        return self.head

    def _bars(self, ib, ibkr, c, end, dur, bs, w, rth):
        self.bar_calls.append((end, dur))
        if self.max_pages is not None and len(self.bar_calls) > self.max_pages:
            return pd.DataFrame(columns=sqlite_storage.BAR_COLUMNS)
        return _hourly_bars(end, 24)


def test_load_symbol_history_downloads_then_skips(tmp_path, monkeypatch):
    ib = FakeIb(monkeypatch, head=None)
    cfg = _config(tmp_path, [], [])
    st = StorageType(from_date="2024-01-01", to_date="2024-01-02T23:00", timeframe="1 hour")

    first = history_loader.load_symbol_history(cfg, "AAPL", st)
    assert first["rows_inserted"] == 48 and first["requests"] == 2 and first["skipped"] is False
    assert first["table_name"] == "AAPL_SMART_TRADES_USD_1_HOUR_RTH_FALSE"
    assert first["db_path"] == str(tmp_path / "AAPL.db")
    assert first["head_timestamp"] is None
    assert ib.connects == [1]

    second = history_loader.load_symbol_history(cfg, "AAPL", st)
    assert second["skipped"] is True and second["requests"] == 0
    assert set(first) == set(second)


def test_head_timestamp_is_requested_stored_and_clamps_from_date(tmp_path, monkeypatch):
    ib = FakeIb(monkeypatch, head=dt(2024, 1, 2))
    cfg = _config(tmp_path, [], [])
    st = StorageType(from_date="2020-01-01", to_date="2024-01-02T23:00", timeframe="1 hour")

    res = history_loader.load_symbol_history(cfg, "AAPL", st)
    assert ib.head_calls == 1
    assert res["head_timestamp"] == dt(2024, 1, 2).isoformat()
    assert res["rows_inserted"] == 24 and res["requests"] == 1      # only 2024-01-02, not from 2020

    conn = sqlite_storage.open_connection(cfg.config_db_path)
    rows = conn.execute("SELECT symbol, exchange, useRTH, whatToShow, currency, timeframe, "
                        "dataTableName, headTimeStamp FROM equities").fetchall()
    conn.close()
    assert rows == [("AAPL", "SMART", "False", "TRADES", "USD", 60,
                     "AAPL_SMART_TRADES_USD_1_HOUR_RTH_FALSE", "2024-01-02 00:00:00+00:00")]


def test_head_timestamp_from_config_db_is_reused_across_timeframes(tmp_path, monkeypatch):
    cfg = _config(tmp_path, [], [])
    conn = sqlite_storage.open_connection(cfg.config_db_path)
    sqlite_storage.ensure_equities_table(conn, "equities")
    sqlite_storage.save_head_timestamp(conn, "equities", "AAPL_SMART_TRADES_USD_1_DAY_RTH_FALSE", "AAPL",
                                       "SMART", "TRADES", "USD", False, 1440, dt(2024, 1, 2))
    conn.close()

    ib = FakeIb(monkeypatch, head=dt(1999, 1, 1))
    st = StorageType(from_date="2020-01-01", to_date="2024-01-02T23:00", timeframe="1 hour")
    res = history_loader.load_symbol_history(cfg, "AAPL", st)
    assert ib.head_calls == 0                                        # no reqHeadTimeStamp
    assert res["head_timestamp"] == dt(2024, 1, 2).isoformat()
    assert res["rows_inserted"] == 24


def test_nothing_downloaded_when_head_after_to_date(tmp_path, monkeypatch):
    ib = FakeIb(monkeypatch, head=dt(2025, 1, 1))
    cfg = _config(tmp_path, [], [])
    res = history_loader.load_symbol_history(cfg, "NEWIPO", StorageType(from_date="2020-01-01", to_date="2024-01-01"))
    assert res["skipped"] is True and res["requests"] == 0 and ib.bar_calls == []


def test_daily_bars_are_requested_in_10_year_chunks(tmp_path, monkeypatch):
    ib = FakeIb(monkeypatch, head=dt(1990, 1, 1), max_pages=3)
    cfg = _config(tmp_path, [], [])
    history_loader.load_symbol_history(cfg, "AAPL", StorageType(from_date="1980-01-01", to_date="2024-01-01",
                                                                timeframe="1 day"))
    assert ib.bar_calls and all(dur == "10 Y" for _, dur in ib.bar_calls)


def test_load_symbol_history_rejects_inverted_range(tmp_path):
    cfg = _config(tmp_path, [], [])
    with pytest.raises(ValueError):
        history_loader.load_symbol_history(cfg, "AAPL", StorageType(from_date="2024-02-01", to_date="2024-01-01"))


def test_merge_storage_type_applies_overrides():
    base = StorageType(from_date="2000-01-01", timeframe="1 day")
    merged = merge_storage_type(StockEntry("MSFT", {"from_date": "2010-01-01"}), base)
    assert merged == StorageType(from_date="2010-01-01", timeframe="1 day")


def test_load_all_from_config_merges_and_captures_errors(tmp_path, monkeypatch):
    seen = []

    def fake_load(config, symbol, st, client_id=None):
        seen.append((symbol, st.timeframe, st.from_date))
        if symbol == "BAD":
            raise RuntimeError("boom")
        return {"symbol": symbol}

    monkeypatch.setattr(history_loader, "load_symbol_history", fake_load)
    cfg = _config(
        tmp_path,
        stocks=[{"symbol": "AAPL"}, {"symbol": "MSFT", "from_date": "2010-01-01"}, {}, {"symbol": "BAD"}],
        storage_types=[{"from_date": "2000-01-01", "timeframe": "1 day"},
                       {"from_date": "2000-01-01", "timeframe": "1 hour"}],
    )
    results = history_loader.load_all_from_config(cfg)

    assert seen == [
        ("AAPL", "1 day", "2000-01-01"), ("AAPL", "1 hour", "2000-01-01"),
        ("MSFT", "1 day", "2010-01-01"), ("MSFT", "1 hour", "2010-01-01"),
        ("BAD", "1 day", "2000-01-01"), ("BAD", "1 hour", "2000-01-01"),
    ]
    assert results[-1] == {"symbol": "BAD", "timeframe": "1 hour", "whatToShow": "TRADES", "error": "boom"}
    assert len(results) == 6
