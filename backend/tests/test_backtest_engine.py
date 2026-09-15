"""Execution-cost simulator, metrics and release-gate tests."""

from __future__ import annotations

from copy import deepcopy
from itertools import pairwise

import pandas as pd
import pytest

from app.engines.backtest_engine import (
    ExecutionAssumptions,
    ReleaseCriteria,
    calculate_backtest_metrics,
    evaluate_release_gate,
    run_rolling_walk_forward_backtest,
    run_walk_forward_backtest,
    simulate_execution,
    summarize_sweep_observations,
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


def test_gap_through_stop_executes_at_open_not_trigger_price():
    future = _bars([
        (100, 101, 99, 100, 10_000),
        (90, 92, 89, 91, 10_000),
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
    assert result["exit_price"] == pytest.approx(90.0)


def test_partial_fill_is_exposed_before_remaining_entry_can_fill():
    future = _bars([
        (100, 101, 99, 100, 10),
        (94, 100, 94, 95, 10),
        (100, 101, 99, 100, 10),
    ])
    result = simulate_execution(
        direction="long",
        order_type="limit",
        entry=100,
        stop_loss=95,
        take_profit=110,
        requested_quantity=10,
        future_bars=future,
        assumptions=ExecutionAssumptions(
            fee_bps=0,
            spread_bps=0,
            slippage_bps=0,
            latency_bars=0,
            entry_timeout_bars=3,
            max_volume_participation=0.1,
            max_fill_fraction_per_bar=1.0,
        ),
    )
    assert result["exit_reason"] == "stop_loss"
    assert result["filled_quantity"] == pytest.approx(1.0)
    assert result["exit_price"] == pytest.approx(94.0)


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
            "regime": "trending", "scenario_id": "S1_BULL_BREAKOUT", "confluence": 80,
        },
        {
            "status": "closed", "r_multiple": -1.0, "net_pnl": -100.0,
            "requested_quantity": 1.0, "filled_quantity": 0.5,
            "mfe_r": 0.4, "mae_r": 1.0, "realized_slippage_bps": 3.0,
            "regime": "ranging", "scenario_id": "S2_BULL_OB_RETEST", "confluence": 70,
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
    assert set(metrics["by_scenario"]) == {"S1_BULL_BREAKOUT", "S2_BULL_OB_RETEST"}
    assert metrics["by_scenario"]["S1_BULL_BREAKOUT"]["win_rate_pct"] == 100.0
    assert metrics["by_scenario"]["S2_BULL_OB_RETEST"]["win_rate_pct"] == 0.0


def test_release_gate_never_marks_result_production_eligible():
    metrics = {
        "evaluation_mode": "anchored_out_of_sample_replay",
        "completed_trades": 120,
        "expectancy_r": 0.2,
        "profit_factor": 1.5,
        "max_drawdown_pct": 6.0,
        "fill_rate": 0.9,
        "regimes_tested": 3,
        "by_scenario": {
            "S1_BULL_BREAKOUT": {"trades": 60},
            "S2_BULL_OB_RETEST": {"trades": 60},
        },
        "data_readiness": {"coverage": 1.0, "history_days": 180.0},
        "tri_core_policy": {"calibration_status": "walk_forward_validated"},
        "oos_fold_count": 3,
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


def test_backtest_rejects_non_execution_timeframe():
    config = deepcopy(DEFAULT_STRATEGY)
    config["timeframe_profiles"] = deepcopy(DEFAULT_TIMEFRAME_PROFILES)
    frame = _bars([(100, 101, 99, 100, 1000)] * 120)

    with pytest.raises(ValueError, match="configured timeframe 15m"):
        run_walk_forward_backtest(
            market_data=frame,
            symbol="BTC/USDT",
            timeframe="1h",
            config=config,
            assumptions=ExecutionAssumptions(),
            warmup_bars=60,
        )


def test_sweep_distribution_is_diagnostic_and_does_not_select_thresholds():
    result = summarize_sweep_observations(
        [
            {"reclaim_duration_bars": 1, "post_reclaim_extension_atr": 0.2},
            {"reclaim_duration_bars": 2, "post_reclaim_extension_atr": 0.5},
            {"reclaim_duration_bars": 4, "post_reclaim_extension_atr": 1.1},
            {"reclaim_duration_bars": 8, "post_reclaim_extension_atr": 1.8},
        ],
        split_scope="outer_oos_observation_only",
    )

    assert result["reclaim_duration_bars"]["p50"] == 3.0
    assert result["post_reclaim_extension_atr"]["p75"] == pytest.approx(1.275)
    assert result["candidate_selection"] == "not_selected"
    assert "training/inner-validation" in result["warning"]


def test_rolling_walk_forward_uses_non_overlapping_chronological_oos_folds(monkeypatch):
    import app.engines.backtest_engine as module

    calls: list[tuple[int, float]] = []

    def fake_anchored(**kwargs):
        fold_frame = kwargs["market_data"]
        fraction = kwargs["oos_fraction"]
        start = int(len(fold_frame) * fraction)
        calls.append((len(fold_frame), fraction))
        return {
            "trades": [],
            "rejection_diagnostics": [],
            "calibration_distribution": {"observation_count": 0},
            "metrics": {"completed_trades": 0},
            "oos_start_index": start,
        }

    monkeypatch.setattr(module, "run_walk_forward_backtest", fake_anchored)
    frame = _bars([(100, 101, 99, 100, 1000)] * 200)
    result = run_rolling_walk_forward_backtest(
        market_data=frame,
        symbol="BTC/USDT",
        timeframe="15m",
        config={"tri_core_policy": {"calibration_status": "draft_unvalidated"}},
        assumptions=ExecutionAssumptions(),
        folds=3,
        initial_train_fraction=0.50,
        warmup_bars=20,
    )

    fold_ranges = [
        (fold["oos_start_index"], fold["oos_end_index"])
        for fold in result["folds"]
    ]
    assert fold_ranges == [(100, 132), (133, 165), (166, 199)]
    assert all(left[1] < right[0] for left, right in pairwise(fold_ranges))
    assert calls == [(133, 100 / 133), (166, 133 / 166), (200, 166 / 200)]
    assert result["metrics"]["oos_fold_count"] == 3
    assert result["evaluation_mode"] == "rolling_walk_forward_oos"
