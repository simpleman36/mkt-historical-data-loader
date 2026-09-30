"""
IB Gateway / TWS access through ib_async.

- install_rate_limits: patch IB.reqHistoricalData / reqHeadTimeStamp with the configured rate limits (call once)
- connect / disconnect: manage one IB connection
- fetch_head_timestamp: earliest available data point for a contract (reqHeadTimeStamp), logged
- fetch_bars: one historical-bars request, normalised to the storage columns
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from ib_async import IB, Contract, Stock, util

from api_rate_limiter import apply_rate_limits, get_group_stats
from app_logging import module_logger
from config_loader import IbkrSettings

BAR_COLUMNS = ["timestamp", "datetime", "open", "high", "low", "close", "volume", "average", "barCount"]

logger = module_logger(__name__)

_rate_limits_installed = False


def install_rate_limits(ibkr: IbkrSettings) -> None:
    """Patch IB.reqHistoricalData and IB.reqHeadTimeStamp with the shared rate-limit group. Idempotent."""
    global _rate_limits_installed
    if _rate_limits_installed:
        return
    apply_rate_limits(
        IB,
        ["reqHistoricalData", "reqHeadTimeStamp"],
        group=ibkr.rate_limit_group,
        limits=ibkr.rate_limits,
    )
    _rate_limits_installed = True


def connect(ibkr: IbkrSettings, client_id: int) -> IB:
    ib = IB()
    conn = ibkr.connection
    ib.connect(
        host            = conn.host,
        port            = conn.port,
        clientId        = client_id,
        timeout         = conn.timeout,
        raiseSyncErrors = conn.raise_sync_errors,
    )
    return ib


def disconnect(ib: IB) -> None:
    if ib.isConnected():
        ib.disconnect()


def stock_contract(symbol: str, exchange: str, currency: str) -> Contract:
    return Stock(symbol, exchange, currency)


def fetch_head_timestamp(
    ib: IB,
    ibkr: IbkrSettings,
    contract: Contract,
    what_to_show: str,
    use_rth: bool,
) -> Optional[datetime]:
    """
    Ask IBKR for the earliest available data point (reqHeadTimeStamp).
    Returns a UTC-aware datetime, or None when IBKR has no answer.
    """
    logger.info(f"[{contract.symbol}] REQ reqHeadTimeStamp: whatToShow={what_to_show} useRTH={use_rth}")
    try:
        head = ib.reqHeadTimeStamp(contract, whatToShow=what_to_show, useRTH=use_rth, formatDate=2)
    except Exception as exc:
        logger.error(f"[{contract.symbol}] reqHeadTimeStamp failed: {exc}")
        return None
    finally:
        logger.info(f"[{contract.symbol}]  PERFORMANCE {get_group_stats(ibkr.rate_limit_group)}")

    if not isinstance(head, datetime):
        logger.warning(f"[{contract.symbol}] reqHeadTimeStamp returned no timestamp ({head!r})")
        return None

    head = head.astimezone(timezone.utc) if head.tzinfo else head.replace(tzinfo=timezone.utc)
    logger.info(f"[{contract.symbol}]  → earliest available data point: {head.isoformat()}")
    return head


def fetch_bars(
    ib: IB,
    ibkr: IbkrSettings,
    contract: Contract,
    end_dt: datetime,
    duration: str,
    bar_size: str,
    what_to_show: str,
    use_rth: bool,
) -> pd.DataFrame:
    """
    Request one page of historical bars ending at *end_dt*.

    Returns a DataFrame with BAR_COLUMNS (naive UTC ``datetime`` column and Unix
    ``timestamp``), or an empty DataFrame when IBKR returns nothing.
    """
    bars = ib.reqHistoricalData(
        contract       = contract,
        endDateTime    = end_dt,
        durationStr    = duration,
        barSizeSetting = bar_size,
        whatToShow     = what_to_show,
        useRTH         = use_rth,
        formatDate     = ibkr.format_date,
        timeout        = ibkr.request_timeout,
    )
    if not bars:
        return pd.DataFrame(columns=BAR_COLUMNS)

    df = util.df(bars)
    if df is None or df.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)

    # Normalize datetime column
    df["date"] = pd.to_datetime(df["date"])
    df["date"] = df["date"].dt.tz_localize(None) if df["date"].dt.tz is not None else df["date"]
    df = df.rename(columns={"date": "datetime"})
    df["datetime"]  = df["datetime"].astype("datetime64[ns]")
    df["timestamp"] = (df["datetime"].astype("int64") // 10 ** 9).astype("int64")
    return df[BAR_COLUMNS]
