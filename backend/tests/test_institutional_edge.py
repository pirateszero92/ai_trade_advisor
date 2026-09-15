"""
Unit Tests for Institutional Edge & Market-Trap Defense Architecture (4 Dimensions)
Tests:
1. Derivatives and Sentiment Intelligence (OI Delta, Squeeze Risk, Crowding)
2. VPIN Order Flow Toxicity Strategy Gate
3. Inducement (IDM) & Trap Zone Recognition
4. HMM Dynamic Regime Integration in MarketRegimeEngine
"""

import numpy as np
import pandas as pd
import pytest

from app.engines.sentiment_derivatives_engine import (
    DerivativesSentimentResult,
    SentimentDerivativesEngine,
)
from app.engines.smc_engine import SMCEngine, SMCSignal, Zone
from app.engines.strategy_engine import StrategyEngine
from app.engines.regime_engine import MarketRegimeEngine


def _dummy_df(n: int = 50, base: float = 100.0) -> pd.DataFrame:
    np.random.seed(42)
    closes = base + np.cumsum(np.random.randn(n) * 0.3)
    highs = closes + np.random.uniform(0.1, 0.4, n)
    lows = closes - np.random.uniform(0.1, 0.4, n)
    opens = np.roll(closes, 1)
    opens[0] = base
    volumes = np.random.uniform(1000, 3000, n)
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
    )


def test_sentiment_derivatives_enrich_bias():
    res = DerivativesSentimentResult(symbol="BTC/USDT", status="ok")

    # Bullish Expansion: Price Up + OI Up
    res.oi_delta_pct_1h = 2.5
    SentimentDerivativesEngine._enrich_bias(res, price_change_pct_1h=1.2)
    assert res.sentiment_bias == "BULLISH_EXPANSION"

    # Short Squeeze Risk: Price Up + OI Down
    res.oi_delta_pct_1h = -2.1
    SentimentDerivativesEngine._enrich_bias(res, price_change_pct_1h=1.5)
    assert res.sentiment_bias == "SHORT_SQUEEZE_RISK"

    # Long Liquidation Flush: Price Down + OI Down
    res.oi_delta_pct_1h = -3.0
    SentimentDerivativesEngine._enrich_bias(res, price_change_pct_1h=-1.8)
    assert res.sentiment_bias == "LONG_LIQUIDATION_FLUSH"

    # Crowded Long: Extreme Funding Rate
    res.funding_rate = 0.0004
    res.oi_delta_pct_1h = 0.1
    SentimentDerivativesEngine._enrich_bias(res, price_change_pct_1h=0.1)
    assert res.sentiment_bias == "CROWDED_LONG"
    assert "High Long Crowding" in str(res.crowd_risk_warning)


def _enable_tri_core(sig):
    sig.entry = 100.0
    sig.stop_loss = 98.0
    sig.take_profit = 104.0
    sig.risk_reward = 2.0
    sig.volume_quality = "exchange_aggressor"
    sig.tri_core_setup = {"actionable": True, "direction": sig.direction, "grade": "A",
                          "smc_confirmed": True, "cvd_confirmed": True,
                          "flow_confirmed": True}
    return sig


def test_vpin_order_flow_is_warning_not_gate():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="1h")
    sig.direction = "long"
    sig.bias = "bullish"
    sig.confluence = 80
    sig.risk_reward = 2.5
    sig.order_block = Zone(kind="ob", direction="bullish", top=100.0, bottom=98.0)
    sig.market_regime = {"regime": "trending", "direction": "bullish", "policy": {"entry_allowed": True}}
    sig.order_flow = {
        "status": "ok",
        "vpin": 0.46,
        "toxic_flow_detected": True,
        "percentile_toxicity": 92.0,
    }

    result = StrategyEngine().evaluate(_enable_tri_core(sig))
    assert result.approved is True
    assert "VPIN toxicity" in result.warnings


def test_inducement_is_warning_not_gate():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="1h")
    sig.direction = "long"
    sig.bias = "bullish"
    sig.confluence = 80
    sig.risk_reward = 2.5
    sig.order_block = Zone(kind="ob", direction="bullish", top=100.0, bottom=98.0)
    sig.market_regime = {"regime": "trending", "direction": "bullish", "policy": {"entry_allowed": True}}
    sig.active_zone_type = "inducement_trap"
    sig.inducement_swept = False

    result = StrategyEngine().evaluate(_enable_tri_core(sig))
    assert result.approved is True
    assert "Inducement nearby" in result.warnings


