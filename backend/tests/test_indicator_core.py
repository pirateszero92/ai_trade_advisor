from copy import deepcopy

import pandas as pd
import pytest
import yaml

from app.engines import indicator_core as indicator_core_module
from app.engines.indicator_core import (
    DEFAULT_INDICATOR_CORE,
    IndicatorDecisionCore,
    save_indicator_core_config,
    validate_indicator_core_config,
)
from app.engines.indicators import AdvancedIndicatorsEngine
from app.engines.smc_engine import SMCSignal, Zone
from app.engines.strategy_engine import DEFAULT_STRATEGY, StrategyEngine


def _strong_long_signal() -> SMCSignal:
    signal = SMCSignal(symbol="BTC/USDT", timeframe="1h")
    signal.current_price = 100.0
    signal.direction = "long"
    signal.bias = "bullish"
    signal.htf_bias = "bullish"
    signal.order_block = Zone("ob", "bullish", top=100.0, bottom=98.0)
    signal.fvg = Zone("fvg", "bullish", top=101.0, bottom=99.0)
    signal.liquidity_swept = True
    signal.sweep_direction = "low"
    signal.in_discount = True
    signal.bos = True
    signal.risk_reward = 3.0

    signal.volume_data_valid = True
    signal.volume_delta = 1000.0
    signal.delta_ratio = 0.4
    signal.delta_absorption = True
    signal.delta_absorption_type = "bullish_absorption"
    signal.volume_spike = True

    signal.squeeze_data_valid = True
    signal.squeeze_status = "squeeze_fire"
    signal.squeeze_momentum = 2.0
    signal.momentum_direction = "accelerating_up"
    return signal


def test_three_layers_produce_explainable_full_score():
    decision = IndicatorDecisionCore().evaluate(
        _strong_long_signal(), DEFAULT_INDICATOR_CORE
    )

    assert decision["score"] == 100
    assert decision["coverage"] == 100.0
    assert decision["ready"] is True
    assert [layer["id"] for layer in decision["layers"]] == [
        "smc_structure",
        "volume_delta",
        "squeeze_momentum",
    ]
    assert all(layer["evidence"] for layer in decision["layers"])


def test_unavailable_optional_layer_reduces_coverage_and_score():
    signal = _strong_long_signal()
    signal.volume_data_valid = False

    decision = IndicatorDecisionCore().evaluate(signal, DEFAULT_INDICATOR_CORE)

    assert decision["coverage"] == 70.0
    assert decision["score"] == 70
    assert decision["ready"] is True
    volume = next(layer for layer in decision["layers"] if layer["id"] == "volume_delta")
    assert volume["status"] == "unavailable"


def test_required_unavailable_layer_blocks_strategy_entry():
    signal = _strong_long_signal()
    signal.volume_data_valid = False
    config = deepcopy(DEFAULT_INDICATOR_CORE)
    config["indicators"]["volume_delta"]["required"] = True
    signal.indicator_decision = IndicatorDecisionCore().evaluate(signal, config)
    signal.confluence = signal.indicator_decision["score"]

    strategy = StrategyEngine()
    strategy._strategy = deepcopy(DEFAULT_STRATEGY)
    strategy._strategy["filters"].update(
        {
            "require_indicator_readiness": True,
            "require_ob": False,
            "min_confluence": 0,
        }
    )

    result = strategy.evaluate(signal)

    assert result.approved is False
    assert any("Indicator data is not ready" in reason for reason in result.rejection_reasons)


def test_disabled_layer_is_excluded_from_normalization():
    config = deepcopy(DEFAULT_INDICATOR_CORE)
    config["indicators"]["volume_delta"].update(
        {"enabled": False, "required": False}
    )

    decision = IndicatorDecisionCore().evaluate(_strong_long_signal(), config)

    assert decision["score"] == 100
    assert decision["coverage"] == 100.0
    assert decision["enabled_count"] == 2
    assert decision["available_count"] == 2


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda cfg: cfg.update({"version": 2}), "version"),
        (
            lambda cfg: cfg["indicators"].update({"fourth_indicator": {}}),
            "Unregistered indicators",
        ),
        (
            lambda cfg: [
                layer.update({"enabled": False, "required": False})
                for layer in cfg["indicators"].values()
            ],
            "At least one",
        ),
        (
            lambda cfg: cfg["indicators"]["volume_delta"].update(
                {"enabled": False, "required": True}
            ),
            "cannot be required",
        ),
    ],
)
def test_invalid_registry_configuration_is_rejected(mutation, message):
    config = deepcopy(DEFAULT_INDICATOR_CORE)
    mutation(config)

    with pytest.raises(ValueError, match=message):
        validate_indicator_core_config(config)


def test_indicator_config_save_preserves_other_strategy_sections(tmp_path, monkeypatch):
    strategy_file = tmp_path / "strategy.yaml"
    strategy_file.write_text(
        yaml.safe_dump({"name": "Keep me", "filters": {"min_confluence": 65}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(indicator_core_module, "STRATEGY_FILE", strategy_file)
    config = deepcopy(DEFAULT_INDICATOR_CORE)
    config["minimum_data_coverage"] = 80

    saved = save_indicator_core_config(config)
    persisted = yaml.safe_load(strategy_file.read_text(encoding="utf-8"))

    assert saved["minimum_data_coverage"] == 80
    assert persisted["name"] == "Keep me"
    assert persisted["filters"] == {"min_confluence": 65}
    assert persisted["indicator_core"]["minimum_data_coverage"] == 80


def test_volume_delta_parameters_are_validated():
    rows = [
        {
            "open": float(index + 1),
            "high": float(index + 2),
            "low": float(index),
            "close": float(index + 1.5),
            "volume": 100.0,
        }
        for index in range(12)
    ]
    frame = pd.DataFrame(rows)

    result = AdvancedIndicatorsEngine.compute_volume_delta(
        frame,
        absorption_lookback=5,
        volume_spike_multiplier=2.0,
        pressure_threshold=0.5,
    )
    assert result.volume_spike is False

    with pytest.raises(ValueError, match="absorption_lookback"):
        AdvancedIndicatorsEngine.compute_volume_delta(
            frame, absorption_lookback=2
        )
