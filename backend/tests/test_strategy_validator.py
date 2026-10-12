from copy import deepcopy

from app.engines.smc_engine import SMCSignal
from app.engines.strategy_engine import DEFAULT_STRATEGY, StrategyEngine


def _candidate(direction: str = "long") -> SMCSignal:
    signal = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    signal.direction = direction
    signal.entry = 100.0
    signal.stop_loss = 98.0 if direction == "long" else 102.0
    signal.take_profit = 104.0 if direction == "long" else 96.0
    signal.risk_reward = 0.5
    signal.volume_quality = "unavailable"
    signal.tri_core_setup = {
        "actionable": True,
        "direction": direction,
        "grade": "A",
    }
    return signal


def test_strategy_validator_does_not_recreate_tri_core_quality_gates():
    signal = _candidate("long")
    config = deepcopy(DEFAULT_STRATEGY)
    config["filters"]["min_rr"] = 99.0
    config["filters"]["allowed_directions"] = ["short"]

    result = StrategyEngine(strategy_config=config).evaluate(signal)

    assert result.approved is True
    assert result.direction == "long"
    assert result.setup_direction == "long"


def test_strategy_validator_rejects_direction_inconsistency():
    signal = _candidate("long")
    signal.direction = "short"

    result = StrategyEngine().evaluate(signal)

    assert result.approved is False
    assert result.direction == "wait"
    assert any("conflicts with Tri-Core" in reason for reason in result.rejection_reasons)


def test_strategy_validator_rejects_invalid_geometry():
    signal = _candidate("long")
    signal.stop_loss = 101.0

    result = StrategyEngine().evaluate(signal)

    assert result.approved is False
    assert any("Invalid LONG geometry" in reason for reason in result.rejection_reasons)
