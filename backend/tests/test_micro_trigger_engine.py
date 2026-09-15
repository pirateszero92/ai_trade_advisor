import pandas as pd
import pytest

from app.engines.micro_trigger_engine import MicroTriggerEngine, MicroTriggerSetup
from app.engines.smc_engine import SMCSignal, Zone
from app.engines.tri_core_engine import TriCoreSetupEngine


def _make_micro_frame_long() -> pd.DataFrame:
    """Create 15 bars of 3M data where price dips into zone and breaks micro structure."""
    timestamps = pd.date_range("2026-09-14 10:00", periods=15, freq="3min", tz="UTC")
    # Bars 0-5: dipping down to test zone around 101.0
    # Bars 6-8: basing at 101.0 (pivot low)
    # Bars 9-11: rallying to 102.2
    # Bars 12-14: breaking above 102.2 to 102.8 with positive delta
    opens = [103.5, 103.0, 102.5, 102.0, 101.5, 101.2, 101.0, 101.1, 101.5, 102.0, 102.2, 102.1, 102.3, 102.5, 102.6]
    highs = [103.8, 103.2, 102.8, 102.2, 101.8, 101.5, 101.3, 101.6, 102.1, 102.3, 102.4, 102.4, 102.7, 102.9, 103.2]
    lows  = [103.0, 102.4, 101.8, 101.4, 101.1, 100.9, 100.8, 101.0, 101.2, 101.8, 101.9, 102.0, 102.2, 102.4, 102.5]
    closes= [103.1, 102.5, 102.0, 101.5, 101.2, 101.0, 101.1, 101.5, 102.0, 102.2, 102.1, 102.3, 102.5, 102.7, 103.0]
    volume = [100.0] * 15
    deltas = [-20.0, -30.0, -40.0, -25.0, -10.0, 5.0, 10.0, 20.0, 25.0, 30.0, 15.0, 20.0, 35.0, 45.0, 60.0]

    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volume,
            "volume_delta": deltas,
        },
        index=timestamps,
    )


def test_micro_trigger_evaluates_actionable_long_on_micro_choch():
    frame_micro = _make_micro_frame_long()
    zone = Zone(
        kind="breaker", direction="bullish", top=102.0, bottom=100.5, index=10,
        confirmed_index=12, source="swing",
    )
    macro_target = 112.0
    macro_atr = 2.0

    setup = MicroTriggerEngine.evaluate(
        side="long",
        macro_zone=zone,
        macro_target=macro_target,
        frame_micro=frame_micro,
        micro_timeframe="3m",
        min_rr=2.0,
        macro_atr=macro_atr,
    )

    assert setup.actionable
    assert setup.direction == "long"
    assert setup.micro_choch
    assert setup.micro_absorption
    assert setup.trigger_type == "micro_sniper"
    assert setup.entry == 103.0
    assert setup.stop_loss < 101.0  # Placed under micro low 100.8
    assert setup.take_profit == 112.0
    assert setup.risk_reward >= 3.5  # (112 - 103) / (103 - 100.6) ~ 3.75x


def test_micro_trigger_awaits_micro_choch_when_falling():
    # Downward trending frame without CHoCH breakout, but positive absorption bounce
    timestamps = pd.date_range("2026-09-14 10:00", periods=10, freq="3min", tz="UTC")
    opens = [105.0 - i * 0.4 for i in range(10)]
    highs = [p + 0.2 for p in opens]
    lows = [p - 0.3 for p in opens]
    closes = [p - 0.2 for p in opens]

    frame_micro = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [100.0] * 10, "volume_delta": [10.0] * 10},
        index=timestamps,
    )
    zone = Zone(kind="breaker", direction="bullish", top=102.0, bottom=100.5, index=5)

    setup = MicroTriggerEngine.evaluate(
        side="long",
        macro_zone=zone,
        macro_target=110.0,
        frame_micro=frame_micro,
        micro_timeframe="3m",
    )

    assert not setup.actionable
    assert any("micro-CHoCH" in r for r in setup.rejection_reasons)


