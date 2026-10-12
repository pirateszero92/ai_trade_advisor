"""
Unit Tests for Market Facts & Market State (FACT-001 to FACT-008, STATE-001)
"""

from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd
import pytest

from app.facts import MarketFacts, MarketFactsEngine, VolatilityFact
from app.market_state import MarketState, MarketStateEngine


def _generate_synthetic_ohlcv(bars: int = 50, trend: float = 0.5) -> pd.DataFrame:
    timestamps = [
        pd.Timestamp("2026-09-23 00:00:00", tz="UTC") + timedelta(minutes=15 * i)
        for i in range(bars)
    ]
    np.random.seed(42)
    base = 100.0
    records = []
    current = base
    for _ in range(bars):
        current += trend + np.random.uniform(-0.5, 0.5)
        o = current
        h = o + np.random.uniform(0.1, 1.0)
        l = o - np.random.uniform(0.1, 1.0)
        c = np.random.uniform(l, h)
        v = np.random.uniform(100, 1000)
        records.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
    return pd.DataFrame(records, index=timestamps)


def test_market_facts_engine():
    df = _generate_synthetic_ohlcv(bars=40, trend=0.8)
    engine = MarketFactsEngine()
    facts = engine.calculate(df, symbol="BTC/USDT", timeframe="15m")

    assert isinstance(facts, MarketFacts)
    assert facts.symbol == "BTC/USDT"
    assert facts.timeframe == "15m"
    assert facts.current_price > 0
    assert facts.volatility.atr > 0
    assert isinstance(facts.structure.has_bos, bool)


def test_market_state_classification():
    df = _generate_synthetic_ohlcv(bars=30)
    facts = MarketFactsEngine().calculate(df, symbol="SOL/USDT", timeframe="15m")
    state_engine = MarketStateEngine()

    # 1. Normal classification
    state_res = state_engine.classify(facts)
    assert state_res.state in {MarketState.TREND, MarketState.RANGE, MarketState.UNKNOWN}

    # 2. Compression override
    facts.volatility.is_compression = True
    comp_res = state_engine.classify(facts)
    assert comp_res.state == MarketState.COMPRESSION

    # 3. High volatility override
    facts.volatility.is_compression = False
    facts.volatility.atr_percentile = 95.0
    hv_res = state_engine.classify(facts)
    assert hv_res.state == MarketState.HIGH_VOLATILITY
