import numpy as np
import pandas as pd

from app.engines.scenario_engine import ScenarioMatrixEngine
from app.engines.smc_engine import SMCSignal, StructureBreak, SwingPoint, Zone


def _dummy_df(n=50, base_price=100.0):
    prices = base_price + np.linspace(0, 5, n)
    return pd.DataFrame({
        "open": prices - 0.2,
        "high": prices + 1.0,
        "low": prices - 1.0,
        "close": prices,
        "volume": np.full(n, 1000.0),
    })


def test_scenario_s1_bullish_breakout():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.current_price = 105.0
    sig.bias = "bullish"
    sig.bos = True
    sig.squeeze_status = "squeeze_fire"
    sig.squeeze_momentum = 1.5
    sig.delta_ratio = 0.25
    sig.swing_structures = [
        StructureBreak(tag="BOS", kind="swing", direction="bullish", level=104.0, pivot_index=10)
    ]
    sig.equal_highs = [112.0]

    res = ScenarioMatrixEngine.classify(sig, _dummy_df(50, 100.0))
    assert res.scenario_id == "S1_BULL_BREAKOUT"
    assert res.suggested_action == "buy_market"
    assert res.setup_grade == "GRADE_S"
    assert res.actionable is True
    assert "Bullish Momentum Breakout" in res.name_en


def test_s1_requires_real_directional_squeeze_fire():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.current_price = 100.0
    sig.bias = "bullish"
    sig.bos = True
    sig.squeeze_status = "no_squeeze"
    sig.squeeze_momentum = 1.5
    sig.delta_ratio = 0.25

    res = ScenarioMatrixEngine.classify(sig, _dummy_df(50, 100.0))
    assert res.scenario_id != "S1_BULL_BREAKOUT"


