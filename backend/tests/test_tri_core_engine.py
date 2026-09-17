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
    assert setup.trigger_state == "entry_ready"
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


def test_legacy_scenario_and_sqz_cannot_change_strategy_authority():
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
    signal.equal_highs = [102.0, 110.0]
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



