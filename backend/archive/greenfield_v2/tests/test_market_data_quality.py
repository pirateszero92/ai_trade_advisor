"""
Unit Tests for Market Data & Data Quality (DATA-001 to DATA-005)
"""

from datetime import datetime, timezone, timedelta
import pandas as pd
import pytest

from app.domain.reason_codes import ReasonCode
from app.market_data import (
    DataQuality,
    DataQualityMetadata,
    MarketQuote,
    build_closed_candle_frame,
)


def test_data_quality_metadata():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    ts = now - timedelta(milliseconds=150)
    meta = DataQualityMetadata.create(
        quality=DataQuality.EXCHANGE_NATIVE,
        source="binance_ws",
        exchange_timestamp=ts,
        now=now,
    )
    assert meta.quality == DataQuality.EXCHANGE_NATIVE
    assert meta.source == "binance_ws"
    assert pytest.approx(meta.age_ms, abs=1.0) == 150.0


def test_market_quote_valid():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    quote = MarketQuote(
        symbol="BTC/USDT",
        bid=50000.0,
        ask=50002.0,
        last=50001.0,
        timestamp=now - timedelta(seconds=1),
    )
    assert quote.spread_pct == pytest.approx(2.0 / 50000.0)
    assert quote.age_seconds(now=now) == 1.0

    valid, reason, msg = quote.validate_executable(now=now)
    assert valid is True
    assert reason == ReasonCode.DATA_QUALITY_OK


def test_market_quote_invariants():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # Inverted
    q_inv = MarketQuote(symbol="X", bid=100.0, ask=99.0, last=99.5, timestamp=now)
    valid, reason, _ = q_inv.validate_executable(now=now)
    assert not valid
    assert reason == ReasonCode.INVALID_BID_ASK

    # Stale
    q_stale = MarketQuote(symbol="X", bid=100.0, ask=100.1, last=100.05, timestamp=now - timedelta(seconds=10))
    valid, reason, _ = q_stale.validate_executable(max_age_seconds=5.0, now=now)
    assert not valid
    assert reason == ReasonCode.QUOTE_STALE

    # Future skew
    q_future = MarketQuote(symbol="X", bid=100.0, ask=100.1, last=100.05, timestamp=now + timedelta(seconds=5))
    valid, reason, _ = q_future.validate_executable(max_future_skew_seconds=1.0, now=now)
    assert not valid
    assert reason == ReasonCode.QUOTE_FUTURE_SKEW

    # Spread too wide
    q_spread = MarketQuote(symbol="X", bid=100.0, ask=102.0, last=101.0, timestamp=now)
    valid, reason, _ = q_spread.validate_executable(max_spread_pct=0.01, now=now)
    assert not valid
    assert reason == ReasonCode.SPREAD_TOO_WIDE


def test_closed_candle_builder_prevents_leakage():
    # 15m intervals: 12:00 -> close 12:15; 12:15 -> close 12:30
    timestamps = [
        pd.Timestamp("2026-09-23 12:00:00", tz="UTC"),
        pd.Timestamp("2026-09-23 12:15:00", tz="UTC"),
        pd.Timestamp("2026-09-23 12:30:00", tz="UTC"),  # currently forming at 12:35
    ]
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [1000, 1500, 800],
        },
        index=timestamps,
    )

    # At 12:35 UTC, only the first two 15m candles are closed!
    now = pd.Timestamp("2026-09-23 12:35:00", tz="UTC")
    closed = build_closed_candle_frame(df, "15m", now=now)

    assert len(closed) == 2
    assert closed.index[-1] == pd.Timestamp("2026-09-23 12:15:00", tz="UTC")
    # Forming candle 12:30 is NOT included!
    assert pd.Timestamp("2026-09-23 12:30:00", tz="UTC") not in closed.index