def test_scenario_s2_bullish_ob_retest():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.current_price = 98.0
    sig.bias = "bullish"
    sig.in_discount = True
    sig.equilibrium = 100.0
    sig.order_block = Zone(kind="ob", direction="bullish", top=99.0, bottom=97.0)
    sig.delta_absorption = True
    sig.delta_absorption_type = "bullish_absorption"
    sig.squeeze_status = "squeeze_on"
    sig.equal_highs = [110.0]

    df = _dummy_df(50, 98.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [97.8, 99.2, 97.5, 98.5]
    res = ScenarioMatrixEngine.classify(sig, df)
    assert res.scenario_id == "S2_BULL_OB_RETEST"
    assert res.suggested_action == "buy_limit_ob"
    assert res.entry_style == "limit"
    assert res.stop_loss < 97.0
    assert res.contingency_plan.plan_a != ""
    assert res.contingency_plan.plan_b != ""


def test_s2_requires_price_to_touch_order_block():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.current_price = 100.0
    sig.bias = "bullish"
    sig.in_discount = True
    sig.equilibrium = 120.0
    sig.order_block = Zone(kind="ob", direction="bullish", top=150.0, bottom=149.0)

    res = ScenarioMatrixEngine.classify(sig, _dummy_df(50, 100.0))
    assert res.scenario_id != "S2_BULL_OB_RETEST"
    assert not (res.actionable and res.suggested_action == "buy_limit_ob")


def test_s2_uses_touched_active_ob_instead_of_unrelated_primary_ob():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.current_price = 98.5
    sig.bias = "bullish"
    sig.in_discount = True
    sig.order_block = Zone(
        kind="ob", direction="bearish", top=120.0, bottom=118.0
    )
    sig.order_blocks = [
        sig.order_block,
        Zone(kind="ob", direction="bullish", top=99.0, bottom=97.0),
    ]
    sig.delta_ratio = 0.20
    sig.equal_highs = [110.0]
    df = _dummy_df(50, 98.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [
        97.8, 99.2, 97.5, 98.5
    ]

    result = ScenarioMatrixEngine.classify(sig, df)

    assert result.scenario_id == "S2_BULL_OB_RETEST"
    assert result.actionable is True
    assert result.stop_loss < 97.0


def test_scenario_s3_bearish_top_sweep():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.current_price = 105.0
    sig.in_premium = True
    sig.equilibrium = 100.0
    sig.liquidity_swept = True
    sig.sweep_direction = "high"
    sig.sweep_price = 106.0
    sig.momentum_direction = "decelerating_up"
    sig.equal_highs = [106.0]
    sig.delta_ratio = -0.20

    res = ScenarioMatrixEngine.classify(sig, _dummy_df(50, 105.0))
    assert res.scenario_id == "S3_BEAR_TOP_SWEEP"
    assert res.suggested_action == "sell_market"
    assert res.setup_grade == "GRADE_S"
    assert res.stop_loss > 106.0


def test_scenario_s5_mid_range_compression():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.current_price = 100.0
    sig.equilibrium = 100.0
    sig.in_discount = False
    sig.in_premium = False
    sig.squeeze_status = "squeeze_on"

    res = ScenarioMatrixEngine.classify(sig, _dummy_df(50, 100.0))
    assert res.scenario_id == "S5_MID_RANGE_COMPRESSION"
    assert res.suggested_action == "wait_two_way_plan"
    assert res.entry_style == "wait"
    assert "WAIT" in res.contingency_plan.plan_a
    assert "WAIT" in res.contingency_plan.plan_b
    assert "BUY" not in res.contingency_plan.plan_a
    assert "SHORT" not in res.contingency_plan.plan_b


def test_scenario_s6_discount_exhaustion():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.current_price = 95.0
    sig.in_discount = True
    sig.equilibrium = 100.0
    sig.momentum_direction = "decelerating_down"
    sig.delta_ratio = 0.15

    res = ScenarioMatrixEngine.classify(sig, _dummy_df(50, 95.0))
    assert res.scenario_id == "S6_DIVERGENCE_EXHAUSTION"
    assert res.suggested_action == "tighten_sl"
    assert res.setup_grade == "WAIT"
    assert "Discount" in res.name_en


def test_scenario_s3_never_manufactures_target_to_rescue_rr():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.current_price = 100.0
    sig.in_premium = True
    sig.equilibrium = 100.0
    sig.liquidity_swept = True
    sig.sweep_direction = "high"
    sig.sweep_price = 100.2
    sig.momentum_direction = "decelerating_up"
    sig.equal_highs = [100.2]
    sig.delta_ratio = -0.20

    res = ScenarioMatrixEngine.classify(sig, _dummy_df(50, 100.0))
    assert res.scenario_id == "S3_BEAR_SWEEP_WATCH"
    assert res.actionable is False
    assert res.take_profit_1 is None
    assert "R:R" in res.contingency_plan.plan_a


def test_ob_touch_without_rejection_is_watch_only():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.current_price = 98.0
    sig.bias = "bullish"
    sig.in_discount = True
    sig.equilibrium = 100.0
    sig.order_block = Zone(kind="ob", direction="bullish", top=99.0, bottom=97.0)
    sig.equal_highs = [110.0]

    res = ScenarioMatrixEngine.classify(sig, _dummy_df(50, 98.0))

    assert res.scenario_id == "S2_BULL_OB_WATCH"
    assert res.actionable is False
    assert res.setup_grade == "WATCH"


def test_unqualified_fallback_is_neutral_no_edge_not_compression():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=100.0)
    sig.equilibrium = 100.0

    res = ScenarioMatrixEngine.classify(sig, _dummy_df(50, 100.0))

    assert res.scenario_id == "NEUTRAL_NO_EDGE"
    assert res.actionable is False


def test_15m_support_breakdown_is_classified_instead_of_neutral():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=97.0)
    sig.equal_lows = [99.0]
    sig.delta_ratio = -0.25

    df = _dummy_df(50, 96.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [100.0, 100.2, 96.5, 97.0]

    res = ScenarioMatrixEngine.classify(sig, df)

    assert res.scenario_id == "S8_SUPPORT_BREAKDOWN_CONFIRMED"
    assert res.reaction_state == "BREAKDOWN_CONFIRMED"
    assert res.reaction_evidence["timeframe"] == "15m"
    assert res.reaction_evidence["candle_policy"] == "closed_only"
    assert res.actionable is False
    assert res.suggested_action == "wait_confirmation"


def test_15m_support_bounce_requires_closed_body_and_bullish_flow():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=100.4)
    sig.equal_lows = [99.0]
    sig.delta_ratio = 0.20

    df = _dummy_df(50, 96.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [99.0, 100.8, 98.9, 100.4]

    res = ScenarioMatrixEngine.classify(sig, df)

    assert res.scenario_id == "S8_SUPPORT_BOUNCE_CONFIRMED"
    assert res.reaction_state == "BOUNCE_CONFIRMED"
    assert res.reaction_evidence["bullish_flow"] is True
    assert res.actionable is False
    assert res.entry_status == "CONFIRMED_NO_ENTRY"
    assert res.suggested_action == "do_not_chase"
    assert res.entry_block_reason == "no opposing 15M liquidity target above price"


def test_15m_confirmed_support_bounce_opens_bounded_entry_window():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=100.4)
    sig.equal_lows = [99.0]
    sig.equal_highs = [110.0]
    sig.delta_ratio = 0.20

    df = _dummy_df(50, 96.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [
        99.0, 100.8, 98.9, 100.4
    ]

    result = ScenarioMatrixEngine.classify(sig, df)

    assert result.scenario_id == "S8_SUPPORT_BOUNCE_ENTRY"
    assert result.actionable is True
    assert result.suggested_action == "buy_market"
    assert result.reaction_state == "ENTRY_WINDOW_OPEN"
    assert result.entry_status == "ENTRY_WINDOW_OPEN"
    assert result.entry_block_reason is None
    assert result.reaction_evidence["entry_window_bars"] == 3
    assert result.reaction_evidence["reaction_age_bars"] == 0


def test_15m_support_entry_window_latches_reaction_for_two_more_bars():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=101.2)
    sig.bias = "bullish"
    sig.bos = True
    sig.equal_lows = [99.0]
    sig.equal_highs = [110.0]

    df = _dummy_df(50, 96.0)
    df.loc[df.index[-3], ["open", "high", "low", "close"]] = [
        99.0, 100.8, 98.9, 100.4
    ]
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [
        100.4, 101.2, 100.2, 101.0
    ]
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [
        101.0, 101.4, 100.8, 101.2
    ]

    result = ScenarioMatrixEngine.classify(sig, df)

    assert result.scenario_id == "S8_SUPPORT_BOUNCE_ENTRY"
    assert result.actionable is True
    assert result.reaction_evidence["reaction_age_bars"] == 2
    assert result.reaction_evidence["entry_window_remaining_bars"] == 0


