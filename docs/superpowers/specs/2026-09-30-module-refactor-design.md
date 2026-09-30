# Module refactor: design

Date: 2026-09-30
Status: awaiting review

## Goal

Restructure the IBKR historical data loader so that:

1. Every `.py` file has a name that says what it does and opens with a module docstring describing its purpose.
2. Functionality is split by concern: configuration, logging, rate limiting, time helpers, SQLite storage, IBKR access, business logic, and command-line entry points.

## Constraints (agreed with Alex)

- The code is only run as scripts (the `.bat` file / command line); nothing imports it from outside. Clean rename, no compatibility shims.
- Flat layout: all modules stay at the repository root (option B), no package folders.
- Pure restructure: downloaded data, SQLite file locations, table names, table schema, CSV file names and log format stay identical. No new features.

## Current state (problems being fixed)

| File | Today | Problem |
|---|---|---|
| `main.py` | PyCharm "Hi, PyCharm" sample | Dead code |
| `e_logger.py` | Logger setup | Unclear name |
| `instruments.py` | Rate limiter + call timing for IB methods | Misleading name |
| `history_range_loader.py` | Config parsing, date helpers, SQLite, IBKR connection, gap detection, batch loop, CLI | Six concerns in one file |
| `db_to_csv.py` | Config parsing, duplicated helpers, SQLite reads, CSV export, CLI | Duplicates helpers; reads a non-existent `defaults` config key |
| `data_cleaner.py` | OHLCV validation, CLI | Imports `db_reader` from `../strategies`, which is not in this repo |

Duplicated today: `_to_utc_dt`, `_normalize_bar_size`, `_table_name`, `_db_path_for_symbol`. `config.json` is parsed at import time in three modules, each slightly differently.

## Target layout

```
config_loader.py          load + validate config.json into typed settings
app_logging.py            console + timestamped file logger
api_rate_limiter.py       sliding-window rate limits and call timing for API methods
time_utils.py             UTC parsing, bar-size normalising, minutes per bar, IBKR duration strings
sqlite_storage.py         all SQLite access for per-symbol databases
ibkr_client.py            IB Gateway connection + one historical-bars request
history_loader.py         business logic: gap detection, paged download, batch over config
csv_exporter.py           business logic: SQLite -> CSV for one or all symbols
ohlcv_validator.py        business logic: OHLCV sanity checks and suspicious-move detection
run_history_load.py       CLI: download history (single symbol or whole config)
run_csv_export.py         CLI: export to CSV
run_data_validation.py    CLI: validate CSV file or whole database
run_history_loader.bat    points at run_history_load.py
tests/                    pytest suite
requirements.txt
.gitignore
```

Deleted: `main.py`, `e_logger.py`, `instruments.py`, `history_range_loader.py`, `db_to_csv.py`, `data_cleaner.py`. `.idea/` is removed from git tracking and ignored.

## Dependency rules

```
run_*.py  ->  history_loader / csv_exporter / ohlcv_validator
              -> ibkr_client, sqlite_storage, time_utils, api_rate_limiter
              -> config_loader (types only), app_logging
```

- Only `run_*.py` files parse arguments, load `config.json`, and call business logic. Nothing reads config or touches the filesystem at import time.
- Business modules receive settings as arguments (an `AppConfig` or its parts); they never locate `config.json` themselves.
- `sqlite_storage` and `ibkr_client` do not import each other; `history_loader` joins them.
- `time_utils` and `api_rate_limiter` depend on nothing inside the project except `app_logging`.

## Module responsibilities

### `config_loader.py`
- Frozen dataclasses: `ConnectionSettings` (host, port, timeout, raise_sync_errors), `IbkrSettings` (client id, format_date, request timeout, max bars per call, rate-limit group name, rate limits as `dict[int, int]`), `StorageType` (from/to date, timeframe, useRTH, whatToShow, exchange, currency), `StockEntry` (symbol + optional overrides), `AppConfig` (db folder, ibkr, storage_types, stocks).
- `load_config(path) -> AppConfig`. Same defaults as the current code (e.g. port 4002, rate limits `{1: 9, 60: 59}` when missing). Accepts the current `config.json` unchanged.
- `DEFAULT_CONFIG_PATH` = `config.json` next to the module.

### `app_logging.py`
- `get_logger(name)` with identical behaviour to `e_logger.get_logger`: logs to stdout and `logs/mkt-data-loader-YYYYMMDDHHmm.log`, flushes every record, same format string.

