"""
Canonical Closed-Candle Builder (DATA-002)
Guarantees that analysis, setup detection, and replay operate exclusively
on fully closed candles. Completely eliminates forming-candle leakage and repainting.
"""

from datetime import datetime, timezone
import pandas as pd

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}


def build_closed_candle_frame(
    frame: pd.DataFrame,
    timeframe: str = "15m",
    *,
    now: datetime | pd.Timestamp | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """
    Filters raw provider OHLCV to strictly closed candles.
    Candle indexes represent interval-open timestamps.
    A candle is closed only when index + duration <= now.
    """
    if frame.empty:
        return frame.copy()

    tf = str(timeframe).lower()
    if tf == "execution":
        tf = "15m"
    duration_sec = TIMEFRAME_SECONDS.get(tf)
    if not duration_sec:
        duration_sec = 900

    current = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    else:
        current = current.tz_convert("UTC")

    result = frame.copy()
    idx = pd.to_datetime(result.index, utc=True, errors="coerce")
    valid_idx = ~idx.isna()

    close_times = idx + pd.to_timedelta(duration_sec, unit="s")
    closed_mask = valid_idx & (close_times <= current)

    result = result.loc[closed_mask]
    result.index = idx[closed_mask]
    result.index.name = frame.index.name or "timestamp"

    # Deduplicate keeping the latest authoritative print
    result = result[~result.index.duplicated(keep="last")].sort_index()

    if limit is not None and limit > 0:
        return result.tail(limit)
    return result
