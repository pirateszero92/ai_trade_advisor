import numpy as np
import pandas as pd

from app.engines.risk_engine import squeeze_adjusted_risk_pct
from app.engines.smc_engine import SMCSignal, SwingPoint, Zone
from app.engines.strategy_engine import StrategyEngine
from app.engines.tri_core_engine import TriCoreSetupEngine


def _frame() -> pd.DataFrame:
    index = pd.date_range("2026-09-01", periods=20, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "open": [100.0] * 20,
            "high": [101.0] * 19 + [101.5],
            "low": [99.0] * 19 + [98.8],
            "close": [100.0] * 19 + [100.5],
            "volume": [1000.0] * 20,
            "buy_volume": [550.0] * 20,
            "sell_volume": [450.0] * 20,
            "flow_source": ["binance_taker_volume"] * 20,
            "volume_delta": [100.0] * 20,
            "cvd": [float(index * 100) for index in range(1, 21)],
        },
        index=index,
    )


def _signal() -> SMCSignal:
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="1h",
        volume_quality="exchange_aggressor",
        cvd_divergence="bullish",
        liquidity_swept=True,
        sweep_direction="low",
        liquidity_sweep={
            "candle_index": 19,
            "reference_index": 8,
            "extreme": 98.8,
            "reclaim_close": 100.5,
            "event_type": "single_bar_sweep",
        },
    )
    signal.cvd_divergence_evidence = {
        "reference_index": 8,
        "reference_cvd": 900.0,
        "current_cvd": 2000.0,
        "reclaim_close_price": 100.5,
        "price_excursion_atr": 0.7,
        "cvd_efficiency": 0.2,
    }
    signal.cvd = 2000.0
    signal.swing_highs = [
        SwingPoint(8, 110.0, "high", pd.Timestamp("2026-09-01 08:00", tz="UTC"), 10)
    ]
    return signal


def test_current_closed_sweep_plus_cvd_creates_executable_plan():
    signal = _signal()
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert setup.actionable
    assert setup.direction == "long"
    assert setup.setup_type == "sweep_reversal"
    assert setup.stop_loss < setup.entry < setup.take_profit
    assert setup.risk_reward >= 2.0


def test_previous_candle_sweep_remains_entry_ready_for_one_bar():
    signal = _signal()
    signal.liquidity_sweep["candle_index"] = 18
    signal.liquidity_sweep["reclaim_close"] = 100.0
    signal.cvd_divergence_evidence["reclaim_close_price"] = 100.0
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert setup.actionable
    assert setup.trigger_state == "limit_retest_only"
    assert setup.trigger_age_bars == 1
    assert setup.extension_atr is not None
    assert setup.extension_atr <= 0.60


def test_absorption_from_a_different_candle_cannot_confirm_sweep():
    signal = _signal()
    signal.cvd_divergence = "none"
    signal.cvd_divergence_evidence = {}
    signal.delta_absorption = True
    signal.delta_absorption_type = "bullish_absorption"
    signal.delta_absorption_evidence = {
        "candle_index": 18,
        "close_price": 100.0,
        "flow_source": "binance_taker_volume",
    }
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert not setup.actionable
    assert any("not bound" in reason for reason in setup.rejection_reasons)


def test_same_candle_absorption_can_confirm_sweep():
    signal = _signal()
    signal.cvd_divergence = "none"
    signal.cvd_divergence_evidence = {}
    signal.delta_absorption = True
    signal.delta_absorption_type = "bullish_absorption"
    signal.delta_absorption_evidence = {
        "candle_index": 19,
        "close_price": 100.5,
        "flow_source": "binance_taker_volume",
    }
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert setup.actionable
    assert setup.flow_confirmation_type == "sweep_bound_absorption"
    assert setup.flow_confirmed
    assert setup.cvd_confirmed


def test_armed_sweep_uses_recomputed_cvd_not_provider_cumulative_offset():
    signal = _signal()
    signal.liquidity_sweep["candle_index"] = 18
    signal.liquidity_sweep["reclaim_close"] = 100.0
    signal.cvd_divergence_evidence["reclaim_close_price"] = 100.0
    frame = _frame()
    frame["cvd"] = frame["cvd"] - 5000.0
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable


def test_displacement_retest_labels_single_bar_flow_without_claiming_cvd():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        101.5, 102.0, 101.0, 101.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = 0.4
    signal.displacement = {
        "index": 15,
        "direction": "long",
        "structure_break_id": "swing:BOS:bullish:15:100",
        "structure_break_index": 15,
    }
    signal.order_blocks = [
        Zone("ob", "bullish", top=100.2, bottom=99.7, index=8,
             confirmed_index=15)
    ]
    signal.equal_highs = [110.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.flow_confirmation_type == "single_bar_aggressor_delta"
    assert setup.flow_confirmed
    assert not setup.cvd_confirmed


def test_sweep_trigger_uses_configured_two_bar_draft_ttl():
    signal = _signal()
    signal.liquidity_sweep["candle_index"] = 17
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert setup.actionable
    assert setup.trigger_age_bars == 2
    assert setup.expires_after_bars == 2
    assert setup.calibration_status == "draft_unvalidated"


def test_replay_calibrated_ttl_override_changes_expiry_without_code_change():
    signal = _signal()
    signal.liquidity_sweep["candle_index"] = 17
    setup = TriCoreSetupEngine.evaluate(signal, _frame(), {
        "ttl_bars": 1,
        "calibration_status": "walk_forward_validated",
    })
    assert not setup.actionable
    assert setup.trigger_state == "expired"
    assert setup.expires_after_bars == 1
    assert setup.calibration_status == "walk_forward_validated"


def test_armed_sweep_rejects_chasing_beyond_point_six_atr():
    frame = _frame()
    frame.iloc[-1, frame.columns.get_loc("close")] = 104.0
    frame.iloc[-1, frame.columns.get_loc("high")] = 104.5
    signal = _signal()
    signal.liquidity_sweep["candle_index"] = 18
    signal.liquidity_sweep["reclaim_close"] = 100.0
    signal.cvd_divergence_evidence["reclaim_close_price"] = 100.0
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert not setup.actionable
    assert setup.trigger_state == "no_chase"
    assert any("do not chase" in reason for reason in setup.rejection_reasons)


def test_armed_sweep_invalidates_beyond_sweep_extreme():
    frame = _frame()
    frame.iloc[-1, frame.columns.get_loc("low")] = 98.7
    signal = _signal()
    signal.liquidity_sweep["candle_index"] = 18
    signal.liquidity_sweep["reclaim_close"] = 100.0
    signal.cvd_divergence_evidence["reclaim_close_price"] = 100.0
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert not setup.actionable
    assert setup.trigger_state == "invalidated"
    assert any("invalidated" in reason for reason in setup.rejection_reasons)


def test_legacy_scenario_and_sqz_cannot_change_strategy_authority(monkeypatch):
    monkeypatch.setattr("app.core.runtime_config.load_runtime_config", lambda: {})
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "sqz_risk_scaling_enabled", True)
    signal = _signal()
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    signal.tri_core_setup = setup.to_dict()
    signal.direction = setup.direction
    signal.entry = setup.entry
    signal.stop_loss = setup.stop_loss
    signal.take_profit = setup.take_profit
    signal.risk_reward = setup.risk_reward
    signal.scenario = {"actionable": False}
    signal.squeeze_status = "no_squeeze"
    signal.indicator_decision = {"squeeze_bonus": 0, "squeeze_bonus_max": 10}
    without_sqz = StrategyEngine().evaluate(signal)
    base_risk = squeeze_adjusted_risk_pct(signal)

    signal.scenario = {"actionable": True}
    signal.squeeze_status = "squeeze_fire"
    signal.indicator_decision = {"squeeze_bonus": 10, "squeeze_bonus_max": 10}
    with_sqz = StrategyEngine().evaluate(signal)
    bonus_risk = squeeze_adjusted_risk_pct(signal)

    assert without_sqz.approved and with_sqz.approved
    assert without_sqz.direction == with_sqz.direction == "long"
    assert base_risk == 0.75
    assert bonus_risk == 1.0


