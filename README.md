# IBKR Historical Data Loader

A modular Python application for downloading and caching US stock market historical OHLCV (Open, High, Low, Close, Volume) data from Interactive Brokers via the IBKR API, with US market holiday awareness.

## Features

- **Modular Architecture**: Separation of concerns across dedicated modules (API, storage, rate limiting, holidays, logging)
- **Per-Symbol SQLite Databases**: Each stock symbol gets its own database for efficient data organization
- **Config Database Caching**: 
  - Earliest available timestamp (`head_timestamp`) per symbol via `reqHeadTimeStamp`
  - US market holidays (100 years by default)
- **Intelligent Download Strategy**:
  - Sliding-window downloads (max 10 years per request for daily bars)
  - Gap detection to avoid redundant downloads
  - Automatic deduplication with `INSERT OR IGNORE`
- **Rate Limiting**: Per-window enforcement (1s and 60s windows) matching IBKR API constraints
- **US Market Holiday Integration**:
  - Automatic trading day validation
  - Date correction (weekends/holidays)
  - Multi-source fallback: randomapi.dev API
- **Flexible Configuration**:
  - YAML-like JSON config with per-stock overrides
  - Batch and single-symbol CLI modes
  - Configurable holiday year range

## Project Structure

```
.
├── config.json                    # Configuration file (stocks, date ranges, storage types)
├── config_loader.py              # Config parsing and validation
├── app_logging.py                # Logger setup (console + file)
├── api_rate_limiter.py           # Sliding-window rate limit enforcement
├── ibkr_client.py                # IBKR connection and API wrappers
├── time_utils.py                 # Date/duration helpers
├── sqlite_storage.py             # Per-symbol DB operations
├── holidays.py                   # US holiday caching & trading day checking
├── history_loader.py             # Download orchestration
├── csv_exporter.py               # SQLite → CSV export
├── run_history_load.py           # CLI entry point (batch/single mode)
├── run_csv_export.py             # CSV export CLI
├── run_history_loader.bat        # Windows batch launcher
├── requirements.txt              # Python dependencies
└── README.md                     # This file
```

## Installation

### Prerequisites

- Python 3.10+
- Interactive Brokers Gateway or Thinkorswim with API enabled
- Windows, Linux, or macOS

### Setup

1. **Clone/Download the project**

