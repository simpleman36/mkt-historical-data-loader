import pandas as pd

import sqlite_storage
from config_loader import parse_config
from csv_exporter import export_all_to_csv, export_symbol_to_csv
from test_sqlite_storage import make_bars


def _seed(db_folder, symbol, table):
    conn = sqlite_storage.open_connection(sqlite_storage.db_path_for_symbol(db_folder, symbol))
    sqlite_storage.ensure_table(conn, table)
    sqlite_storage.insert_bars(conn, table, make_bars([1704067200, 1704067260]))   # 2024-01-01 00:00, 00:01
    conn.close()


def test_export_symbol_default_filename_is_table_name(tmp_path, monkeypatch):
    db = tmp_path / "db"
    _seed(str(db), "AAPL", "AAPL_SMART_TRADES_USD_1_MIN_RTH_FALSE")
    cfg = parse_config({"config": {"database": {"folder_path": str(db)}}})
    monkeypatch.chdir(tmp_path)

    assert export_symbol_to_csv(cfg, "AAPL", "2024-01-01", "2024-01-02") is True
    out = pd.read_csv(tmp_path / "AAPL_SMART_TRADES_USD_1_MIN_RTH_FALSE.csv")
    assert list(out.columns) == sqlite_storage.BAR_COLUMNS
    assert out["timestamp"].tolist() == [1704067200, 1704067260]


def test_export_symbol_defaults_come_from_first_storage_type(tmp_path):
    db = tmp_path / "db"
    _seed(str(db), "AAPL", "AAPL_SMART_MIDPOINT_USD_1_MIN_RTH_TRUE")
    cfg = parse_config({
        "config": {"database": {"folder_path": str(db)}},
        "data_storage_types": [{"useRTH": True, "whatToShow": "MIDPOINT"}],
    })
    out = tmp_path / "out.csv"
    assert export_symbol_to_csv(cfg, "AAPL", "2024-01-01", "2024-01-02", output_file=str(out)) is True
    assert out.exists()


def test_export_symbol_missing_db_or_table(tmp_path):
    cfg = parse_config({"config": {"database": {"folder_path": str(tmp_path)}}})
    assert export_symbol_to_csv(cfg, "NOPE", "2024-01-01", "2024-01-02") is False
    _seed(str(tmp_path), "AAPL", "OTHER")
    assert export_symbol_to_csv(cfg, "AAPL", "2024-01-01", "2024-01-02") is False


def test_export_all_writes_symbol_named_files(tmp_path):
    db = tmp_path / "db"
    for sym in ["AAPL", "MSFT"]:
        _seed(str(db), sym, f"{sym}_SMART_TRADES_USD_1_MIN_RTH_FALSE")
    cfg = parse_config({"config": {"database": {"folder_path": str(db)}}})
    out_dir = tmp_path / "csv"

    assert export_all_to_csv(cfg, "2024-01-01", "2024-01-02", output_dir=str(out_dir)) == {"AAPL": True, "MSFT": True}
    assert sorted(p.name for p in out_dir.iterdir()) == ["AAPL.csv", "MSFT.csv"]
