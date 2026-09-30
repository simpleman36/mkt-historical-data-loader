"""
Load and validate ``config.json`` into typed, immutable settings.

Only the ``run_*.py`` entry points call :func:`load_config`; every other module
receives an :class:`AppConfig` (or one of its parts) as an argument.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Fallbacks used when a key is missing from config.json (same values as before the refactor).
DEFAULT_DB_FOLDER = "D:\\DB\\"
DEFAULT_CONFIG_DB_PATH = "D:\\DB\\__config.db"
DEFAULT_EQUITIES_TABLE = "equities"
DEFAULT_RATE_LIMITS = {1: 9, 60: 59}
DEFAULT_FROM_DATE = "2001-01-01"
DEFAULT_TO_DATE = "2026-08-16"
DEFAULT_TIMEFRAME = "1 hour"
DEFAULT_HOLIDAY_YEARS = 100


class ConfigError(ValueError):
    """Raised when config.json is missing or malformed."""


@dataclass(frozen=True)
class ConnectionSettings:
    host: str = "127.0.0.1"
    port: int = 4002
    timeout: float = 10
    raise_sync_errors: bool = True


@dataclass(frozen=True)
class IbkrSettings:
    connection: ConnectionSettings = field(default_factory=ConnectionSettings)
    historical_data_client_id: int = 1
    format_date: int = 2
    request_timeout: float = 1000
    max_bars_per_call: int = 15000
    rate_limit_group: str = "IBAPI_HISTORICAL"
    rate_limits: dict[int, int] = field(default_factory=lambda: dict(DEFAULT_RATE_LIMITS))


@dataclass(frozen=True)
class StorageType:
    from_date: str = DEFAULT_FROM_DATE
    to_date: str = DEFAULT_TO_DATE
    timeframe: Any = DEFAULT_TIMEFRAME
    useRTH: bool = False
    whatToShow: str = "TRADES"
    exchange: str = "SMART"
    currency: str = "USD"


@dataclass(frozen=True)
class StockEntry:
    """A symbol plus optional per-symbol overrides of StorageType fields."""
    symbol: Optional[str]
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AppConfig:
    db_folder: str
    ibkr: IbkrSettings
    storage_types: list[StorageType]
    stocks: list[StockEntry]
    source_path: Optional[str] = None
    # Config DB: holds the "equities" table with each symbol's earliest available timestamp.
    config_db_path: str = DEFAULT_CONFIG_DB_PATH
    equities_table: str = DEFAULT_EQUITIES_TABLE
    # Number of years of holidays to pre-cache on startup
    holiday_years: int = DEFAULT_HOLIDAY_YEARS


_STORAGE_KEYS = ("from_date", "to_date", "timeframe", "useRTH", "whatToShow", "exchange", "currency")


def _parse_storage_type(raw: dict) -> StorageType:
    return StorageType(**{k: raw[k] for k in _STORAGE_KEYS if k in raw})


def _parse_stock(raw: dict) -> StockEntry:
    return StockEntry(
        symbol=raw.get("symbol"),
        overrides={k: raw[k] for k in _STORAGE_KEYS if k in raw},
    )


def parse_config(data: dict, source_path: Optional[str] = None) -> AppConfig:
    """Build an AppConfig from an already-decoded config dict."""
    if not isinstance(data, dict):
        raise ConfigError(f"Config root must be a JSON object ({source_path})")

    cfg = data.get("config", {})
    ib = cfg.get("ibapi", {})
    conn = ib.get("connection", {})
    lims_raw = ib.get("rate_limits", {}).get("lims", DEFAULT_RATE_LIMITS)

    try:
        connection = ConnectionSettings(
            host=conn.get("host", "127.0.0.1"),
            port=conn.get("port", 4002),
            timeout=conn.get("timeout", 10),
            raise_sync_errors=conn.get("raise_sync_errors", True),
        )
        ibkr = IbkrSettings(
            connection=connection,
            historical_data_client_id=ib.get("historical_data_client_id", 1),
            format_date=ib.get("format_date", 2),
            request_timeout=ib.get("request_history_data_timeout", 1000),
            max_bars_per_call=ib.get("max_bars_per_call", 15000),
            rate_limit_group=ib.get("group_stats_historical_name", "IBAPI_HISTORICAL"),
            rate_limits={int(k): int(v) for k, v in lims_raw.items()},
        )
        storage_types = [_parse_storage_type(s) for s in data.get("data_storage_types", [])]
        stocks = [_parse_stock(s) for s in data.get("stocks", [])]
    except (AttributeError, TypeError, ValueError) as exc:
        raise ConfigError(f"Malformed config {source_path or ''}: {exc}") from exc

    db = cfg.get("database", {})
    return AppConfig(
        db_folder=db.get("folder_path", DEFAULT_DB_FOLDER),
        config_db_path=db.get("config_file_path", DEFAULT_CONFIG_DB_PATH),
        equities_table=db.get("equities_config_table_name", DEFAULT_EQUITIES_TABLE),
        holiday_years=cfg.get("holiday_years", DEFAULT_HOLIDAY_YEARS),
        ibkr=ibkr,
        storage_types=storage_types,
        stocks=stocks,
        source_path=source_path,
    )


def load_config(path: str = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Read and parse config.json. Raises ConfigError if missing or malformed."""
    if not os.path.isfile(path):
        raise ConfigError(f"Config file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc
    return parse_config(data, source_path=path)
