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
from app.engines.smc_engine import SMCSignal, StructureBreak, Zone
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
    signal.swing_structures = [
        StructureBreak(
            tag="BOS",
            kind="swing",
            direction="bullish",
            level=99.0,
            pivot_index=1,
            break_index=2,
        )
    ]
    signal.risk_reward = 3.0

    signal.volume_data_valid = True
    signal.volume_quality = "exchange_aggressor"
    signal.delta_source = "exchange_aggressor"
    signal.volume_delta = 1000.0
    signal.delta_ratio = 0.4
    signal.delta_absorption = True
    signal.delta_absorption_type = "bullish_absorption"
    signal.cvd_divergence = "bullish"
    signal.cvd_divergence_evidence = {
        "price_excursion_atr": 0.8,
        "cvd_efficiency": 0.2,
    }
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

    assert decision["coverage"] == 57.1  # SMC 40 / core weight 70; SQZ is not coverage.
    assert decision["score"] == 57
    assert decision["ready"] is False
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
    assert any("SMC+CVD" in reason for reason in result.rejection_reasons)


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


def test_opposing_structure_event_never_adds_smc_points():
    aligned = _strong_long_signal()
    opposing = deepcopy(aligned)
    opposing.swing_structures = [
        StructureBreak(
            tag="CHoCH",
            kind="swing",
            direction="bearish",
            level=99.0,
            pivot_index=1,
            break_index=3,
        )
    ]

    aligned_layer = IndicatorDecisionCore().evaluate(aligned)["layers"][0]
    opposing_layer = IndicatorDecisionCore().evaluate(opposing)["layers"][0]

    assert aligned_layer["weighted_points"] == opposing_layer["weighted_points"] + 3
    assert any("opposes" in item for item in opposing_layer["evidence"])


def test_estimated_cvd_is_not_counted_as_execution_coverage():
    signal = _strong_long_signal()
    signal.volume_quality = "estimated"
    decision = IndicatorDecisionCore().evaluate(signal)
    volume = next(layer for layer in decision["layers"] if layer["id"] == "volume_delta")
    assert volume["available"] is False
    assert decision["coverage"] == 57.1
    assert decision["ready"] is False


def test_wait_signal_reports_both_directional_evidence_scores():
    signal = _strong_long_signal()
    signal.direction = "wait"
    decision = IndicatorDecisionCore().evaluate(signal)
    assert decision["score_role"] == "directional_evidence"
    assert decision["selected_direction"] == "evidence_only"
    assert decision["directional_scores"]["long"] > decision["directional_scores"]["short"]
    assert decision["squeeze_bonus"] == 0


def test_cvd_divergence_contributes_to_volume_layer():
    signal = _strong_long_signal()
    with_divergence = IndicatorDecisionCore().evaluate(signal)["layers"][1]
    signal.cvd_divergence = "none"
    signal.cvd_divergence_evidence = {}
    without_divergence = IndicatorDecisionCore().evaluate(signal)["layers"][1]
    assert with_divergence["weighted_points"] > without_divergence["weighted_points"]
