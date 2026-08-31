import numpy as np
import pandas as pd

from app.engines.scenario_engine import ScenarioMatrixEngine
from app.engines.smc_engine import SMCSignal, StructureBreak, Zone


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
    assert reaction.scenario_id == "S8_SUPPORT_TOUCH_WAIT"
    assert reaction.reaction_state == "TOUCH_WAIT"
    assert reaction.actionable is False
    assert reaction.stop_loss is None
    assert reaction.take_profit_1 is None


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
    sig.direction = "long"
    sig.confluence = 75
    sig.risk_reward = 2.0
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
