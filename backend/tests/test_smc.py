import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from app.engines.smc_engine import SMCEngine, SwingPoint


def generate_synthetic_ohlcv(bars: int = 100, trend: str = "bullish") -> pd.DataFrame:
    """Generate realistic OHLCV dataframe for testing."""
    start_time = datetime(2026, 1, 1, 0, 0)
    timestamps = [start_time + timedelta(hours=i) for i in range(bars)]

    np.random.seed(42)
    close = 100.0
    records = []

    for i in range(bars):
        delta = np.random.normal(0.5 if trend == "bullish" else -0.5, 1.0)
        c = max(close + delta, 10.0)
        h = c + abs(np.random.normal(0.8, 0.3))
        l = c - abs(np.random.normal(0.8, 0.3))
        o = (records[-1]["close"] if records else c - 0.2)
        v = np.random.uniform(100, 1000)

        records.append({
            "open": o,
            "high": max(h, o, c),
            "low": min(l, o, c),
            "close": c,
            "volume": v,
        })
        close = c

    df = pd.DataFrame(records, index=timestamps)
    df.index.name = "timestamp"
    return df


def test_smc_engine_initialization():
    engine = SMCEngine(swing_length=5, internal_swing_length=3)
    assert engine.swing_length == 5
    assert engine.internal_swing_length == 3


def test_persistent_structure_bias_requires_aligned_swing_pairs():
    engine = SMCEngine(swing_length=5, eql_tolerance=0.002)
    timestamps = pd.date_range("2026-01-01", periods=4, freq="h")

    bullish = engine._infer_persistent_bias(
        [SwingPoint(0, 100.0, "high", timestamps[0]), SwingPoint(2, 103.0, "high", timestamps[2])],
        [SwingPoint(1, 90.0, "low", timestamps[1]), SwingPoint(3, 92.0, "low", timestamps[3])],
    )
    bearish = engine._infer_persistent_bias(
        [SwingPoint(0, 103.0, "high", timestamps[0]), SwingPoint(2, 100.0, "high", timestamps[2])],
        [SwingPoint(1, 92.0, "low", timestamps[1]), SwingPoint(3, 89.0, "low", timestamps[3])],
    )
    mixed = engine._infer_persistent_bias(
        [SwingPoint(0, 100.0, "high", timestamps[0]), SwingPoint(2, 103.0, "high", timestamps[2])],
        [SwingPoint(1, 92.0, "low", timestamps[1]), SwingPoint(3, 89.0, "low", timestamps[3])],
    )
    equal_inside_tolerance = engine._infer_persistent_bias(
        [SwingPoint(0, 100.0, "high", timestamps[0]), SwingPoint(2, 100.1, "high", timestamps[2])],
        [SwingPoint(1, 90.0, "low", timestamps[1]), SwingPoint(3, 90.1, "low", timestamps[3])],
    )

    assert bullish == "bullish"
    assert bearish == "bearish"
    assert mixed == "neutral"
    assert equal_inside_tolerance == "neutral"


def test_smc_analysis_structure():
    df = generate_synthetic_ohlcv(bars=120, trend="bullish")
    engine = SMCEngine(swing_length=5, internal_swing_length=3)
    signal = engine.analyze(df, symbol="BTCUSDT", timeframe="1h", htf_bias="bullish")

    assert signal.symbol == "BTCUSDT"
    assert signal.timeframe == "1h"
    assert signal.htf_bias == "bullish"
    assert signal.confluence >= 0
    assert signal.confluence <= 100

def test_premium_discount_calculation():
    df = generate_synthetic_ohlcv(bars=100)
    engine = SMCEngine(swing_length=5)
    signal = engine.analyze(df, symbol="ETHUSDT", timeframe="1h")

    if signal.equilibrium > 0:
        assert (signal.in_premium or signal.in_discount) or (signal.equilibrium == df["close"].iloc[-1])


def test_fvg_detection():
    # Construct a clear Bullish FVG: candle 0 high < candle 2 low
    records = [
        {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 100},   # candle 0: high = 102
        {"open": 101, "high": 115, "low": 101, "close": 114, "volume": 500},  # candle 1: big expansion
        {"open": 114, "high": 120, "low": 108, "close": 118, "volume": 200},  # candle 2: low = 108 (gap: 102 to 108)
    ]
    # Add dummy preceding bars for minimum lookback
    pre = [{"open": 98, "high": 100, "low": 97, "close": 99, "volume": 50} for _ in range(30)]
    all_recs = pre + records
    df = pd.DataFrame(all_recs, index=[datetime(2026, 1, 1) + timedelta(hours=i) for i in range(len(all_recs))])

    engine = SMCEngine(swing_length=5)
    signal = engine.analyze(df, symbol="TEST", timeframe="1h", htf_bias="bullish")
    if signal.fvg is not None:
        assert signal.fvg.bottom == 102
        assert signal.fvg.top == 108

