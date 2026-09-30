import os

import pytest

from config_loader import (
    DEFAULT_CONFIG_PATH, ConfigError, StorageType, load_config, parse_config,
)


def test_repository_config_loads():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.db_folder
    assert cfg.storage_types
    assert cfg.stocks and all(s.symbol for s in cfg.stocks)
    assert all(isinstance(k, int) for k in cfg.ibkr.rate_limits)


def test_repository_clsk_config_loads():
    path = os.path.join(os.path.dirname(DEFAULT_CONFIG_PATH), "config.json")
    cfg = load_config(path)
    assert [s.symbol for s in cfg.stocks] == ["CLSK"]
    assert cfg.ibkr.historical_data_client_id == 3
    assert cfg.ibkr.rate_limits == {1: 5, 600: 59}


def test_missing_keys_fall_back_to_defaults():
    cfg = parse_config({})
    assert cfg.db_folder == "D:\\DB\\"
    assert cfg.ibkr.connection.host == "127.0.0.1"
    assert cfg.ibkr.connection.port == 4002
    assert cfg.ibkr.historical_data_client_id == 1
    assert cfg.ibkr.max_bars_per_call == 15000
    assert cfg.ibkr.rate_limit_group == "IBAPI_HISTORICAL"
    assert cfg.ibkr.rate_limits == {1: 9, 60: 59}
    assert cfg.storage_types == [] and cfg.stocks == []


def test_stock_overrides_and_storage_type_defaults():
    cfg = parse_config({
        "data_storage_types": [{"timeframe": "5 mins"}],
        "stocks": [{"symbol": "MSFT", "from_date": "2010-01-01", "unknown": 1}],
    })
    assert cfg.storage_types == [StorageType(timeframe="5 mins")]
    assert cfg.stocks[0].overrides == {"from_date": "2010-01-01"}


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "nope.json"))


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(p))


def test_malformed_rate_limits_raise():
    with pytest.raises(ConfigError):
        parse_config({"config": {"ibapi": {"rate_limits": {"lims": {"x": 1}}}}})