def test_15m_support_false_break_reclaim_is_distinct_from_bounce():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=100.5)
    sig.equal_lows = [99.0]
    sig.delta_absorption = True
    sig.delta_absorption_type = "bullish_absorption"

    df = _dummy_df(50, 96.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [98.7, 100.8, 98.0, 100.5]

    res = ScenarioMatrixEngine.classify(sig, df)

    assert res.scenario_id == "S8_SUPPORT_FALSE_BREAK_RECLAIM"
    assert res.reaction_state == "FALSE_BREAK_RECLAIM"
    assert res.actionable is False
    assert res.entry_status == "CONFIRMED_NO_ENTRY"
    assert res.suggested_action == "do_not_chase"
    assert "no opposing 15M liquidity target" in res.entry_block_reason
    assert "ห้ามไล่ราคา" in res.contingency_plan.plan_a


def test_15m_support_touch_without_confirmation_remains_wait():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=99.15)
    sig.equal_lows = [99.0]

    df = _dummy_df(50, 96.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [99.1, 99.5, 98.8, 99.15]

    res = ScenarioMatrixEngine.classify(sig, df)

    assert res.scenario_id == "S8_SUPPORT_TOUCH_WAIT"
    assert res.reaction_state == "TOUCH_WAIT"
    assert res.actionable is False
    assert "WAIT" in res.contingency_plan.plan_a


def test_support_reaction_state_machine_does_not_run_on_1h():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="1h", current_price=97.0)
    sig.equal_lows = [99.0]
    sig.delta_ratio = -0.25

    df = _dummy_df(50, 96.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [100.0, 100.2, 96.5, 97.0]

    res = ScenarioMatrixEngine.classify(sig, df)

    assert res.scenario_id == "NEUTRAL_NO_EDGE"
    assert res.reaction_state is None


def test_15m_resistance_rejection_is_classified_from_bearish_ob():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=104.0)
    sig.order_blocks = [Zone(kind="ob", direction="bearish", top=107.0, bottom=105.0)]
    sig.delta_ratio = -0.20

    df = _dummy_df(50, 101.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [106.5, 106.8, 103.5, 104.0]

    res = ScenarioMatrixEngine.classify(sig, df)

    assert res.scenario_id == "S9_RESISTANCE_REJECTION_CONFIRMED"
    assert res.reaction_state == "REJECTION_CONFIRMED"
    assert res.reaction_evidence["resistance_source"] == "bearish_order_block"
    assert res.reaction_evidence["bearish_flow"] is True
    assert res.actionable is False


def test_15m_confirmed_resistance_rejection_opens_short_entry_window():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=104.0)
    sig.order_blocks = [
        Zone(kind="ob", direction="bearish", top=107.0, bottom=105.0)
    ]
    sig.equal_lows = [90.0]
    sig.delta_ratio = -0.20

    df = _dummy_df(50, 101.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [
        106.5, 106.8, 103.5, 104.0
    ]

    result = ScenarioMatrixEngine.classify(sig, df)

    assert result.scenario_id == "S9_RESISTANCE_REJECTION_ENTRY"
    assert result.actionable is True
    assert result.suggested_action == "sell_market"
    assert result.reaction_state == "ENTRY_WINDOW_OPEN"
    assert result.entry_status == "ENTRY_WINDOW_OPEN"


def test_ranging_gate_accepts_confirmed_15m_zone_reaction_without_fake_sweep():
    from copy import deepcopy

    from app.engines.regime_engine import DEFAULT_REGIME_POLICY
    from app.engines.strategy_engine import StrategyEngine

    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.direction = "long"
    sig.bias = "bullish"
    sig.confluence = 80
    sig.risk_reward = 2.5
    sig.entry = 100.0
    sig.stop_loss = 98.0
    sig.take_profit = 105.0
    sig.volume_quality = "exchange_aggressor"
    sig.tri_core_setup = {"actionable": True, "direction": "long", "grade": "A",
                          "smc_confirmed": True, "cvd_confirmed": True,
                          "flow_confirmed": True}
    sig.zone_position = "mid_range"
    sig.indicator_decision = {"ready": True, "coverage": 100.0}
    sig.scenario = {
        "scenario_id": "S8_SUPPORT_BOUNCE_ENTRY",
        "archetype": "support_reaction",
        "reaction_state": "ENTRY_WINDOW_OPEN",
        "actionable": True,
    }
    sig.market_regime = {
        "regime": "ranging",
        "direction": "bullish",
        "ready": True,
        "policy": deepcopy(DEFAULT_REGIME_POLICY["policies"]["ranging"]),
    }

    result = StrategyEngine().evaluate(sig)

    assert result.approved is True
    assert any("Causal SMC setup" in check for check in result.passed_checks)


def test_15m_resistance_false_break_rejection_is_distinct():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=104.0)
    sig.order_blocks = [Zone(kind="ob", direction="bearish", top=107.0, bottom=105.0)]
    sig.delta_absorption = True
    sig.delta_absorption_type = "bearish_absorption"

    df = _dummy_df(50, 101.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [106.8, 107.8, 103.5, 104.0]

    res = ScenarioMatrixEngine.classify(sig, df)

    assert res.scenario_id == "S9_RESISTANCE_FALSE_BREAK_REJECTION"
    assert res.reaction_state == "FALSE_BREAK_REJECTION"
    assert res.actionable is False


def test_15m_resistance_breakout_requires_bullish_close_and_flow():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=108.0)
    sig.order_blocks = [Zone(kind="ob", direction="bearish", top=107.0, bottom=105.0)]
    sig.delta_ratio = 0.20

    df = _dummy_df(50, 103.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [106.5, 108.5, 106.0, 108.0]

    res = ScenarioMatrixEngine.classify(sig, df)

    assert res.scenario_id == "S9_RESISTANCE_BREAKOUT_CONFIRMED"
    assert res.reaction_state == "BREAKOUT_CONFIRMED"
    assert res.reaction_evidence["bullish_flow"] is True
    assert res.actionable is False


def test_15m_resistance_touch_without_confirmation_remains_wait():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=105.9)
    sig.order_blocks = [Zone(kind="ob", direction="bearish", top=107.0, bottom=105.0)]

    df = _dummy_df(50, 101.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [105.8, 106.5, 105.2, 105.9]

    res = ScenarioMatrixEngine.classify(sig, df)

    assert res.scenario_id == "S9_RESISTANCE_TOUCH_WAIT"
    assert res.reaction_state == "TOUCH_WAIT"
    assert res.actionable is False


def test_reaction_is_independent_when_primary_s1_watch_short_circuits():
    sig = SMCSignal(symbol="ADA/USDT", timeframe="15m", current_price=99.1)
    sig.bias = "bearish"
    sig.bos = True
    sig.squeeze_status = "squeeze_fire"
    sig.squeeze_momentum = -1.0
    sig.delta_ratio = -0.20
    sig.equal_lows = [99.0]
    sig.swing_structures = [
        StructureBreak(
            tag="BOS",
            kind="swing",
            direction="bearish",
            level=100.0,
            pivot_index=10,
        )
    ]

    df = _dummy_df(50, 96.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [100.0, 100.2, 98.9, 99.1]

    primary = ScenarioMatrixEngine.classify(sig, df)
    reaction = ScenarioMatrixEngine.classify_reaction(sig, df)

    assert primary.scenario_id == "S1_BEAR_BREAKDOWN_WATCH"
    assert reaction is not None
    assert reaction.scenario_id == "S8_SUPPORT_FAILURE_PRESSURE"
    assert reaction.reaction_state == "SUPPORT_FAILURE_PRESSURE"
    assert reaction.entry_status == "PRESSURE_WARNING"
    assert reaction.actionable is False
    assert reaction.stop_loss is None
    assert reaction.take_profit_1 is None


def test_support_pressure_warns_before_closed_breakdown_without_authorizing_entry():
    sig = SMCSignal(symbol="NEAR/USDT", timeframe="15m", current_price=1.918)
    sig.equal_lows = [1.916]
    sig.delta_ratio = -0.36
    sig.momentum_direction = "accelerating_down"

    df = _dummy_df(50, 1.90)
    df.loc[df.index[-1], ["open", "high", "low", "close", "volume"]] = [
        1.931, 1.933, 1.917, 1.918, 2500.0
    ]

    result = ScenarioMatrixEngine.classify(sig, df)

    assert result.scenario_id == "S8_SUPPORT_FAILURE_PRESSURE"
    assert result.entry_status == "PRESSURE_WARNING"
    assert result.pressure_warning["side"] == "bearish"
    assert result.pressure_warning["score"] >= 70
    assert result.pressure_warning["execution_authorized"] is False
    assert result.actionable is False


def test_zone_cannot_explain_reaction_on_its_own_confirmation_candle():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=100.5)
    fresh_zone = Zone(
        kind="ob",
        direction="bullish",
        top=100.0,
        bottom=99.0,
        index=45,
        confirmed_index=49,
    )
    sig.order_blocks = [fresh_zone]
    sig.order_block = fresh_zone
    sig.delta_ratio = 0.25

    df = _dummy_df(50, 96.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [
        99.2, 100.8, 98.9, 100.5
    ]

    result = ScenarioMatrixEngine.classify(sig, df)

    assert result.zone_id != fresh_zone.zone_id
    assert result.archetype != "support_reaction"


def test_equal_level_cannot_explain_reaction_before_it_is_confirmed():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=100.5)
    sig.equal_lows = [99.0]
    sig.equal_low_levels = [{
        "price": 99.0,
        "origin_index": 47,
        "confirmed_index": 49,
        "level_id": "equal:low:40:47:49:99.00000000",
    }]
    sig.delta_ratio = 0.25

    df = _dummy_df(50, 96.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [
        99.2, 100.8, 98.9, 100.5
    ]

    result = ScenarioMatrixEngine.classify(sig, df)

    assert result.zone_id != sig.equal_low_levels[0]["level_id"]
    assert result.archetype != "support_reaction"


def test_swing_cannot_explain_reaction_on_its_confirmation_candle():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=100.5)
    df = _dummy_df(50, 96.0)
    swing = SwingPoint(
        index=45,
        price=99.0,
        kind="low",
        timestamp=df.index[45],
        confirmed_index=49,
        confirmed_timestamp=df.index[49],
    )
    sig.swing_lows = [swing]
    sig.delta_ratio = 0.25
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [
        99.2, 100.8, 98.9, 100.5
    ]

    result = ScenarioMatrixEngine.classify(sig, df)

    assert result.zone_id != swing.level_id
    assert result.archetype != "support_reaction"


def test_strong_weak_level_cannot_explain_reaction_on_confirmation_candle():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=100.5)
    sig.strong_weak_low = {
        "price": 99.0,
        "label": "Strong Low",
        "origin_index": 45,
        "confirmed_index": 49,
        "level_id": "strong_weak:low:45:49:99.00000000",
    }
    sig.delta_ratio = 0.25
    df = _dummy_df(50, 96.0)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [
        99.2, 100.8, 98.9, 100.5
    ]

    result = ScenarioMatrixEngine.classify(sig, df)

    assert result.zone_id != sig.strong_weak_low["level_id"]
    assert result.archetype != "support_reaction"


