from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
import yaml

from app.engines import regime_engine as regime_module
from app.engines.regime_engine import (
    DEFAULT_REGIME_POLICY,
    MarketRegimeEngine,
    save_regime_policy_config,
    validate_regime_policy_config,
)
from app.engines.risk_engine import RiskEngine
from app.engines.smc_engine import SMCSignal, Zone
from app.engines.strategy_engine import StrategyEngine


def _frame(close: np.ndarray, spread: np.ndarray | float = 0.6) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    spread_values = np.broadcast_to(np.asarray(spread, dtype=float), close.shape)
    previous = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "open": previous,
            "high": np.maximum(previous, close) + spread_values,
            "low": np.minimum(previous, close) - spread_values,
            "close": close,
            "volume": np.full(close.shape, 1000.0),
        }
    )


def _signal(**updates) -> SMCSignal:
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="1h",
        direction="long",
        bias="bullish",
        htf_bias="bullish",
        current_price=100.0,
        in_discount=True,
        confluence=90,
        order_block=Zone("ob", "bullish", top=100.0, bottom=98.0),
        entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        risk_reward=3.0,
        volume_data_valid=True,
        volume_delta=250.0,
    )
    for key, value in updates.items():
        setattr(signal, key, value)
    return signal


def _nonvolatile_config() -> dict:
    config = deepcopy(DEFAULT_REGIME_POLICY)
    config["classification"]["volatile_atr_ratio"] = 10.0
    config["classification"]["volatile_percentile"] = 100.0
    return config


def test_classifier_detects_directional_trend_without_adding_confluence():
    signal = _signal(confluence=73)
    result = MarketRegimeEngine().classify(
        _frame(np.linspace(100.0, 160.0, 140)), signal, _nonvolatile_config()
    )

    assert result["regime"] == "trending"
    assert result["direction"] == "bullish"
    assert result["ready"] is True
    assert signal.confluence == 73
    assert "score" not in result


def test_classifier_detects_range_and_compression():
    close = 100.0 + np.sin(np.linspace(0, 12 * np.pi, 140))
    ranging_signal = _signal(bias="neutral", htf_bias="neutral")
    ranging = MarketRegimeEngine().classify(
        _frame(close), ranging_signal, _nonvolatile_config()
    )
    compression_signal = _signal(
        bias="neutral",
        htf_bias="neutral",
        squeeze_data_valid=True,
        squeeze_status="squeeze_on",
    )
    compression = MarketRegimeEngine().classify(
        _frame(close), compression_signal, _nonvolatile_config()
    )

    assert ranging["regime"] == "ranging"
    assert compression["regime"] == "compression"
    assert compression["policy"]["entry_allowed"] is False


def test_classifier_detects_late_volatility_expansion():
    stable = np.linspace(100.0, 102.0, 110)
    expanded = 102.0 + np.array(
        [4, -3, 5, -5, 7, -6, 8, -7, 9, -8, 10, -9, 11, -10, 12, -11, 13, -12, 14, -13],
        dtype=float,
    )
    spread = np.r_[np.full(stable.shape, 0.3), np.full(expanded.shape, 3.0)]
    result = MarketRegimeEngine().classify(
        _frame(np.r_[stable, expanded], spread), _signal()
    )

    assert result["regime"] == "volatile"
    assert result["policy"]["risk_multiplier"] == pytest.approx(0.40)


def test_insufficient_data_fails_closed():
    result = MarketRegimeEngine().classify(
        _frame(np.linspace(100.0, 110.0, 20)), _signal()
    )

    assert result["regime"] == "unknown"
    assert result["ready"] is False
    assert result["policy"]["entry_allowed"] is False


