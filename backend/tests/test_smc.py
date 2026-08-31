import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from app.api.chart import _clean_smc_overlay
from app.engines.smc_engine import SMCEngine, SMCSignal, StructureBreak, SwingPoint, Zone


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
    assert signal.fvg is not None
    assert signal.fvg.bottom == 102
    assert signal.fvg.top == 108


def test_luxalgo_overlay_payload_is_bounded_and_has_time_segments():
    df = generate_synthetic_ohlcv(bars=300, trend="bullish")
    signal = SMCEngine().analyze(df, symbol="SOLUSDT", timeframe="15m")

    assert len(signal.swing_structures) <= 6
    assert len(signal.internal_structures) <= 8
    assert len([zone for zone in signal.order_blocks if zone.source == "swing"]) <= 5
    assert len([zone for zone in signal.order_blocks if zone.source == "internal"]) <= 5
    for structure in signal.swing_structures + signal.internal_structures:
        assert structure.pivot_time is not None
        assert structure.break_time is not None
        assert structure.pivot_time < structure.break_time


def test_structure_break_event_is_only_fresh_on_latest_bar():
    df = generate_synthetic_ohlcv(bars=300, trend="bullish")
    signal = SMCEngine(swing_length=5, internal_swing_length=3).analyze(
        df, symbol="SOLUSDT", timeframe="15m"
    )

    if signal.structure_event_age is not None and signal.structure_event_age > 0:
        assert signal.bos is False
        assert signal.choch is False
        assert signal.structure_bias_source == "confirmed_swing_trend"
    if signal.bos or signal.choch:
        assert signal.structure_event_age == 0
        assert signal.structure_event_id


def test_htf_bias_never_changes_single_timeframe_score():
    df = generate_synthetic_ohlcv(bars=160, trend="bullish")
    engine = SMCEngine(swing_length=5, internal_swing_length=3)
    bullish_context = engine.analyze(df, "BTCUSDT", "15m", htf_bias="bullish")
    bearish_context = engine.analyze(df, "BTCUSDT", "15m", htf_bias="bearish")

    assert bullish_context.direction == bearish_context.direction
    assert bullish_context.confluence == bearish_context.confluence
    assert bullish_context.indicator_decision == bearish_context.indicator_decision


def test_clean_overlay_keeps_only_primary_zones_and_latest_structure():
    signal = SMCSignal(symbol="SOLUSDT", timeframe="15m")
    signal.order_block = Zone("ob", "bullish", top=102, bottom=101, index=20)
    signal.order_blocks = [
        Zone("ob", "bearish", top=110, bottom=109, index=5),
        signal.order_block,
    ]
    signal.fvg = Zone("fvg", "bullish", top=103, bottom=102.5, index=21)
    signal.fvgs = [
        Zone("fvg", "bearish", top=108, bottom=107, index=6),
        signal.fvg,
    ]
    now = pd.Timestamp("2026-01-01T12:00:00Z")
    signal.swing_structures = [
        StructureBreak("BOS", "swing", "bullish", 101, 3, now, 10, now),
        StructureBreak("CHoCH", "swing", "bearish", 104, 8, now, 22, now),
    ]
    signal.internal_structures = [
        StructureBreak("BOS", "internal", "bullish", 102, 9, now, 23, now)
    ]

    overlay = _clean_smc_overlay(signal)

    assert overlay["overlay_policy"] == "clean_v1"
    assert len(overlay["order_blocks"]) == 1
    assert overlay["order_blocks"][0]["mid"] == 101.5
    assert len(overlay["fvgs"]) == 1
    assert len(overlay["swing_structures"]) == 1
    assert overlay["swing_structures"][0]["tag"] == "CHoCH"
    assert overlay["internal_structures"] == []

