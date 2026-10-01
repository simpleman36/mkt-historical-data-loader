"""
All SQLite access for the per-symbol bar databases.

Layout: one file per symbol, ``<db_folder>/<SYMBOL>.db``, with one table per
(symbol, exchange, whatToShow, currency, bar size, RTH) combination, named e.g.
``AAPL_SMART_TRADES_USD_1_HOUR_RTH_FALSE``. Rows are keyed by Unix timestamp.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

BAR_COLUMNS = ["timestamp", "datetime", "open", "high", "low", "close", "volume", "average", "barCount"]


def db_path_for_symbol(db_folder: str, symbol: str) -> str:
    return os.path.join(db_folder, f"{symbol}.db")


def table_name(
    symbol: str,
    bar_size: str,
    use_rth: bool,
    what_to_show: str,
    exchange: str = "SMART",
    currency: str = "USD",
) -> str:
    raw = f"{symbol}_{exchange}_{what_to_show}_{currency}_{bar_size}_RTH_{str(use_rth).upper()}"
    return re.sub(r"[^A-Za-z0-9]", "_", raw).upper()


def open_connection(db_path: str) -> sqlite3.Connection:
    """Open (creating the folder if needed) the SQLite file at *db_path*."""
    folder = os.path.dirname(db_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    return sqlite3.connect(db_path)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def ensure_table(conn: sqlite3.Connection, table: str) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            timestamp  UNSIGNED BIG INT NOT NULL PRIMARY KEY,
            datetime   TEXT,
            open       DECIMAL(10, 5),
            high       DECIMAL(10, 5),
            low        DECIMAL(10, 5),
            close      DECIMAL(10, 5),
            volume     INTEGER,
            average    DECIMAL(10, 5),
            barCount   INTEGER
        )
    """)
    conn.commit()