def test_strategy_blocks_compression_and_requires_sweep_in_range():
    compression_signal = _signal(
        market_regime={
            "regime": "compression",
            "direction": "bullish",
            "ready": True,
            "policy": deepcopy(DEFAULT_REGIME_POLICY["policies"]["compression"]),
        }
    )
    range_signal = _signal(
        liquidity_swept=False,
        market_regime={
            "regime": "ranging",
            "direction": "neutral",
            "ready": True,
            "policy": deepcopy(DEFAULT_REGIME_POLICY["policies"]["ranging"]),
        },
    )

    compression_result = StrategyEngine().evaluate(compression_signal)
    range_result = StrategyEngine().evaluate(range_signal)
    range_signal.liquidity_swept = True
    range_with_sweep = StrategyEngine().evaluate(range_signal)

    assert compression_result.approved is False
    assert any("blocked" in reason for reason in compression_result.rejection_reasons)
    assert range_result.approved is False
    assert range_result.direction == "wait"
    assert range_result.setup_direction == "long"
    assert any("Liquidity sweep" in reason for reason in range_result.rejection_reasons)
    assert range_with_sweep.approved is True
    assert range_with_sweep.direction == "long"
    assert range_with_sweep.setup_direction == "long"
    assert range_with_sweep.effective_policy["min_rr"] == pytest.approx(2.5)


def test_strategy_keeps_short_setup_direction_when_gate_rejects():
    signal = _signal(
        direction="short",
        bias="bearish",
        htf_bias="bearish",
        in_discount=False,
        in_premium=True,
        order_block=Zone("ob", "bearish", top=102.0, bottom=100.0),
        entry=100.0,
        stop_loss=101.0,
        take_profit=97.0,
        risk_reward=3.0,
        confluence=72,
        liquidity_swept=False,
        market_regime={
            "regime": "ranging",
            "direction": "bearish",
            "ready": True,
            "policy": deepcopy(DEFAULT_REGIME_POLICY["policies"]["ranging"]),
        },
    )

    result = StrategyEngine().evaluate(signal)

    assert result.approved is False
    assert result.direction == "wait"
    assert result.setup_direction == "short"
    assert result.to_dict()["status"] == "wait"


def test_risk_budget_is_reduced_by_regime_multiplier():
    legacy = _signal()
    ranging = _signal(
        market_regime={
            "regime": "ranging",
            "ready": True,
            "policy": deepcopy(DEFAULT_REGIME_POLICY["policies"]["ranging"]),
        }
    )
    engine = RiskEngine()

    base_result = engine.evaluate(legacy, account_balance=10_000.0)
    range_result = engine.evaluate(ranging, account_balance=10_000.0)

    assert base_result.approved is True
    assert range_result.approved is True
    assert range_result.regime_risk_multiplier == pytest.approx(0.65)
    assert range_result.risk_amount == pytest.approx(base_result.risk_amount * 0.65, abs=0.02)


def test_regime_config_rejects_risk_increase_and_save_preserves_sections(
    tmp_path, monkeypatch
):
    unsafe = deepcopy(DEFAULT_REGIME_POLICY)
    unsafe["policies"]["trending"]["risk_multiplier"] = 1.1
    with pytest.raises(ValueError, match="risk_multiplier"):
        validate_regime_policy_config(unsafe)

    strategy_file = tmp_path / "strategy.yaml"
    strategy_file.write_text(
        yaml.safe_dump({"name": "Keep", "indicator_core": {"version": 1}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(regime_module, "STRATEGY_FILE", strategy_file)
    saved = save_regime_policy_config(DEFAULT_REGIME_POLICY)
    persisted = yaml.safe_load(strategy_file.read_text(encoding="utf-8"))

    assert saved["policies"]["volatile"]["risk_multiplier"] == pytest.approx(0.4)
    assert persisted["name"] == "Keep"
    assert persisted["indicator_core"] == {"version": 1}
    assert persisted["regime_policy"]["version"] == 1


def test_smc_analysis_serializes_market_regime():
    signal = _signal()
    result = MarketRegimeEngine().classify(
        _frame(np.linspace(100.0, 150.0, 140)), signal, _nonvolatile_config()
    )
    signal.market_regime = result

    serialized = signal.to_dict()
    assert serialized["market_regime"]["regime"] == "trending"
    assert serialized["market_regime"]["policy"]["risk_multiplier"] == 1.0
