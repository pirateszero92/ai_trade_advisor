"""Market-session and candle-freshness metadata for execution snapshots."""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


_NEW_YORK = ZoneInfo("America/New_York")
_TF_SECONDS = {
    "1m": 60, "2m": 120, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
    "1d": 86400,
}


def us_equity_market_status(
    last_candle_open: datetime,
    timeframe: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Describe the regular-session state without granting trading authority.

    Holidays are safely handled by the freshness check: if a weekday is an
    exchange holiday, no new closed candle arrives and execution remains
    blocked.  The UI labels this calendar approximation explicitly.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    candle = last_candle_open
    if candle.tzinfo is None:
        candle = candle.replace(tzinfo=timezone.utc)
    candle = candle.astimezone(timezone.utc)
    tf_seconds = _TF_SECONDS.get(str(timeframe).lower(), 900)
    candle_close = candle.timestamp() + tf_seconds
    age_seconds = max(0.0, current.timestamp() - candle_close)

    local = current.astimezone(_NEW_YORK)
    weekday = local.weekday() < 5
    clock = local.time().replace(tzinfo=None)
    regular_open = weekday and time(9, 30) <= clock < time(16, 0)
    if not weekday:
        state = "closed_weekend"
    elif clock < time(9, 30):
        state = "pre_market"
    elif clock >= time(16, 0):
        state = "after_hours"
    else:
        state = "regular_open"
    # During regular hours, tolerate one missing bar plus provider latency.
    freshness_limit = max(tf_seconds * 1.5, 120.0)
    candle_fresh = age_seconds <= freshness_limit
    return {
        "market": "US_EQUITY",
        "session_state": state,
        "regular_session_open": regular_open,
        "calendar_quality": "weekday_clock_plus_freshness",
        "last_closed_candle_at": datetime.fromtimestamp(
            candle_close, tz=timezone.utc
        ).isoformat(),
        "candle_age_seconds": round(age_seconds, 1),
        "freshness_limit_seconds": freshness_limit,
        "candle_fresh": candle_fresh,
        "fresh_for_execution": regular_open and candle_fresh,
        "reason": (
            "REGULAR_SESSION_AND_FRESH"
            if regular_open and candle_fresh
            else "US_REGULAR_SESSION_CLOSED"
            if not regular_open
            else "STALE_STOCK_CANDLE"
        ),
    }