def test_oldest_unresolved_zone_touch_is_latched_over_new_nearby_zone():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=103.0)
    old_zone = Zone(
        kind="ob", direction="bullish", top=99.0, bottom=98.0,
        index=10, confirmed_index=20,
    )
    new_zone = Zone(
        kind="ob", direction="bullish", top=102.0, bottom=101.5,
        index=45, confirmed_index=48,
    )
    sig.order_blocks = [old_zone, new_zone]
    sig.delta_ratio = 0.25
    sig.equal_highs = [110.0]

    df = _dummy_df(50, 96.0)
    df.loc[df.index[-3], ["open", "high", "low", "close"]] = [
        98.5, 99.2, 97.9, 98.8
    ]
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [
        98.8, 101.9, 98.7, 101.8
    ]
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [
        101.8, 103.2, 101.7, 103.0
    ]

    result = ScenarioMatrixEngine.classify(sig, df)

    assert result.archetype == "support_reaction"
    assert result.zone_id == old_zone.zone_id
    assert result.reaction_evidence["reaction_age_bars"] == 2


def test_signal_serializes_independent_reaction_payload():
    sig = SMCSignal(symbol="ADA/USDT", timeframe="15m")
    sig.scenario = {"scenario_id": "S1_BEAR_BREAKDOWN_WATCH"}
    sig.reaction = {
        "scenario_id": "S8_SUPPORT_TOUCH_WAIT",
        "reaction_state": "TOUCH_WAIT",
        "actionable": False,
    }

    payload = sig.to_dict()

    assert payload["scenario"]["scenario_id"] == "S1_BEAR_BREAKDOWN_WATCH"
    assert payload["reaction"]["reaction_state"] == "TOUCH_WAIT"