2. **Create virtual environment** (optional but recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

### config.json Structure

```json
{
  "config": {
    "holiday_years": 100,
    "database": {
      "folder_path": "D:\\DB\\",
      "config_file_path": "D:\\DB\\__config.db",
      "equities_config_table_name": "equities"
    },
    "ibapi": {
      "connection": {
        "host": "127.0.0.1",
        "port": 4002,
        "timeout": 10,
        "raise_sync_errors": true
      },
      "historical_data_client_id": 1,
      "format_date": 2,
      "request_history_data_timeout": 1000,
      "max_bars_per_call": 15000,
      "group_stats_historical_name": "IBAPI_HISTORICAL",
      "rate_limits": {
        "lims": {
          "1": 9,
          "60": 59
        }
      }
    }
  },
  "data_storage_types": [
    {
      "from_date": "2001-01-01",
      "to_date": "2026-09-30",
      "timeframe": "1 day",
      "useRTH": false,
      "whatToShow": "TRADES",
      "exchange": "SMART",
      "currency": "USD"
    }
  ],
  "stocks": [
    {
      "symbol": "AAPL",
      "timeframe": "1 hour"
    },
    {
      "symbol": "MSFT"
    }
  ]
}
```

**Key Parameters**:
- `holiday_years`: Number of years to pre-cache holidays (default: 100)
- `from_date` / `to_date`: Date range for historical data
- `timeframe`: Bar size (1 day, 1 hour, 5 mins, etc.)
- `useRTH`: Regular Trading Hours only (default: false = includes pre/after hours)
- `whatToShow`: TRADES, MIDPOINT, BID, ASK, etc.

## Usage

### Batch Mode: Download All Stocks to Earliest Data

```bash
python run_history_load.py --config config.json
```

- Loads config once
- Pre-caches 100 years of US holidays
- Downloads all stocks from `to_date` back to earliest available data
- Auto-exits when complete
- Ctrl+C to interrupt

### Single Symbol Mode

```bash
python run_history_load.py --symbol AAPL --from 2024-01-01 --to 2024-12-31 --timeframe "1 day"
```

Overrides default from config.json for a single download.

### Export to CSV

```bash
# Export all symbols
python run_csv_export.py --all

# Export single symbol
python run_csv_export.py --symbol AAPL
```

Exports SQLite data to CSV files.

## How It Works

### Startup

1. **Load config.json** (once in batch mode)
2. **Connect to IBKR Gateway** (port 4002 by default)
3. **Install rate limits** (1s and 60s window enforcement)
4. **Pre-cache holidays** (100 years by default from randomapi.dev)
5. **Create config DB** if missing (stores head timestamps and holidays)

### Download Process

For each stock × storage_type:

1. **Check earliest available** (`head_timestamp`):
   - Query config DB cache first
   - If miss, call `reqHeadTimeStamp` via IBKR
   - Store in config DB for future runs

2. **Correct dates to trading days**:
   - Skip weekends and US market holidays
   - Move `from_date` forward, `to_date` backward to nearest trading day

3. **For daily bars**: Restrict `to_date` to before today (data only available after market close)

4. **Detect gaps**:
   - Query per-symbol DB for existing data range
   - Identify ranges still needed (before earliest / after latest)

5. **Download in chunks**:
   - Max 10-year chunks for daily bars (IBKR limit)
   - Sliding-window paging backward from `to_date` to `from_date`
   - Respect rate limits (9 req/sec, 59 req/min)

6. **Deduplicate and insert**:
   - `INSERT OR IGNORE` prevents duplicates
   - Track rows inserted per request

### Continuous Iterations (Batch Mode)

- After processing all stocks once, re-scan for new gaps
- Stop when no new data is downloaded (all stocks complete)
- Each iteration is independent; no state between iterations

## Database Schema

### Per-Symbol Database (`D:\DB\{SYMBOL}.db`)

**Table**: `{SYMBOL}_SMART_TRADES_USD_{TIMEFRAME}_RTH_{TRUE|FALSE}`

```sql
CREATE TABLE ... (
    datetime    TEXT NOT NULL UNIQUE,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      INTEGER,
    barCount    INTEGER,
    wap         REAL
)
```

### Config Database (`D:\DB\__config.db`)

**Table**: `equities`
- `symbol`, `exchange`, `whatToShow`, `currency`, `useRTH`
- `min_per_bar`, `head_timestamp` (earliest available data point)

**Table**: `us_holidays`
- `date` (ISO format), `name`, `year`, `cached_at`

## Rate Limiting

IBKR API has strict rate limits:
- **1-second window**: Max 9 requests/sec
- **60-second window**: Max 59 requests/min

The loader enforces these with a sliding-window counter:

```
PERFORMANCE {'group': 'IBAPI_HISTORICAL', 
             'window_counts': {1: 9, 60: 59}, 
             'lims': {1: 9, 60: 59}, 
             'wait_s': 0.56}
```

Automatic delays are applied between requests if limits are reached.

## Logging

Logs are written to:
- **Console**: Real-time INFO and above
- **File**: `F:\trading\code\mkt-historical-data-loader\mkt-historical-data-loader\run_history_load.log`

Log levels:
- `DEBUG`: Holiday cache hits, verbose state
- `INFO`: Downloads, counts, progress
- `ERROR`: API failures, malformed dates

## Holidays

US market holidays are fetched from **randomapi.dev** (free, no API key required):

```
GET https://randomapi.dev/api/holidays?country=US&year=2026&type=any
```

Holidays are cached in `us_holidays` table for 100 years (configurable).

## Troubleshooting

### "Failed to fetch holidays for YYYY"
- Check internet connectivity
- Verify randomapi.dev is accessible
- Check app logs for detailed error message

### "No bars returned, stopping"
- Symbol may not exist on exchange
- Date range may be outside trading history
- Symbol may not trade during requested timeframe (e.g., pre/post-market)

### Rate limit delays
- Expected behavior; loader will pause and retry
- Check `wait_s` in PERFORMANCE log line

### No data for recent dates
- For daily bars: data is available only after market close
- Script automatically restricts `to_date` to previous trading day

## Requirements

```
ib_async>=2.0          # IBKR API
pandas>=2.0            # Data manipulation
pytest>=8.0            # Testing (optional)
requests>=2.28         # HTTP (for holiday API)
```

## License

This project is provided as-is for educational and trading purposes.