def test_inducement_swept_approves_extreme_ob():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="1h")
    sig.scenario = {"scenario_id": "TEST_CONFIRMED", "actionable": True}
    sig.direction = "long"
    sig.bias = "bullish"
    sig.confluence = 80
    sig.risk_reward = 2.5
    sig.order_block = Zone(kind="ob", direction="bullish", top=100.0, bottom=98.0)
    sig.market_regime = {"regime": "trending", "direction": "bullish", "policy": {"entry_allowed": True}}
    sig.in_discount = True
    sig.zone_position = "discount"
    sig.active_zone_type = "extreme_ob"
    sig.inducement_swept = True

    result = StrategyEngine().evaluate(_enable_tri_core(sig))
    assert result.approved is True


def test_crowded_long_sentiment_is_warning_not_gate():
    sig = SMCSignal(symbol="BTC/USDT", timeframe="1h")
    sig.direction = "long"
    sig.bias = "bullish"
    sig.confluence = 80
    sig.risk_reward = 2.5
    sig.order_block = Zone(kind="ob", direction="bullish", top=100.0, bottom=98.0)
    sig.market_regime = {"regime": "trending", "direction": "bullish", "policy": {"entry_allowed": True}}
    sig.derivatives_sentiment = {
        "sentiment_bias": "CROWDED_LONG",
        "funding_rate": 0.0004,
        "long_short_ratio": 2.5,
    }

    result = StrategyEngine().evaluate(_enable_tri_core(sig))
    assert result.approved is True
    assert "Crowded derivatives" in result.warnings


def test_hmm_dynamic_regime_included_in_regime_engine():
    df = _dummy_df(70, base=100.0)
    engine = MarketRegimeEngine()
    result = engine.classify(df, None)

    assert "metrics" in result
    assert "hmm_dominant_state" in result["metrics"]
    assert "hmm_probabilities" in result["metrics"]
    assert any("HMM dynamic regime" in ev for ev in result["evidence"])


def test_tri_core_edge_overrides_observation_only_scenario():
    """Tri-Core: SMC Sweep + OB + R:R >= 2.0 overrides observation-only scenario status."""
    sig = SMCSignal(symbol="BTC/USDT", timeframe="1h")
    # Legacy scenario engine labeled this S4 observation-only
    sig.scenario = {"scenario_id": "S4_BEAR_BREAKER_WATCH", "actionable": False}
    sig.direction = "long"
    sig.bias = "bullish"
    sig.confluence = 70
    sig.risk_reward = 2.4
    sig.liquidity_swept = True
    sig.order_block = Zone(kind="ob", direction="bullish", top=100.0, bottom=98.0)
    sig.market_regime = {"regime": "trending", "direction": "bullish", "policy": {"entry_allowed": True}}
    sig.in_discount = True
    sig.zone_position = "discount"

    result = StrategyEngine().evaluate(_enable_tri_core(sig))
    assert result.approved is True
    assert any("Causal SMC setup" in check for check in result.passed_checks)


def test_liquidity_swept_overrides_inducement_trap():
    """When liquidity was swept, adjacent IDM does not block the origin OB."""
    sig = SMCSignal(symbol="BTC/USDT", timeframe="1h")
    sig.scenario = {"scenario_id": "S1_BULL_PULLBACK", "actionable": True}
    sig.direction = "long"
    sig.bias = "bullish"
    sig.confluence = 70
    sig.risk_reward = 2.2
    sig.order_block = Zone(kind="ob", direction="bullish", top=100.0, bottom=98.0)
    sig.market_regime = {"regime": "trending", "direction": "bullish", "policy": {"entry_allowed": True}}
    sig.in_discount = True
    sig.zone_position = "discount"
    sig.active_zone_type = "inducement_trap"
    sig.liquidity_swept = True

    result = StrategyEngine().evaluate(_enable_tri_core(sig))
    assert result.approved is True
    assert "Inducement nearby" in result.warnings