def test_fmt_forex_vs_crypto():
    from app.engines.scenario_engine import _fmt, _is_forex
    assert _is_forex("EURUSD") is True
    assert _is_forex("EUR/USD") is True
    assert _is_forex("XAUUSD") is True
    assert _is_forex("BTC/USDT") is False
    assert _is_forex("ETH/USDT") is False

    # BTC price should have comma and 2 decimals
    formatted_btc = _fmt(105432.50, "BTC/USDT")
    assert formatted_btc == "$105,432.50"

    # EURUSD should have 4 decimals
    formatted_eur = _fmt(1.0850, "EUR/USD")
    assert formatted_eur == "$1.0850"


def test_strategy_engine_regime_policy_safe_defaults():
    from app.engines.strategy_engine import StrategyEngine
    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.scenario = {"scenario_id": "TEST_CONFIRMED", "actionable": True}
    sig.direction = "long"
    sig.confluence = 75
    sig.risk_reward = 2.0
    sig.entry = 100.0
    sig.stop_loss = 98.0
    sig.take_profit = 104.0
    sig.volume_quality = "exchange_aggressor"
    sig.tri_core_setup = {"actionable": True, "direction": "long", "grade": "A",
                          "smc_confirmed": True, "cvd_confirmed": True,
                          "flow_confirmed": True}
    sig.in_equilibrium = True
    sig.zone_position = "equilibrium"
    sig.order_block = Zone(kind="ob", direction="bullish", top=100.0, bottom=98.0)
    sig.market_regime = {
        "regime": "trending",
        "direction": "bullish",
        "ready": True,
        "policy": {
            "entry_allowed": True,
            # min_confluence and min_rr intentionally omitted to test safe fallbacks
        }
    }
    strat = StrategyEngine()
    res = strat.evaluate(sig)
    assert res.approved is True
    assert res.effective_policy["min_confluence"] == 65.0
    assert res.effective_policy["min_rr"] == 2.0


def test_strategy_engine_does_not_use_mid_range_as_entry_gate():
    from app.engines.strategy_engine import StrategyEngine

    sig = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    sig.direction = "long"
    sig.bias = "bullish"
    sig.confluence = 75
    sig.risk_reward = 2.0
    sig.entry = 100.0
    sig.stop_loss = 98.0
    sig.take_profit = 104.0
    sig.volume_quality = "exchange_aggressor"
    sig.tri_core_setup = {"actionable": True, "direction": "long", "grade": "A",
                          "smc_confirmed": True, "cvd_confirmed": True,
                          "flow_confirmed": True}
    sig.zone_position = "mid_range"
    sig.order_block = Zone(
        kind="ob", direction="bullish", top=100.0, bottom=98.0
    )
    sig.market_regime = {
        "regime": "trending",
        "direction": "bullish",
        "ready": True,
        "policy": {"entry_allowed": True},
    }

    result = StrategyEngine().evaluate(sig)

    assert result.approved is True