def test_micro_trigger_rejects_when_micro_absorption_false():
    frame_micro = _make_micro_frame_long()
    # Invert deltas so absorption fails
    frame_micro["volume_delta"] = [-50.0] * len(frame_micro)
    zone = Zone(
        kind="breaker", direction="bullish", top=102.0, bottom=100.5, index=10,
        confirmed_index=12, source="swing",
    )
    setup = MicroTriggerEngine.evaluate(
        side="long",
        macro_zone=zone,
        macro_target=112.0,
        frame_micro=frame_micro,
        micro_timeframe="3m",
    )
    assert not setup.actionable
    assert any("Micro CVD absorption not confirmed" in r for r in setup.rejection_reasons)


def test_micro_cannot_upgrade_non_actionable_15m_setup():
    frame_micro = _make_micro_frame_long()
    # Macro signal that is NOT actionable (e.g. no liquidity swept, no displacement)
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        volume_quality="exchange_aggressor",
        current_price=103.0,
    )
    frame_15m = pd.DataFrame(
        {"open": [100.0] * 20, "high": [101.0] * 20, "low": [99.0] * 20, "close": [100.0] * 20, "volume": [100.0] * 20, "volume_delta": [10.0] * 20},
        index=pd.date_range("2026-09-01", periods=20, freq="15min", tz="UTC"),
    )
    setup = TriCoreSetupEngine.evaluate_with_micro_trigger(
        signal=signal,
        frame=frame_15m,
        frame_micro=frame_micro,
        micro_timeframe="3m",
    )
    # CANONICAL RULE: 15M not actionable -> must remain NOT actionable!
    assert not setup.actionable



def test_tri_core_evaluate_with_micro_trigger_integration():
    # 15M Macro setup
    macro_index = pd.date_range("2026-09-01", periods=20, freq="15min", tz="UTC")
    frame_15m = pd.DataFrame(
        {
            "open": [100.0] * 20,
            "high": [101.0] * 19 + [103.5],
            "low": [99.0] * 19 + [100.5],
            "close": [100.0] * 19 + [103.0],
            "volume": [1000.0] * 20,
            "buy_volume": [600.0] * 20,
            "sell_volume": [400.0] * 20,
            "flow_source": ["binance_taker_volume"] * 20,
            "volume_delta": [200.0] * 20,
            "cvd": [float(i * 100) for i in range(1, 21)],
        },
        index=macro_index,
    )
    frame_15m.loc[frame_15m.index[16:19], ["open", "high", "low", "close"]] = [
        105.0, 106.0, 104.5, 105.5
    ]
    frame_15m.loc[frame_15m.index[19], ["open", "high", "low", "close"]] = [
        104.0, 104.5, 102.1, 102.6
    ]

    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        volume_quality="exchange_aggressor",
        cvd_divergence="none",
        delta_ratio=0.15,
        current_price=103.0,
    )
    signal.displacement = {
        "index": 15,
        "direction": "long",
        "structure_break_id": "swing:CHoCH:bullish:15:102.0",
        "structure_break_index": 15,
    }
    breaker = Zone(
        kind="breaker", direction="bullish", top=102.0, bottom=100.5, index=12,
        confirmed_index=15, source="swing",
    )
    signal.breaker_blocks = [breaker]
    signal.equal_highs = [115.0]

    # Evaluate with micro 3M trigger
    frame_micro = _make_micro_frame_long()
    setup = TriCoreSetupEngine.evaluate_with_micro_trigger(
        signal=signal,
        frame=frame_15m,
        frame_micro=frame_micro,
        micro_timeframe="3m",
    )

    assert setup.actionable
    assert setup.direction == "long"
    assert setup.setup_type == "micro_sniper_3m"
    assert setup.risk_reward >= 3.0
    assert any("Micro-CHoCH" in item for item in setup.evidence)
