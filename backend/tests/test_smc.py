import pandas as pd
import numpy as np
import pytest
from datetime import datetime, timedelta
from app.api.chart import _clean_smc_overlay
from app.engines.smc_engine import SMCEngine, SMCSignal, StructureBreak, SwingPoint, Zone


def test_zone_serializes_origin_and_causal_confirmation_metadata():
    zone = Zone(
        kind="ob",
        direction="bullish",
        top=101.0,
        bottom=99.0,
        index=12,
        confirmed_index=18,
    )

    payload = zone.to_dict()

    assert payload["origin_index"] == 12
    assert payload["confirmed_index"] == 18
    assert payload["zone_id"] == zone.zone_id
    assert ":12:18:" in zone.zone_id


def test_swing_serializes_origin_and_causal_confirmation_metadata():
    timestamps = pd.date_range("2026-01-01", periods=8, freq="15min")
    swing = SwingPoint(
        index=2,
        price=101.25,
        kind="high",
        timestamp=timestamps[2],
        confirmed_index=7,
        confirmed_timestamp=timestamps[7],
    )

    payload = swing.to_dict()

    assert payload["origin_index"] == 2
    assert payload["confirmed_index"] == 7
    assert payload["confirmed_timestamp"] == timestamps[7].isoformat()
    assert ":2:7:" in payload["level_id"]


def test_multi_bar_reclaim_preserves_deepest_breach_extreme():
    index = pd.date_range("2026-09-01", periods=12, freq="h", tz="UTC")
    frame = pd.DataFrame({
        "open": [101.0] * 12,
        "high": [102.0] * 12,
        "low": [100.0] * 10 + [96.0, 98.0],
        "close": [101.0] * 10 + [98.0, 101.0],
        "volume": [1000.0] * 12,
    }, index=index)
    signal = SMCSignal("TEST", "1h")
    signal.swing_lows = [SwingPoint(
        4, 100.0, "low", index[4], confirmed_index=6,
        confirmed_timestamp=index[6],
    )]
    SMCEngine()._detect_liquidity_sweep(frame, signal)
    assert signal.liquidity_swept
    assert signal.liquidity_sweep["event_type"] == "multi_bar_reclaim"
    assert signal.liquidity_sweep["extreme"] == 96.0
    assert signal.liquidity_sweep["extreme_index"] == 10


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
    engine = SMCEngine(swing_length=5, structure_tolerance=0.002)
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
        assert signal.zone_position in {
            "premium", "discount", "equilibrium", "mid_range"
        }
        assert sum(
            (signal.in_premium, signal.in_discount, signal.in_equilibrium)
        ) <= 1


def test_luxalgo_range_zone_boundaries_are_not_entire_half_ranges():
    premium = SMCEngine._classify_range_zones(200.0, 100.0, 196.0)
    discount = SMCEngine._classify_range_zones(200.0, 100.0, 104.0)
    equilibrium = SMCEngine._classify_range_zones(200.0, 100.0, 150.0)
    mid_range = SMCEngine._classify_range_zones(200.0, 100.0, 175.0)

    assert premium["zone_position"] == "premium"
    assert premium["premium_zone"] == {"top": 200.0, "bottom": 195.0}
    assert discount["zone_position"] == "discount"
    assert discount["discount_zone"] == {"top": 105.0, "bottom": 100.0}
    assert equilibrium["zone_position"] == "equilibrium"
    assert equilibrium["equilibrium_zone"] == {"top": 152.5, "bottom": 147.5}
    assert mid_range["zone_position"] == "mid_range"


def test_wilder_atr_matches_pine_warmup_and_rma():
    highs = np.array([11.0, 12.0, 13.0, 15.0])
    lows = np.array([9.0, 10.0, 11.0, 12.0])
    closes = np.array([10.0, 11.0, 12.0, 14.0])

    atr = SMCEngine._wilder_atr(highs, lows, closes, 3)

    assert np.isnan(atr[0]) and np.isnan(atr[1])
    assert atr[2] == 2.0
    assert atr[3] == pytest.approx(7.0 / 3.0)


def test_equal_level_threshold_uses_atr_not_asset_price():
    engine = SMCEngine(eql_tolerance=0.1)
    assert engine._equal_level_threshold(300.0) == 30.0
    assert np.isnan(engine._equal_level_threshold(float("nan")))


def test_internal_confluence_filter_is_optional_like_luxalgo():
    records = [
        {"open": 104, "high": 106, "low": 103, "close": 105, "volume": 100},
        {"open": 105, "high": 106, "low": 95, "close": 100, "volume": 100},
        {"open": 100, "high": 104, "low": 99, "close": 103, "volume": 100},
        {"open": 103, "high": 105, "low": 101, "close": 104, "volume": 100},
        {"open": 104, "high": 110, "low": 103, "close": 108, "volume": 100},
        {"open": 108, "high": 106, "low": 102, "close": 104, "volume": 100},
        {"open": 104, "high": 107, "low": 103, "close": 105, "volume": 100},
        # Close crosses 110, but the lower wick is larger than the upper wick.
        {"open": 108, "high": 112, "low": 100, "close": 111, "volume": 100},
    ]
    records.extend(
        {"open": 105, "high": 109, "low": 101, "close": 105, "volume": 100}
        for _ in range(12)
    )
    frame = pd.DataFrame(
        records,
        index=pd.date_range("2026-01-01", periods=len(records), freq="15min"),
    )

    unfiltered = SMCEngine(
        swing_length=10,
        internal_swing_length=2,
        atr_length=3,
        internal_confluence_filter=False,
    ).analyze(frame, "TEST", "15m")
    filtered = SMCEngine(
        swing_length=10,
        internal_swing_length=2,
        atr_length=3,
        internal_confluence_filter=True,
    ).analyze(frame, "TEST", "15m")

    assert any(
        item.direction == "bullish" and item.level == 110
        for item in unfiltered.internal_structures
    )
    assert not any(
        item.direction == "bullish" and item.level == 110
        for item in filtered.internal_structures
    )