def test_true_aggressor_cvd_is_mandatory():
    signal = _signal()
    signal.volume_quality = "estimated"
    assert not TriCoreSetupEngine.evaluate(signal, _frame()).actionable


def test_divergence_from_a_different_swing_cannot_confirm_sweep():
    signal = _signal()
    signal.cvd_divergence_evidence["reference_index"] = 7
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert not setup.actionable
    assert any("same swept swing" in reason for reason in setup.rejection_reasons)


def test_nearest_obstacle_is_not_skipped_to_manufacture_two_r():
    signal = _signal()
    signal.equal_highs = [100.6, 110.0]
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert not setup.actionable
    assert any("Nearest opposing obstacle" in reason for reason in setup.rejection_reasons)


def test_multi_bar_sweep_uses_full_window_extreme_for_stop():
    signal = _signal()
    signal.liquidity_sweep.update({
        "event_type": "multi_bar_reclaim",
        "extreme": 97.0,
        "extreme_index": 18,
    })
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert setup.actionable
    assert setup.stop_loss < 97.0
    assert setup.invalidation_source == "full_sweep_window_extreme"


def test_continuation_requires_lineage_and_first_retest():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        101.5, 102.0, 101.0, 101.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = 0.4
    signal.displacement = {
        "index": 15,
        "direction": "long",
        "structure_break_id": "swing:BOS:bullish:15:100",
        "structure_break_index": 15,
    }
    zone = Zone(
        "ob", "bullish", top=100.2, bottom=99.7, index=8,
        confirmed_index=15,
    )
    signal.order_blocks = [zone]
    signal.equal_highs = [110.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable
    assert setup.setup_type == "displacement_retest"

    # A prior overlap after confirmation means the current candle is stale.
    stale_frame = frame.copy()
    stale_frame.iloc[17, stale_frame.columns.get_loc("low")] = 99.8
    assert not TriCoreSetupEngine.evaluate(signal, stale_frame).actionable

    signal.displacement["structure_break_id"] = ""
    assert not TriCoreSetupEngine.evaluate(signal, frame).actionable


def test_bearish_breakdown_retest_actionable_on_non_opposing_flow():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        97.0, 98.0, 96.5, 97.5
    ]
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [
        99.0, 101.8, 98.8, 101.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = 0.02
    signal.displacement = {
        "index": 15,
        "direction": "short",
        "structure_break_id": "swing:CHoCH:bearish:15:100.0",
        "structure_break_index": 15,
    }
    zone = Zone(
        "ob", "bearish", top=102.0, bottom=101.0, index=12,
        confirmed_index=15,
    )
    signal.order_blocks = [zone]
    signal.equal_lows = [90.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable
    assert setup.direction == "short"
    assert setup.setup_type == "displacement_retest"
    assert setup.entry == 101.5
    assert setup.take_profit == 90.0
    assert setup.risk_reward >= 2.0


def test_bearish_breakdown_retest_rejected_on_bullish_cvd_divergence():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        97.0, 98.0, 96.5, 97.5
    ]
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [
        99.0, 101.8, 98.8, 101.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "bullish"
    signal.delta_ratio = -0.05
    signal.displacement = {
        "index": 15,
        "direction": "short",
        "structure_break_id": "swing:CHoCH:bearish:15:100.0",
        "structure_break_index": 15,
    }
    zone = Zone(
        "ob", "bearish", top=102.0, bottom=101.0, index=12,
        confirmed_index=15,
    )
    signal.order_blocks = [zone]
    signal.equal_lows = [90.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert not setup.actionable


def test_bearish_breakdown_retest_rejected_on_strongly_opposing_delta():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        97.0, 98.0, 96.5, 97.5
    ]
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [
        99.0, 101.8, 98.8, 101.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = 0.35
    signal.displacement = {
        "index": 15,
        "direction": "short",
        "structure_break_id": "swing:CHoCH:bearish:15:100.0",
        "structure_break_index": 15,
    }
    zone = Zone(
        "ob", "bearish", top=102.0, bottom=101.0, index=12,
        confirmed_index=15,
    )
    signal.order_blocks = [zone]
    signal.equal_lows = [90.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert not setup.actionable


def test_bullish_breaker_retest_actionable_on_non_opposing_flow():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        105.0, 106.0, 104.5, 105.5
    ]
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [
        103.0, 103.5, 101.2, 101.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = 0.05
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
    signal.order_blocks = []
    signal.equal_highs = [115.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable
    assert setup.direction == "long"
    assert setup.setup_type == "breaker_retest"
    assert setup.entry == 101.5
    assert setup.take_profit == 110.0
    assert setup.risk_reward >= 2.0


def test_bearish_breaker_retest_actionable_on_non_opposing_flow():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        97.0, 98.0, 96.5, 97.5
    ]
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [
        99.0, 101.8, 98.8, 101.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = -0.05
    signal.displacement = {
        "index": 15,
        "direction": "short",
        "structure_break_id": "swing:CHoCH:bearish:15:100.0",
        "structure_break_index": 15,
    }
    breaker = Zone(
        kind="breaker", direction="bearish", top=102.0, bottom=101.0, index=12,
        confirmed_index=15, source="swing",
    )
    signal.breaker_blocks = [breaker]
    signal.order_blocks = []
    signal.equal_lows = [90.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable
    assert setup.direction == "short"
    assert setup.setup_type == "breaker_retest"
    assert setup.entry == 101.5
    assert setup.take_profit == 90.0
    assert setup.risk_reward >= 2.0


def test_fvg_retest_actionable_setup():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        105.0, 106.0, 104.5, 105.5
    ]
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [
        103.0, 103.5, 101.2, 101.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = 0.08
    signal.displacement = {
        "index": 15,
        "direction": "long",
        "structure_break_id": "swing:BOS:bullish:15:102.0",
        "structure_break_index": 15,
    }
    fvg_zone = Zone(
        kind="fvg", direction="bullish", top=102.5, bottom=101.0, index=14,
        confirmed_index=15,
    )
    signal.fvgs = [fvg_zone]
    signal.order_blocks = []
    signal.equal_highs = [115.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable
    assert setup.direction == "long"
    assert setup.setup_type == "fvg_retest"
    assert setup.risk_reward >= 2.0


def test_breaker_retest_rejected_on_opposing_cvd_divergence():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        105.0, 106.0, 104.5, 105.5
    ]
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [
        103.0, 103.5, 101.2, 101.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "bearish"  # Opposing divergence (Bear Trap / Exhaustion)
    signal.delta_ratio = 0.05
    signal.displacement = {
        "index": 15,
        "direction": "long",
        "structure_break_id": "swing:CHoCH:bullish:15:102.0",
        "structure_break_index": 15,
    }
    breaker = Zone(
        kind="breaker", direction="bullish", top=102.0, bottom=100.5, index=12,
        confirmed_index=15,
    )
    signal.breaker_blocks = [breaker]
    signal.equal_highs = [115.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert not setup.actionable


def test_breaker_retest_rejected_on_insufficient_rr():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        105.0, 106.0, 104.5, 105.5
    ]
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [
        103.0, 103.5, 101.2, 101.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = 0.05
    signal.displacement = {
        "index": 15,
        "direction": "long",
        "structure_break_id": "swing:CHoCH:bullish:15:102.0",
        "structure_break_index": 15,
    }
    breaker = Zone(
        kind="breaker", direction="bullish", top=102.0, bottom=100.5, index=12,
        confirmed_index=15,
    )
    signal.breaker_blocks = [breaker]
    # Obstacle is too close -> R:R < 2.0
    signal.equal_highs = [102.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert not setup.actionable


def test_fvg_shallow_retest_respecting_consequent_encroachment():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        105.0, 106.0, 104.5, 105.5
    ]
    # Candle dips into upper half of FVG (102.2 > mid 102.0, <= top 103.0) and bounces
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [
        104.0, 104.5, 102.2, 102.8
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = 0.12
    signal.displacement = {
        "index": 15,
        "direction": "long",
        "structure_break_id": "swing:BOS:bullish:15:102.0",
        "structure_break_index": 15,
    }
    fvg_zone = Zone(
        kind="fvg", direction="bullish", top=103.0, bottom=101.0, index=14,
        confirmed_index=15,
    )
    signal.fvgs = [fvg_zone]
    signal.order_blocks = []
    signal.equal_highs = [115.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable
    assert setup.direction == "long"
    assert setup.setup_type == "fvg_shallow_retest"
    assert setup.risk_reward >= 2.0
    assert any("50% Consequent Encroachment" in item for item in setup.evidence)


def test_breaker_front_run_buffer_shallow_retest():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        105.0, 106.0, 104.5, 105.5
    ]
    # Breaker top is 102.0. ATR is ~2.0, so 0.15*ATR is ~0.3 (buffer extends to ~102.3).
    # Candle low is 102.1 (does not touch 102.0 directly, but within 0.15 ATR front-run buffer)
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [
        104.0, 104.5, 102.1, 102.6
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = 0.15
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
    signal.order_blocks = []
    signal.equal_highs = [115.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable
    assert setup.direction == "long"
    assert setup.setup_type == "breaker_shallow_retest"
    assert setup.risk_reward >= 2.0
    assert any("Front-run shallow retest" in item for item in setup.evidence)


def test_breaker_front_run_buffer_rejected_when_too_far():
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        105.0, 106.0, 104.5, 105.5
    ]
    # Candle low is 103.0 (beyond 0.15 ATR buffer of 102.0)
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [
        104.0, 104.5, 103.0, 103.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = 0.15
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
    signal.order_blocks = []
    signal.equal_highs = [115.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    # Price was too far away from the breaker; not a retest
    assert not setup.actionable


def test_nearest_target_selects_closest_obstacle():
    from app.engines.tri_core_engine import _nearest_target

    signal = _signal()
    # For Long, entry is 100.0, swing highs are 105.0 and 120.0
    signal.swing_highs = [
        SwingPoint(index=5, price=105.0, kind="high", timestamp=pd.Timestamp.now(tz="UTC")),
        SwingPoint(index=10, price=120.0, kind="high", timestamp=pd.Timestamp.now(tz="UTC")),
    ]
    target_long = _nearest_target(signal, "long", 100.0)
    assert target_long == 105.0, f"Expected closest obstacle 105.0, got {target_long}"

    # For Short, entry is 100.0, swing lows are 95.0 and 80.0
    signal.swing_lows = [
        SwingPoint(index=5, price=95.0, kind="low", timestamp=pd.Timestamp.now(tz="UTC")),
        SwingPoint(index=10, price=80.0, kind="low", timestamp=pd.Timestamp.now(tz="UTC")),
    ]
    target_short = _nearest_target(signal, "short", 100.0)
    assert target_short == 95.0, f"Expected closest obstacle 95.0, got {target_short}"


def test_range_opposing_target_locks_to_major_boundaries():
    from app.engines.tri_core_engine import _range_opposing_target

    signal = _signal()
    # For Long, minor high is 105.0, but major supply order block is 135.0-140.0
    signal.swing_highs = [
        SwingPoint(index=5, price=105.0, kind="high", timestamp=pd.Timestamp.now(tz="UTC")),
    ]
    signal.order_blocks = [
        Zone(kind="ob", direction="bearish", top=140.0, bottom=135.0, index=2, confirmed_index=3, source="swing"),
    ]
    target_long = _range_opposing_target(signal, "long", 100.0)
    assert target_long == 135.0, f"Expected major supply boundary 135.0, got {target_long}"

    # For Short, minor low is 95.0, but major demand order block is 70.0-75.0
    signal.swing_lows = [
        SwingPoint(index=5, price=95.0, kind="low", timestamp=pd.Timestamp.now(tz="UTC")),
    ]
    signal.order_blocks = [
        Zone(kind="ob", direction="bullish", top=75.0, bottom=70.0, index=2, confirmed_index=3, source="swing"),
    ]
    target_short = _range_opposing_target(signal, "short", 100.0)
    assert target_short == 75.0, f"Expected major demand boundary 75.0, got {target_short}"


def test_range_boundary_ping_pong_long_creates_actionable_setup():
    signal = _signal()
    signal.liquidity_swept = False
    signal.market_regime = {"regime": "ranging"}
    signal.in_discount = True
    signal.delta_ratio = 0.05
    signal.cvd_divergence = "none"

    # Bullish demand OB at 98.0-100.0
    demand_ob = Zone(kind="ob", direction="bullish", top=100.0, bottom=98.0, index=10, confirmed_index=11, source="swing")
    # Bearish supply OB at 120.0-125.0
    supply_ob = Zone(kind="ob", direction="bearish", top=125.0, bottom=120.0, index=5, confirmed_index=6, source="swing")
    signal.order_blocks = [demand_ob, supply_ob]

    frame = _frame()
    setup = TriCoreSetupEngine.evaluate(signal, frame, {"ping_pong_enabled": True, "minimum_rr": 2.0})
    assert setup.actionable is True
    assert setup.direction == "long"
    assert setup.setup_type == "range_boundary_ping_pong"
    assert setup.take_profit == 120.0
    assert setup.stop_loss < 98.0
    assert setup.risk_reward >= 2.0


def test_range_boundary_ping_pong_short_creates_actionable_setup():
    signal = _signal()
    signal.liquidity_swept = False
    signal.market_regime = {"regime": "ranging"}
    signal.in_premium = True
    signal.delta_ratio = -0.05
    signal.cvd_divergence = "none"

    # Bullish demand OB at 80.0-85.0
    demand_ob = Zone(kind="ob", direction="bullish", top=85.0, bottom=80.0, index=5, confirmed_index=6, source="swing")
    # Bearish supply OB at 100.0-102.0
    supply_ob = Zone(kind="ob", direction="bearish", top=102.0, bottom=100.0, index=10, confirmed_index=11, source="swing")
    signal.order_blocks = [demand_ob, supply_ob]

    frame = _frame()
    setup = TriCoreSetupEngine.evaluate(signal, frame, {"ping_pong_enabled": True, "minimum_rr": 2.0})
    assert setup.actionable is True
    assert setup.direction == "short"
    assert setup.setup_type == "range_boundary_ping_pong"
    assert setup.take_profit == 85.0
    assert setup.stop_loss > 102.0
    assert setup.risk_reward >= 2.0


def _short_signal() -> SMCSignal:
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="1h",
        volume_quality="exchange_aggressor",
        cvd_divergence="bearish",
        liquidity_swept=True,
        sweep_direction="high",
        liquidity_sweep={
            "candle_index": 19,
            "reference_index": 8,
            "extreme": 103.0,
            "reclaim_close": 100.5,
            "event_type": "single_bar_sweep",
        },
    )
    signal.cvd_divergence_evidence = {
        "reference_index": 8,
        "reference_cvd": 1500.0,
        "current_cvd": 500.0,
        "reclaim_close_price": 100.5,
        "price_excursion_atr": 0.7,
        "cvd_efficiency": 0.2,
    }
    signal.cvd = 500.0
    signal.swing_lows = [
        SwingPoint(8, 90.0, "low", pd.Timestamp("2026-09-01 08:00", tz="UTC"), 10)
    ]
    return signal


def test_counter_trend_short_rejected_in_trending_bullish_regime():
    signal = _short_signal()
    signal.market_regime = {"regime": "trending", "direction": "bullish"}
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert not setup.actionable
    assert any("Counter-trend SHORT rejected: Market regime is trending bullish" in r for r in setup.rejection_reasons)


def test_trend_aligned_long_accepted_in_trending_bullish_regime():
    signal = _signal()
    signal.market_regime = {"regime": "trending", "direction": "bullish"}
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert setup.actionable
    assert setup.direction == "long"


def test_counter_trend_long_rejected_in_trending_bearish_regime():
    signal = _signal()
    signal.market_regime = {"regime": "trending", "direction": "bearish"}
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert not setup.actionable
    assert any("Counter-trend LONG rejected: Market regime is trending bearish" in r for r in setup.rejection_reasons)


def test_counter_trend_short_rejected_by_htf_bullish_bias():
    signal = _short_signal()
    signal.market_regime = {"regime": "unknown", "direction": "neutral"}
    signal.htf_bias = "bullish"
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert not setup.actionable
    assert any("Counter-trend SHORT rejected: HTF bias is bullish" in r for r in setup.rejection_reasons)


def test_ranging_regime_allows_both_sides_ping_pong_regardless_of_htf():
    # Ping-Pong short works in ranging market even if HTF bias is bullish
    signal = _signal()
    signal.liquidity_swept = False
    signal.market_regime = {"regime": "ranging", "direction": "bullish"}
    signal.htf_bias = "bullish"
    signal.in_premium = True
    signal.delta_ratio = -0.05
    signal.cvd_divergence = "none"

    demand_ob = Zone(kind="ob", direction="bullish", top=85.0, bottom=80.0, index=5, confirmed_index=6, source="swing")
    supply_ob = Zone(kind="ob", direction="bearish", top=102.0, bottom=100.0, index=10, confirmed_index=11, source="swing")
    signal.order_blocks = [demand_ob, supply_ob]

    frame = _frame()
    setup = TriCoreSetupEngine.evaluate(signal, frame, {"ping_pong_enabled": True, "minimum_rr": 2.0})
    assert setup.actionable is True
    assert setup.direction == "short"
    assert setup.setup_type == "range_boundary_ping_pong"


def test_trending_regime_suppresses_counter_trend_ping_pong():
    # In trending bullish market, supply ping-pong short must be suppressed
    signal = _signal()
    signal.liquidity_swept = False
    signal.market_regime = {"regime": "trending", "direction": "bullish"}
    signal.in_premium = True
    signal.delta_ratio = -0.05
    signal.cvd_divergence = "none"

    demand_ob = Zone(kind="ob", direction="bullish", top=85.0, bottom=80.0, index=5, confirmed_index=6, source="swing")
    supply_ob = Zone(kind="ob", direction="bearish", top=102.0, bottom=100.0, index=10, confirmed_index=11, source="swing")
    signal.order_blocks = [demand_ob, supply_ob]

    frame = _frame()
    setup = TriCoreSetupEngine.evaluate(signal, frame, {"ping_pong_enabled": True, "minimum_rr": 2.0})
    assert not setup.actionable


def test_counter_trend_filter_disabled_allows_short():
    signal = _short_signal()
    signal.market_regime = {"regime": "trending", "direction": "bullish"}
    setup = TriCoreSetupEngine.evaluate(signal, _frame(), {"counter_trend_filter_enabled": False})
    assert setup.actionable
    assert setup.direction == "short"






def test_intraday_mtf_scenario_a_pullback_rejected():
    # 4H Down, 1H Down, 15m bounces up -> Reject Long (Pullback into supply, wait to short)
    signal = _signal()
    signal.market_regime = {"regime": "unknown", "direction": "neutral"}
    signal.intraday_bias = "bearish"
    signal.macro_bias = "bearish"
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert not setup.actionable
    assert any("Counter-trend LONG rejected: 1H intraday trend is bearish" in r for r in setup.rejection_reasons)


def test_intraday_mtf_scenario_b_counter_4h_allowed_with_room():
    # 4H Down, but 1H shifts UP -> 15m Long setup allowed if room to 4H resistance allows RR >= minimum_rr
    signal = _signal()
    signal.market_regime = {"regime": "unknown", "direction": "neutral"}
    signal.intraday_bias = "bullish"
    signal.macro_bias = "bearish"
    # Entry ~100.5, Stop ~98.0 (Risk = 2.5). 4H resistance at 115.0 gives distance 14.5 (RR ~ 5.8 > 1.5)
    signal.macro_obstacles = [{"type": "order_block", "mid": 115.0, "low": 114.0}]
    setup = TriCoreSetupEngine.evaluate(signal, _frame(), {"minimum_rr": 1.5})
    assert setup.actionable
    assert setup.direction == "long"


def test_intraday_mtf_scenario_b_insufficient_room_rejected():
    # 4H Down, 1H UP, but 4H resistance is too close (RR < minimum_rr) -> Reject No Chase
    signal = _signal()
    signal.market_regime = {"regime": "unknown", "direction": "neutral"}
    signal.intraday_bias = "bullish"
    signal.macro_bias = "bearish"
    # Entry ~100.5, Stop ~98.0. 4H resistance at 101.5 gives distance only 1.0 (RR < 1.0 < 1.5)
    signal.macro_obstacles = [{"type": "order_block", "mid": 101.5, "low": 101.2}]
    setup = TriCoreSetupEngine.evaluate(signal, _frame(), {"minimum_rr": 1.5})
    assert not setup.actionable
    assert any("Insufficient room to 4H obstacle" in r for r in setup.rejection_reasons)


def test_intraday_mtf_scenario_c_at_4h_obstacle_rejected():
    # At 4H obstacle within 0.25 ATR -> Reject Adverse location
    signal = _signal()
    signal.market_regime = {"regime": "unknown", "direction": "neutral"}
    signal.intraday_bias = "bullish"
    signal.macro_bias = "bearish"
    # Entry ~100.5. 4H obstacle at 100.6 (within 0.25 ATR)
    signal.macro_obstacles = [{"type": "order_block", "mid": 100.6, "low": 100.55}]
    setup = TriCoreSetupEngine.evaluate(signal, _frame(), {"minimum_rr": 1.5})
    assert not setup.actionable
    assert any("Adverse location: At major 4H obstacle" in r for r in setup.rejection_reasons)


def test_unicorn_breaker_fvg_overlap_upgrades_displacement_retest_to_grade_s():
    signal = _signal()
    signal.liquidity_swept = False
    signal.liquidity_sweep = {}
    signal.delta_ratio = 0.22
    signal.displacement = {
        "direction": "long",
        "index": 17,
        "structure_break_index": 17,
        "structure_break_id": "BOS:bullish:17:100.0",
    }
    # Breaker Block [99.8, 100.8] and overlapping Bullish FVG [100.2, 101.0]
    signal.breaker_blocks = [
        Zone(
            kind="breaker",
            direction="bullish",
            top=100.8,
            bottom=99.8,
            index=12,
            confirmed_index=17,
            source="swing",
        )
    ]
    signal.fvgs = [
        Zone(
            kind="fvg",
            direction="bullish",
            top=101.0,
            bottom=100.2,
            index=17,
            confirmed_index=18,
            source="swing",
        )
    ]
    frame = _frame()
    # Keep bars 18 away from zone so bar 19 is the first retest
    frame.iloc[18, frame.columns.get_loc("low")] = 102.0
    frame.iloc[18, frame.columns.get_loc("close")] = 102.5
    frame.iloc[19, frame.columns.get_loc("low")] = 100.1
    frame.iloc[19, frame.columns.get_loc("close")] = 100.5

    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable is True
    assert setup.grade == "S"
    assert any("Unicorn Confluence (Grade S)" in item for item in setup.evidence)


def test_ifvg_role_flip_retest_labeled_in_displacement_evidence():
    signal = _signal()
    signal.liquidity_swept = False
    signal.liquidity_sweep = {}
    signal.delta_ratio = 0.22
    signal.displacement = {
        "direction": "long",
        "index": 17,
        "structure_break_index": 17,
        "structure_break_id": "BOS:bullish:17:100.0",
    }
    signal.breaker_blocks = [
        Zone(
            kind="breaker",
            direction="bullish",
            top=100.8,
            bottom=99.8,
            index=14,
            confirmed_index=17,
            source="ifvg",
        )
    ]
    frame = _frame()
    frame.iloc[18, frame.columns.get_loc("low")] = 102.0
    frame.iloc[18, frame.columns.get_loc("close")] = 102.5
    frame.iloc[19, frame.columns.get_loc("low")] = 100.1
    frame.iloc[19, frame.columns.get_loc("close")] = 100.5

    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable is True
    assert setup.setup_type == "breaker_retest"
    assert setup.grade == "A"
    assert any("Inversion FVG (iFVG Role-Flip)" in item for item in setup.evidence)


def test_session_judas_sweep_upgrades_moderate_cvd_sweep_reversal_to_grade_s():
    signal = _signal()
    # Moderate CVD efficiency (< 0.15) would normally produce Grade A, not Grade S
    signal.cvd_divergence_evidence["cvd_efficiency"] = 0.05
    signal.liquidity_sweep["session_sweep"] = True
    signal.liquidity_sweep["session_window"] = "london_ny_killzone"

    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert setup.actionable is True
    assert setup.setup_type == "sweep_reversal"
    assert setup.grade == "S"
    assert any("Session Sweep (Judas/AMD)" in item for item in setup.evidence)


def test_fresh_sweep_reversal_remains_market_eligible_with_causal_zone_attached():
    """Track 1 Fix #1: sweep_reversal must always use limit_retest_only with causal zone attached."""
    signal = _signal()
    signal.liquidity_sweep["breach_start_index"] = 18
    signal.order_blocks = [
        Zone(
            kind="ob",
            direction="bullish",
            top=100.2,
            bottom=98.8,
            index=18,
            confirmed_index=19,
            source="swing",
        )
    ]
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert setup.actionable is True
    assert setup.entry_policy == "limit_retest_only"
    assert setup.order_type == "limit"
    assert setup.limit_zone_id != ""


def test_counter_trend_long_rejected_in_volatile_bearish_regime():
    signal = _signal()
    signal.market_regime = {"regime": "volatile", "direction": "bearish"}
    signal.intraday_bias = "neutral"
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert not setup.actionable
    assert any("Counter-trend LONG rejected: Market regime is volatile bearish" in r for r in setup.rejection_reasons)

def test_adaptive_momentum_entry_expands_market_ceiling_for_strong_flow():
    """Track 1 Fix #1: sweep_reversal uses limit_retest_only regardless of flow strength, rejecting overextended entries without causal zone."""
    signal = _signal()
    signal.liquidity_sweep["candle_index"] = 18
    signal.liquidity_sweep["reclaim_close"] = 99.0
    signal.cvd_divergence_evidence["reclaim_close_price"] = 99.0
    signal.delta_ratio = 0.20
    setup_strong = TriCoreSetupEngine.evaluate(signal, _frame())
    assert setup_strong.actionable is True
    assert setup_strong.entry_policy == "limit_retest_only"

    signal.delta_ratio = 0.05
    signal.cvd_divergence_evidence["price_excursion_atr"] = 0.10
    signal.order_blocks = [
        Zone(kind="ob", direction="bullish", top=99.2, bottom=98.5, index=18, confirmed_index=19, source="swing")
    ]
    setup_weak = TriCoreSetupEngine.evaluate(signal, _frame())
    assert setup_weak.actionable is True
    assert setup_weak.entry_policy == "limit_retest_only"


def test_ranging_regime_rejects_counter_trend_when_ping_pong_disabled():
    """NEAR regression: In a ranging market, if HTF bias is bullish and ping-pong is off, SHORT must be rejected."""
    signal = _signal()
    signal.liquidity_swept = False
    signal.market_regime = {"regime": "ranging", "direction": "bullish"}
    signal.htf_bias = "bullish"
    signal.intraday_bias = "bullish"
    signal.in_premium = True
    signal.delta_ratio = -0.15
    signal.cvd_divergence = "none"

    demand_ob = Zone(kind="ob", direction="bullish", top=85.0, bottom=80.0, index=5, confirmed_index=6, source="swing")
    supply_ob = Zone(kind="ob", direction="bearish", top=102.0, bottom=100.0, index=10, confirmed_index=11, source="swing")
    signal.order_blocks = [demand_ob, supply_ob]

    frame = _frame()
    # Ping-Pong is False (default)
    setup = TriCoreSetupEngine.evaluate(signal, frame, {"ping_pong_enabled": False, "counter_trend_filter_enabled": True})
    assert setup.actionable is False
    assert any("Counter-trend SHORT rejected" in r for r in setup.rejection_reasons)


def test_displacement_retest_forces_limit_when_price_is_extended():
    """XRP/SUI regression: Displacement retest must NOT chase on market when price has broken out past zone."""
    signal = _signal()
    signal.liquidity_swept = False
    signal.market_regime = {"regime": "trending", "direction": "bearish"}
    signal.htf_bias = "bearish"
    signal.intraday_bias = "bearish"
    signal.in_premium = True
    signal.delta_ratio = -0.10
    signal.cvd_divergence = "none"
    signal.atr = 1.0

    # Bearish OB is at 100.0 - 102.0. Current price has extended down to 98.0 (2.0 below zone edge 100.0)
    bearish_ob = Zone(kind="ob", direction="bearish", top=102.0, bottom=100.0, index=10, confirmed_index=11, source="swing")
    signal.order_blocks = [bearish_ob]
    signal.displacement = {
        "event_id": "disp_test",
        "side": "short",
        "candle_index": 12,
        "displacement_size": 2.0,
        "broken_level": 100.0,
    }

    frame = _frame()
    # Mock current price far below OB
    frame.loc[frame.index[-1], "close"] = 98.0
    setup = TriCoreSetupEngine.evaluate(signal, frame, {"counter_trend_filter_enabled": True})
    if setup.actionable:
        # If actionable, it MUST be a limit order waiting at the zone edge, NOT chasing market at 98.0
        assert setup.entry_policy == "limit_retest_only"
        assert setup.order_type == "limit"
        assert setup.entry == 100.0  # zone edge


def test_market_entry_enforces_minimum_breathing_room():
    """Verify market entry never has an ultra-tight stop loss below 0.65% breathing room."""
    signal = _signal()
    signal.liquidity_sweep["candle_index"] = 18
    signal.liquidity_sweep["reclaim_close"] = 100.0
    signal.liquidity_sweep["level_price"] = 99.8  # only 0.2% distance
    signal.cvd_divergence_evidence["reclaim_close_price"] = 100.0
    signal.delta_ratio = 0.20

    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    if setup.actionable and setup.order_type == "market":
        sl_dist_pct = abs(setup.entry - setup.stop_loss) / setup.entry
        assert sl_dist_pct >= 0.0065  # at least 0.65%


def test_htf_aligned_pullback_short_not_deadlocked_by_15m_bullish_regime_direction():
    """When 1H trend is bearish, a 15m bounce (regime direction=bullish) is a pullback and must not deadlock SHORT."""
    signal = _short_signal()
    signal.market_regime = {"regime": "volatile", "direction": "bullish"}
    signal.intraday_bias = "bearish"
    signal.htf_bias = "bearish"
    setup = TriCoreSetupEngine.evaluate(signal, _frame())
    assert setup.actionable is True
    assert setup.direction == "short"
    assert not any("Counter-trend SHORT rejected" in r for r in setup.rejection_reasons)


def test_volatile_sweep_suspended_when_unaligned_but_allowed_when_htf_aligned():
    """Volatile sweep is suspended when counter-trend/neutral, but allowed when aligned with 1H trend."""
    # Unaligned (neutral 1H) -> suspended
    sig_unaligned = _short_signal()
    sig_unaligned.market_regime = {"regime": "volatile", "direction": "bearish"}
    sig_unaligned.intraday_bias = "neutral"
    setup_unaligned = TriCoreSetupEngine.evaluate(sig_unaligned, _frame())
    assert not setup_unaligned.actionable
    assert "VOLATILE_SWEEP_SUSPENDED" in setup_unaligned.rejection_codes

    # Aligned with 1H Bearish -> allowed as limit_retest_only (never blind market in volatile)
    sig_aligned = _short_signal()
    sig_aligned.market_regime = {"regime": "volatile", "direction": "bearish"}
    sig_aligned.intraday_bias = "bearish"
    setup_aligned = TriCoreSetupEngine.evaluate(sig_aligned, _frame())
    assert setup_aligned.actionable is True
    assert setup_aligned.direction == "short"
    assert setup_aligned.entry_policy == "limit_retest_only"
    assert setup_aligned.order_type == "limit"


def test_follow_through_sweep_invalidated_when_closing_back_beyond_reclaim():
    """#57 NEAR regression: If age >= 1 follow-through bar closes back beyond reclaim_close, invalidate sweep."""
    sig = _short_signal()
    sig.liquidity_sweep["candle_index"] = 18  # age = 1
    sig.liquidity_sweep["reclaim_close"] = 100.2  # bar 18 reclaimed at 100.2, but bar 19 closed at 100.5 (> 100.2)
    sig.liquidity_sweep["level_price"] = 100.3
    sig.cvd_divergence_evidence["reclaim_close_price"] = 100.2
    setup = TriCoreSetupEngine.evaluate(sig, _frame())
    assert not setup.actionable
    assert any("follow-through candle closed" in r for r in setup.rejection_reasons)


def test_order_block_retest_rejects_candle_closing_below_bullish_ob():
    """#72 DOT regression: A candle that closes below a bullish OB bottom is a breakdown, not a valid retest."""
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        101.5, 102.0, 101.0, 101.5
    ]
    # Bar 19 overlaps [99.7, 100.2] but closes at 99.4 (below OB bottom 99.7)
    frame.iloc[19, frame.columns.get_loc("open")] = 100.5
    frame.iloc[19, frame.columns.get_loc("high")] = 100.6
    frame.iloc[19, frame.columns.get_loc("low")] = 99.3
    frame.iloc[19, frame.columns.get_loc("close")] = 99.4

    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = -0.03
    signal.displacement = {
        "index": 15,
        "direction": "long",
        "structure_break_id": "swing:BOS:bullish:15:100",
        "structure_break_index": 15,
    }
    signal.order_blocks = [
        Zone("ob", "bullish", top=100.2, bottom=99.7, index=8, confirmed_index=15)
    ]
    signal.equal_highs = [110.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert not setup.actionable


def test_multi_bar_reclaim_sweep_uses_limit_retest_instead_of_market():
    """#74 LINK regression: multi_bar_reclaim sweep must require limit_retest_only, not blind market."""
    sig = _short_signal()
    sig.liquidity_sweep["event_type"] = "multi_bar_reclaim"
    setup = TriCoreSetupEngine.evaluate(sig, _frame())
    assert setup.actionable is True
    assert setup.entry_policy == "limit_retest_only"
    assert setup.order_type == "limit"


def test_displacement_retest_on_internal_structure_or_ranging_requires_limit_retest():
    """#86 SUI & #88 ADA regression: internal CHoCH or ranging displacement_retest must require limit_retest_only."""
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        101.5, 102.0, 101.0, 101.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.cvd_divergence = "none"
    signal.delta_ratio = 0.4
    signal.displacement = {
        "index": 15,
        "direction": "long",
        "structure_break_id": "internal:CHoCH:bullish:15:100",
        "structure_break_index": 15,
    }
    signal.order_blocks = [
        Zone("ob", "bullish", top=100.6, bottom=99.7, index=8, confirmed_index=15, source="internal")
    ]
    signal.equal_highs = [110.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable is True
    assert setup.setup_type == "displacement_retest"
    assert setup.entry_policy == "limit_retest_only"
    assert setup.order_type == "limit"


def test_waterfall_bos_arms_proactive_limit_at_fresh_fvg_when_bar_is_climax():
    """Hybrid Option 1: On a fresh waterfall Swing BOS in Discount, immediately arm a Proactive Limit at the FVG edge."""
    frame = _frame()
    # Bar 19 is a huge climax breakdown bar (high=100.5, low=96.5 -> range 4.0 > 1.25*ATR) closing at 97.0
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [100.2, 100.5, 96.5, 97.0]
    signal = _signal()
    signal.liquidity_swept = False
    signal.market_regime = {"regime": "trending", "direction": "bearish"}
    signal.htf_bias = "bearish"
    signal.intraday_bias = "bearish"
    signal.in_discount = True
    signal.cvd_divergence = "none"
    signal.delta_ratio = -0.30
    signal.displacement = {
        "index": 19,
        "direction": "short",
        "structure_break_id": "swing:BOS:bearish:19:98.5",
        "structure_break_index": 19,
    }
    signal.fvgs = [
        Zone("fvg", "bearish", top=101.0, bottom=100.0, index=18, confirmed_index=19, source="swing")
    ]
    signal.order_blocks = []
    signal.equal_lows = [90.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable is True
    assert setup.direction == "short"
    assert setup.entry_policy == "limit_retest_only"
    assert setup.order_type == "limit"
    assert setup.entry == 100.0
    assert any("Proactive Trend Limit Armed" in e for e in setup.evidence)


def test_trending_bos_allows_momentum_market_when_flow_strong_and_bar_compact():
    """Hybrid Option 2: On a compact Swing BOS bar with strong institutional flow, allow direct Momentum Market continuation."""
    frame = _frame()
    # Narrow ATR (~0.8) so buffer is ~0.08, and bar 19 is compact (range 0.6 <= 1.25*ATR) closing at 99.6
    frame["high"] = 100.4
    frame["low"] = 99.6
    frame["close"] = 100.0
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [100.1, 100.2, 99.55, 99.6]
    signal = _signal()
    signal.liquidity_swept = False
    signal.market_regime = {"regime": "trending", "direction": "bearish"}
    signal.htf_bias = "bearish"
    signal.intraday_bias = "bearish"
    signal.in_discount = True
    signal.cvd_divergence = "none"
    signal.delta_ratio = -0.28
    signal.displacement = {
        "index": 19,
        "direction": "short",
        "structure_break_id": "swing:BOS:bearish:19:99.7",
        "structure_break_index": 19,
    }
    # Zone top=100.35 -> stop ~100.43 -> SL distance from 99.6 is ~0.83% (within 0.55% - 1.05%)
    signal.fvgs = [
        Zone("fvg", "bearish", top=100.35, bottom=99.85, index=18, confirmed_index=19, source="swing")
    ]
    signal.order_blocks = []
    signal.equal_lows = [95.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert setup.actionable is True
    assert setup.direction == "short"
    assert setup.entry_policy == "market_eligible"
    assert setup.order_type == "market"
    assert setup.entry == 99.6
    assert any("Momentum BOS Continuation (Market)" in e for e in setup.evidence)


def test_ranging_regime_suppresses_inducement_order_block_displacement_retest():
    """#93 BTC / #95 LINK / #111 ADA regression: Order Block displacement_retest in ranging is suppressed as inducement."""
    frame = _frame()
    frame.loc[frame.index[16:19], ["open", "high", "low", "close"]] = [
        101.5, 102.0, 101.0, 101.5
    ]
    signal = _signal()
    signal.liquidity_swept = False
    signal.market_regime = {"regime": "ranging", "direction": "bullish"}
    signal.htf_bias = "bullish"
    signal.intraday_bias = "bullish"
    signal.cvd_divergence = "none"
    signal.delta_ratio = 0.25
    signal.displacement = {
        "index": 15,
        "direction": "long",
        "structure_break_id": "swing:BOS:bullish:15:100",
        "structure_break_index": 15,
    }
    signal.order_blocks = [
        Zone("ob", "bullish", top=100.6, bottom=99.7, index=8, confirmed_index=15, source="swing")
    ]
    signal.equal_highs = [110.0]
    setup = TriCoreSetupEngine.evaluate(signal, frame)
    assert not setup.actionable
    assert "RANGING_INDUCEMENT_OB_SUPPRESSED" in setup.rejection_codes


def test_compact_multi_bar_reclaim_sweep_preserves_market_execution():
    """Track 1 Fix #1: Even compact multi_bar_reclaim sweeps now enforce limit_retest_only."""
    frame = _frame()
    frame["high"] = 100.4
    frame["low"] = 99.6
    frame["close"] = 100.0
    frame.loc[frame.index[19], ["open", "high", "low", "close"]] = [99.8, 100.2, 99.3, 100.0]
    sig = _signal()
    sig.liquidity_sweep["event_type"] = "multi_bar_reclaim"
    sig.liquidity_sweep["extreme"] = 99.3
    sig.liquidity_sweep["level"] = 99.7
    sig.liquidity_sweep["reclaim_close"] = 100.0
    sig.cvd_divergence_evidence["reclaim_close_price"] = 100.0
    setup = TriCoreSetupEngine.evaluate(sig, frame)
    assert setup.actionable is True
    assert setup.entry_policy == "limit_retest_only"
    assert setup.order_type == "limit"


def test_sweep_limit_entry_clamped_to_reclaimed_level():
    """#110 LINK regression: When a sweep uses limit_retest_only, short limit must never exceed the swept resistance level."""
    sig = _short_signal()
    sig.liquidity_sweep["event_type"] = "multi_bar_reclaim"
    sig.liquidity_sweep["extreme"] = 103.0
    sig.liquidity_sweep["level"] = 101.2
    sig.liquidity_sweep["reclaim_close"] = 100.5
    setup = TriCoreSetupEngine.evaluate(sig, _frame())
    assert setup.actionable is True
    assert setup.entry_policy == "limit_retest_only"
    assert setup.entry <= 101.2


def test_strict_4h_freight_train_blocks_counter_trend_entries():
    """Strict 4H freight-train stack blocks counter-trend entries while preserving 4H mixed rebounds."""
    # Case 1: Strict 4H downtrend (close < EMA20 < EMA50) blocks LONG even if 1H flipped bullish
    long_sig = _signal()
    long_sig.market_regime = {"regime": "ranging", "direction": "neutral"}
    long_sig.intraday_bias = "bullish"
    long_sig.macro_bias = "bearish"
    long_sig.macro_strict_trend = "bearish"
    setup_blocked_long = TriCoreSetupEngine.evaluate(long_sig, _frame())
    assert not setup_blocked_long.actionable
    assert "COUNTER_4H_FREIGHT_TRAIN" in setup_blocked_long.rejection_codes

    # Case 2: Strict 4H uptrend (close > EMA20 > EMA50) blocks SHORT even if 1H flipped bearish
    short_sig = _short_signal()
    short_sig.market_regime = {"regime": "ranging", "direction": "neutral"}
    short_sig.intraday_bias = "bearish"
    short_sig.macro_bias = "bullish"
    short_sig.macro_strict_trend = "bullish"
    setup_blocked_short = TriCoreSetupEngine.evaluate(short_sig, _frame())
    assert not setup_blocked_short.actionable
    assert "COUNTER_4H_FREIGHT_TRAIN" in setup_blocked_short.rejection_codes

    # Case 3: 4H mixed (price crossed above 4H EMA20 rebound) ALLOWS aligned 1H LONG
    long_sig_mixed = _signal()
    long_sig_mixed.market_regime = {"regime": "ranging", "direction": "neutral"}
    long_sig_mixed.intraday_bias = "bullish"
    long_sig_mixed.macro_bias = "bearish"
    long_sig_mixed.macro_strict_trend = "mixed"
    setup_allowed = TriCoreSetupEngine.evaluate(long_sig_mixed, _frame())
    assert setup_allowed.actionable is True
    assert setup_allowed.direction == "long"


def test_1h_ema_consensus_overrides_noisy_smc_flip():
    """1H EMA20/50 anchor prevents noisy 5-bar 1H SMC swing breaks from flipping bias into dead-cat bounces."""
    from app.services.execution_analysis import (
        compute_ema_trend_snapshot,
        resolve_intraday_trend_bias,
    )

    # Sustained 1H downtrend followed by a tiny 3-bar dead-cat bounce
    down_closes = list(np.linspace(120.0, 95.0, 70)) + [95.4, 95.8, 96.1]
    down_df = pd.DataFrame({"close": down_closes})
    snap_down = compute_ema_trend_snapshot(down_df)
    assert snap_down["ema_bias"] == "bearish"
    # Even if 1H SMC flipped bullish on the 3-bar bounce, consensus holds bearish
    assert resolve_intraday_trend_bias("bullish", snap_down["ema_bias"]) == "bearish"

    # Strict 4H bear stack vs 4H mixed rebound
    strict_bear_df = pd.DataFrame({"close": list(np.linspace(140.0, 90.0, 70))})
    assert compute_ema_trend_snapshot(strict_bear_df)["strict_trend"] == "bearish"

    # Rebound where price reclaims above EMA20 while EMA20 < EMA50 -> mixed (not freight-train)
    rebound_df = pd.DataFrame({"close": list(np.linspace(140.0, 90.0, 68)) + [97.0, 102.0]})
    snap_rebound = compute_ema_trend_snapshot(rebound_df)
    assert snap_rebound["ema_bias"] == "bearish"
    assert snap_rebound["strict_trend"] == "mixed"

    # Short dataframe (<50 bars) gracefully falls back to SMC bias
    short_df = pd.DataFrame({"close": [100.0, 101.0, 102.0]})
    snap_short = compute_ema_trend_snapshot(short_df)
    assert snap_short["ema_bias"] == "neutral"
    assert resolve_intraday_trend_bias("bullish", snap_short["ema_bias"]) == "bullish"