def existing_bounds(
    conn: sqlite3.Connection,
    table: str,
    from_dt: datetime,
    to_dt: datetime,
) -> tuple[datetime, datetime] | None:
    """
    Return (min_datetime, max_datetime) of rows already stored in *table*
    within [from_dt, to_dt], or None if the table has no rows in that window.
    Datetimes are returned as UTC-aware.
    """
    if not table_exists(conn, table):
        return None

    from_ts = int(from_dt.timestamp())
    to_ts = int(to_dt.timestamp())
    cur = conn.execute(
        f"SELECT MIN(timestamp), MAX(timestamp) FROM {table} "
        f"WHERE timestamp >= ? AND timestamp <= ?",
        (from_ts, to_ts),
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        return None

    min_dt = datetime.fromtimestamp(row[0], tz=timezone.utc)
    max_dt = datetime.fromtimestamp(row[1], tz=timezone.utc)
    return min_dt, max_dt


def insert_bars(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    """Insert bars (columns = BAR_COLUMNS), ignoring timestamps already stored. Returns rows added."""
    if df.empty:
        return 0

    rows = [
        (
            int(r.timestamp),
            str(r.datetime),
            float(r.open),
            float(r.high),
            float(r.low),
            float(r.close),
            int(r.volume)   if r.volume   is not None else None,
            float(r.average) if r.average is not None else None,
            int(r.barCount) if r.barCount is not None else None,
        )
        for r in df.itertuples(index=False)
    ]

    before = conn.total_changes
    conn.executemany(
        f"""
        INSERT OR IGNORE INTO {table}
            (timestamp, datetime, open, high, low, close, volume, average, barCount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def read_bars(db_path: str, table: str, from_dt: datetime, to_dt: datetime) -> Optional[pd.DataFrame]:
    """
    Return bars in [from_dt, to_dt] ordered by timestamp, or None if the table
    does not exist. The caller checks that *db_path* exists first.
    """
    from_ts = int(from_dt.timestamp())
    to_ts = int(to_dt.timestamp())

    conn = sqlite3.connect(db_path)
    try:
        if not table_exists(conn, table):
            return None
        query = f"""
            SELECT timestamp, datetime, open, high, low, close, volume, average, barCount
            FROM   {table}
            WHERE  timestamp >= ? AND timestamp <= ?
            ORDER  BY timestamp ASC
        """
        return pd.read_sql_query(query, conn, params=(from_ts, to_ts))
    finally:
        conn.close()


def list_symbols(db_folder: str) -> list[str]:
    """Sorted symbols that have a .db file in *db_folder* (files starting with '_' are skipped)."""
    if not os.path.isdir(db_folder):
        return []
    return sorted(
        f[:-3]  # strip ".db"
        for f in os.listdir(db_folder)
        if f.lower().endswith(".db") and not f.startswith("_")
    )


# ---------------------------------------------------------------------------
# Config DB: "equities" table with the earliest available timestamp per series
# ---------------------------------------------------------------------------

def _migrate_equities_table(conn: sqlite3.Connection, table: str) -> None:
    """Add any missing columns and rename existing columns in equities table."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    columns = {row[1] for row in cur.fetchall()}

    # Add earliest_reached columns if missing
    if "earliest_reached" not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN earliest_reached BOOLEAN DEFAULT 0")

    if "earliest_reached_at" not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN earliest_reached_at TEXT")

    # Rename columns: headTimeStamp → ibkr_head_ts, tailTimeStamp → db_head_ts
    if "headTimeStamp" in columns and "ibkr_head_ts" not in columns:
        conn.execute(f"ALTER TABLE {table} RENAME COLUMN headTimeStamp TO ibkr_head_ts")

    if "tailTimeStamp" in columns and "db_head_ts" not in columns:
        conn.execute(f"ALTER TABLE {table} RENAME COLUMN tailTimeStamp TO db_head_ts")

    conn.commit()


def ensure_equities_table(conn: sqlite3.Connection, table: str) -> None:
    """Create the equities table (same schema as the existing __config.db) if it is missing."""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            symbol         TEXT,
            name           TEXT,
            exchange       TEXT NOT NULL DEFAULT 'SMART',
            useRTH         BOOLEAN NOT NULL DEFAULT 'False',
            whatToShow     CHARACTER(10) NOT NULL DEFAULT 'TRADES',
            currency       CHARACTER(10) NOT NULL DEFAULT 'USD',
            timeframe      INTEGER NOT NULL DEFAULT 1,
            dataTableName  TEXT NOT NULL DEFAULT 'AAPL_SMART_TRADES_USD_1MIN_RTH_FALSE' UNIQUE,
            earliest_reached BOOLEAN DEFAULT 0,
            earliest_reached_at TEXT,
            ibkr_head_ts   TEXT,
            db_head_ts     TEXT,
            id             INTEGER UNIQUE,
            PRIMARY KEY("id" AUTOINCREMENT)
        )
    """)
    conn.commit()

    # Migrate existing tables to add missing columns
    _migrate_equities_table(conn, table)


def get_head_timestamp(
    conn: sqlite3.Connection,
    table: str,
    symbol: str,
    exchange: str,
    what_to_show: str,
    currency: str,
    use_rth: bool,
) -> Optional[datetime]:
    """
    Earliest available timestamp stored for this series, or None.

    The head timestamp depends on symbol / exchange / whatToShow / currency / RTH,
    not on bar size, so any row for the same series (any timeframe) is reused.
    """
    row = conn.execute(
        f"SELECT ibkr_head_ts FROM {table} "
        f"WHERE symbol = ? AND exchange = ? AND whatToShow = ? AND currency = ? AND useRTH = ? "
        f"AND ibkr_head_ts IS NOT NULL AND ibkr_head_ts != '' "
        f"ORDER BY id LIMIT 1",
        (symbol, exchange, what_to_show, currency, str(use_rth)),
    ).fetchone()
    if row is None:
        return None
    dt = datetime.fromisoformat(row[0])
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def save_head_timestamp(
    conn: sqlite3.Connection,
    table: str,
    data_table_name: str,
    symbol: str,
    exchange: str,
    what_to_show: str,
    currency: str,
    use_rth: bool,
    timeframe_minutes: float,
    head_dt: datetime,
) -> None:
    """Insert or update the equities row for *data_table_name* with its head timestamp (UTC)."""
    head_utc = head_dt.astimezone(timezone.utc) if head_dt.tzinfo else head_dt.replace(tzinfo=timezone.utc)
    tf = int(timeframe_minutes) if float(timeframe_minutes).is_integer() else timeframe_minutes
    conn.execute(
        f"""
        INSERT INTO {table}
            (symbol, exchange, useRTH, whatToShow, currency, timeframe, dataTableName, ibkr_head_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dataTableName) DO UPDATE SET ibkr_head_ts = excluded.ibkr_head_ts
        """,
        (symbol, exchange, str(use_rth), what_to_show, currency, tf, data_table_name, str(head_utc)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Checkpoint: track last processed stock for resume on restart
# ---------------------------------------------------------------------------

def ensure_checkpoint_table(conn: sqlite3.Connection) -> None:
    """Create checkpoint table if missing."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS download_checkpoint (
            id          INTEGER PRIMARY KEY,
            last_symbol TEXT,
            updated_at  TEXT
        )
    """)
    conn.commit()


def save_checkpoint(conn: sqlite3.Connection, symbol: str) -> None:
    """Save the last processed stock symbol."""
    ensure_checkpoint_table(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO download_checkpoint (last_symbol, updated_at)
        VALUES (?, ?)
        ON CONFLICT(id) DO UPDATE SET last_symbol = excluded.last_symbol, updated_at = excluded.updated_at
    """, (symbol, now))
    conn.commit()


def get_checkpoint(conn: sqlite3.Connection) -> Optional[str]:
    """Get the last processed stock symbol (for resume)."""
    ensure_checkpoint_table(conn)
    cur = conn.execute("SELECT last_symbol FROM download_checkpoint LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Earliest data tracking: mark when stock has no more historical data
# ---------------------------------------------------------------------------

def mark_earliest_reached(
    conn: sqlite3.Connection,
    table: str,
    data_table_name: str,
) -> None:
    """Mark that this stock has reached its earliest available data."""
    # Ensure schema is up to date
    _migrate_equities_table(conn, table)

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        f"""
        UPDATE {table}
        SET earliest_reached = 1, earliest_reached_at = ?
        WHERE dataTableName = ?
        """,
        (now, data_table_name),
    )
    conn.commit()


def is_earliest_reached(
    conn: sqlite3.Connection,
    table: str,
    data_table_name: str,
) -> bool:
    """Check if this stock has already reached its earliest available data."""
    # Ensure schema is up to date
    _migrate_equities_table(conn, table)

    cur = conn.execute(
        f"SELECT earliest_reached FROM {table} WHERE dataTableName = ?",
        (data_table_name,),
    )
    row = cur.fetchone()
    return row and row[0] == 1 if row else False
