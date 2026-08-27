"""Execution-cost simulator, metrics and release-gate tests."""

from __future__ import annotations

from copy import deepcopy
import pandas as pd
import pytest

from app.engines.backtest_engine import (
    ExecutionAssumptions,
    ReleaseCriteria,
    calculate_backtest_metrics,
    evaluate_release_gate,
    run_walk_forward_backtest,
    simulate_execution,
)
from app.engines.strategy_engine import DEFAULT_STRATEGY
from app.engines.timeframe_profiles import DEFAULT_TIMEFRAME_PROFILES


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close", "volume"],
        index=pd.date_range("2026-01-01", periods=len(rows), freq="h", tz="UTC"),
    )


def test_limit_execution_models_partial_fill_fees_spread_and_slippage():
    future = _bars([
        (100, 101, 99, 100, 100),
        (100, 101, 99, 100, 100),
        (100, 101, 99, 100, 100),
        (109, 111, 108, 110, 100),
    ])
    assumptions = ExecutionAssumptions(
        fee_bps=10,
        spread_bps=4,
        slippage_bps=2,
        latency_bars=0,
        entry_timeout_bars=3,
        max_holding_bars=10,
        max_volume_participation=0.01,
        max_fill_fraction_per_bar=0.50,
    )
    result = simulate_execution(
        direction="long",
        order_type="limit",
        entry=100,
        stop_loss=95,
        take_profit=110,
        requested_quantity=10,
        future_bars=future,
        assumptions=assumptions,
    )
    assert result["status"] == "closed"
    assert result["exit_reason"] == "take_profit"
    assert result["filled_quantity"] == 3
    assert result["fill_rate"] == 0.3
    assert len([fill for fill in result["fills"] if fill["leg"] == "entry"]) == 3
    assert result["fees"] > 0
    assert result["realized_slippage_bps"] > 0


def test_same_bar_stop_and_target_uses_conservative_stop_ordering():
    future = _bars([
        (100, 101, 99, 100, 10_000),
        (100, 112, 94, 101, 10_000),
    ])
    result = simulate_execution(
        direction="long",
        order_type="market",
        entry=100,
        stop_loss=95,
        take_profit=110,
        requested_quantity=1,
        future_bars=future,
        assumptions=ExecutionAssumptions(
            fee_bps=0,
            spread_bps=0,
            slippage_bps=0,
            latency_bars=0,
            entry_timeout_bars=1,
        ),
    )
    assert result["exit_reason"] == "stop_loss"
    assert result["r_multiple"] < 0


def test_short_execution_opens_sell_and_closes_with_buy_to_cover_costs():
    future = _bars([
        (100, 101, 99, 100, 10_000),
        (95, 96, 89, 90, 10_000),
    ])
    result = simulate_execution(
        direction="short",
        order_type="market",
        entry=100,
        stop_loss=105,
        take_profit=90,
        requested_quantity=1,
        future_bars=future,
        assumptions=ExecutionAssumptions(
            fee_bps=10,
            spread_bps=10,
            slippage_bps=5,
            latency_bars=0,
            entry_timeout_bars=1,
            max_volume_participation=1,
            max_fill_fraction_per_bar=1,
        ),
    )

    entry_fill = result["fills"][0]
    exit_fill = result["fills"][-1]
    assert result["status"] == "closed"
    assert result["exit_reason"] == "take_profit"
    assert result["filled_quantity"] == 1
    assert entry_fill["leg"] == "entry"
    assert entry_fill["price"] < entry_fill["reference_price"]
    assert exit_fill["leg"] == "exit"
    assert exit_fill["price"] > exit_fill["reference_price"]
    assert result["net_pnl"] > 0
    assert result["r_multiple"] > 0


def test_metrics_include_expectancy_drawdown_regime_and_calibration():
    attempts = [
        {
            "status": "closed", "r_multiple": 2.0, "net_pnl": 200.0,
            "requested_quantity": 1.0, "filled_quantity": 1.0,
            "mfe_r": 2.2, "mae_r": 0.3, "realized_slippage_bps": 2.0,
            "regime": "trending", "confluence": 80,
        },
        {
            "status": "closed", "r_multiple": -1.0, "net_pnl": -100.0,
            "requested_quantity": 1.0, "filled_quantity": 0.5,
            "mfe_r": 0.4, "mae_r": 1.0, "realized_slippage_bps": 3.0,
            "regime": "ranging", "confluence": 70,
        },
    ]
    metrics = calculate_backtest_metrics(
        attempts,
        initial_capital=10_000,
        evaluation_mode="out_of_sample_walk_forward",
    )
    assert metrics["completed_trades"] == 2
    assert metrics["expectancy_r"] == 0.5
    assert metrics["profit_factor"] == 2.0
    assert metrics["fill_rate"] == 0.75
    assert metrics["regimes_tested"] == 2
    assert set(metrics["by_regime"]) == {"trending", "ranging"}


def test_release_gate_never_marks_result_production_eligible():
    metrics = {
        "evaluation_mode": "out_of_sample_walk_forward",
        "completed_trades": 40,
        "expectancy_r": 0.2,
        "profit_factor": 1.5,
        "max_drawdown_pct": 6.0,
        "fill_rate": 0.9,
        "regimes_tested": 3,
    }
    gate = evaluate_release_gate(metrics, ReleaseCriteria())
    assert gate["passed"] is True
    assert gate["human_approval_required"] is True
    assert gate["production_eligible"] is False


def test_release_gate_fails_closed_for_insufficient_sample():
    metrics = {
        "evaluation_mode": "out_of_sample_walk_forward",
        "completed_trades": 3,
        "expectancy_r": 1.0,
        "profit_factor": None,
        "max_drawdown_pct": 1.0,
        "fill_rate": 1.0,
        "regimes_tested": 1,
    }
    gate = evaluate_release_gate(metrics, ReleaseCriteria())
    assert gate["passed"] is False
    assert any("completed_trades" in reason for reason in gate["failure_reasons"])
    assert any("profit_factor" in reason for reason in gate["failure_reasons"])


def test_phase5_backtest_rejects_non_trigger_timeframe():
    config = deepcopy(DEFAULT_STRATEGY)
    config["timeframe_profiles"] = deepcopy(DEFAULT_TIMEFRAME_PROFILES)
    frame = _bars([(100, 101, 99, 100, 1000)] * 120)

    with pytest.raises(ValueError, match="trigger timeframe 15m"):
        run_walk_forward_backtest(
            market_data=frame,
            symbol="BTC/USDT",
            timeframe="1h",
            config=config,
            assumptions=ExecutionAssumptions(),
            warmup_bars=60,
        )