### `api_rate_limiter.py`
- Contents of `instruments.py` renamed: `api_limitations_checker` becomes `apply_rate_limits(target, method_names, *, group, limits)`, `get_group_stats` is kept. Internals unchanged.
- The patching of `IB.reqHistoricalData` moves out of import time into `ibkr_client.install_rate_limits(ibkr_settings)`, called once by `run_history_load.py`.

### `time_utils.py`
- `DateLike` type alias, `to_utc_datetime`, `normalize_bar_size`, `minutes_per_bar`, `ibkr_duration_string`. Logic copied verbatim from `history_range_loader.py`.

### `sqlite_storage.py`
- `db_path_for_symbol(db_folder, symbol)`, `table_name(symbol, bar_size, use_rth, what_to_show, exchange, currency)`, `open_connection(db_path)` (creates folder), `ensure_table`, `existing_bounds`, `insert_bars`, `read_bars(...) -> DataFrame`, `list_symbols(db_folder)`.
- Table naming and schema byte-for-byte identical to today, so existing `.db` files keep working.

### `ibkr_client.py`
- `install_rate_limits(ibkr_settings)` (see above).
- `connect(ibkr_settings, client_id) -> IB` and `disconnect(ib)`.
- `fetch_bars(ib, ibkr_settings, contract, end_dt, duration, bar_size, what_to_show, use_rth) -> DataFrame` returning bars normalised to the storage columns (`timestamp, datetime, open, high, low, close, volume, average, barCount`), or an empty frame.

### `history_loader.py`
- `find_missing_ranges(bounds, from_dt, to_dt, bar_delta) -> list[tuple]` extracted as a pure function (gap before / after existing data, same rules as today).
- `load_symbol_history(config, symbol, storage_type, client_id=None) -> dict` replaces `load_historical_range_to_sql` + `run`; same summary dict keys.
- `load_all_from_config(config) -> list[dict]` replaces `run_from_config` / `run_all`; same per-stock override merge and per-task error capture.
- The backward paging loop (`_download_range`) stays here as a private function taking a `fetch_bars` callable, so it can be tested with a fake.

### `csv_exporter.py`
- `export_symbol_to_csv(config, symbol, from_date, to_date, ...)` and `export_all_to_csv(...)`, using `sqlite_storage.read_bars`. Defaults for missing options come from the first `StorageType` in config (fixes the dead `defaults` key); timeframe default stays `1 min` as today.

### `ohlcv_validator.py`
- `ValidationIssue`, `DataValidator`, `clean_historical_data` unchanged in logic.
- `scan_all_stocks(config, ...)` reads through `sqlite_storage.list_symbols` / `read_bars` instead of the external `db_reader`.

### `run_*.py`
- Same flags as the current `__main__` blocks of the files they replace. `run_history_load.py` with no `--symbol` keeps today's batch mode: an endless cycle that reloads `config.json`, runs `load_all_from_config`, logs the cycle summary, sleeps 60 seconds, and exits cleanly on Ctrl+C. With `--symbol` it loads one symbol once, with today's fallback defaults (`2001-01-01`, `2026-08-16`, `1 hour`, RTH false, `TRADES`).

## Error handling

Unchanged from today: invalid date ranges raise `ValueError`; the batch loader logs and records per-task errors and continues; the IB connection and SQLite connection are always closed in `finally`. A missing or malformed `config.json` raises a clear error from `load_config`.

## Testing

pytest, no network and no IB Gateway needed:
- `time_utils`: bar-size normalising, minutes per bar, duration strings, UTC parsing.
- `config_loader`: the repository's `config.json` loads; missing keys fall back to defaults.
- `sqlite_storage`: table name format, create/insert/dedupe/bounds/read on a temp DB.
- `history_loader`: `find_missing_ranges` cases; paging loop with a fake `fetch_bars` writing to a temp DB; batch override merge.
- `ohlcv_validator`: detects inverted high/low, non-positive prices, negative volume, large moves.
- `api_rate_limiter`: a limit of N calls per window blocks the (N+1)th call.

Before the refactor, characterisation tests for `time_utils` and table naming are written against the old code, then kept passing through the move.

## Out of scope

New data sources, schema changes, async IBKR usage, packaging as an installable package, changes to `config.json` format.