def test_unmitigated_fvg_does_not_expire_after_lookback_age():
    records = [
        {"open": 99, "high": 102, "low": 98, "close": 100, "volume": 100}
        for _ in range(10)
    ]
    records.extend(
        [
            {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 100},
            {"open": 101, "high": 112, "low": 101, "close": 111, "volume": 500},
            {"open": 111, "high": 114, "low": 108, "close": 112, "volume": 100},
        ]
    )
    records.extend(
        {"open": 110, "high": 114, "low": 107, "close": 111, "volume": 100}
        for _ in range(20)
    )
    frame = pd.DataFrame(
        records,
        index=pd.date_range("2026-01-01", periods=len(records), freq="15min"),
    )

    signal = SMCEngine(
        swing_length=5,
        internal_swing_length=3,
        atr_length=3,
        fvg_lookback=5,
    ).analyze(frame, "TEST", "15m")

    assert any(
        zone.direction == "bullish"
        and zone.bottom == 102
        and zone.index < len(frame) - 5
        for zone in signal.fvgs
    )


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


def test_structure_break_event_remains_fresh_during_three_bar_entry_window():
    df = generate_synthetic_ohlcv(bars=300, trend="bullish")
    signal = SMCEngine(swing_length=5, internal_swing_length=3).analyze(
        df, symbol="SOLUSDT", timeframe="15m"
    )

    if signal.structure_event_age is not None:
        if signal.structure_event_age <= 3:
            assert signal.bos or signal.choch
        else:
            assert signal.bos is False
            assert signal.choch is False
            assert signal.structure_bias_source == "confirmed_swing_trend"
    if signal.bos or signal.choch:
        assert signal.structure_event_age is not None
        assert 0 <= signal.structure_event_age <= 3
        assert signal.structure_event_id


def test_htf_bias_never_changes_single_timeframe_score():
    df = generate_synthetic_ohlcv(bars=160, trend="bullish")
    engine = SMCEngine(swing_length=5, internal_swing_length=3)
    bullish_context = engine.analyze(df, "BTCUSDT", "15m", htf_bias="bullish")
    bearish_context = engine.analyze(df, "BTCUSDT", "15m", htf_bias="bearish")

    assert bullish_context.direction == bearish_context.direction
    assert bullish_context.confluence == bearish_context.confluence
    assert bullish_context.indicator_decision == bearish_context.indicator_decision


def test_rich_luxalgo_overlay_retains_unmitigated_zones_and_structures():
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

    assert overlay["overlay_policy"] == "luxalgo_rich_v1"
    assert len(overlay["order_blocks"]) == 2
    assert overlay["order_blocks"][0]["mid"] == 101.5  # Most recent index 20 first
    assert len(overlay["fvgs"]) == 2
    assert len(overlay["swing_structures"]) == 2
    assert overlay["swing_structures"][0]["tag"] == "CHoCH"
    assert len(overlay["internal_structures"]) == 1
    assert overlay["internal_structures"][0]["tag"] == "BOS"


def test_displacement_detection_preserves_structure_break_across_subsequent_bars():
    df = generate_synthetic_ohlcv(bars=200, trend="bearish")
    engine = SMCEngine()
    signal = engine.analyze(df, symbol="BTCUSDT", timeframe="15m")
    assert isinstance(signal.displacement, dict)
    # If a structure break occurred in the displacement window, displacement metadata is populated
    if signal.displacement.get("structure_break_id"):
        assert signal.displacement.get("structure_break_index") is not None
        assert signal.displacement.get("direction") in {"long", "short"}


def test_breaker_blocks_tracked_on_order_block_mitigation():
    signal = SMCSignal(symbol="BTCUSDT", timeframe="15m")
    breaker = Zone("breaker", "bullish", top=105.0, bottom=103.0, index=10, confirmed_index=15)
    signal.breaker_blocks = [breaker]
    
    data = signal.to_dict()
    assert "breaker_blocks" in data
    assert len(data["breaker_blocks"]) == 1
    assert data["breaker_blocks"][0]["kind"] == "breaker"
    assert data["breaker_blocks"][0]["direction"] == "bullish"
    assert data["breaker_blocks"][0]["mid"] == 104.0

    overlay = _clean_smc_overlay(signal)
    assert "breaker_blocks" in overlay
    assert len(overlay["breaker_blocks"]) == 1
    assert overlay["breaker_blocks"][0]["direction"] == "bullish"

