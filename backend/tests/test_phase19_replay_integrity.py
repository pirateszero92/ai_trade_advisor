from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from app.engines.backtest_engine import (
    ExecutionAssumptions,
    build_ai_review_cohorts,
    build_replay_manifest,
    calculate_backtest_metrics,
    run_walk_forward_backtest,
)
from app.engines.smc_engine import SMCSignal
from app.engines.strategy_engine import DEFAULT_STRATEGY, StrategyResult
from app.engines.timeframe_profiles import DEFAULT_TIMEFRAME_PROFILES
from app.services.evidence import fingerprint


def _frame(rows=70):
    return pd.DataFrame(
        {
            "open": [100.0] * rows,
            "high": [101.0] * rows,
            "low": [99.0] * rows,
            "close": [100.0] * rows,
            "volume": [1000.0] * rows,
        },
        index=pd.date_range("2026-01-01", periods=rows, freq="15min", tz="UTC"),
    )


def _config():
    config = deepcopy(DEFAULT_STRATEGY)
    config["timeframe_profiles"] = deepcopy(DEFAULT_TIMEFRAME_PROFILES)
    config["tri_core_policy"] = {
        "calibration_status": "draft_unvalidated",
        "marker": 1,
    }
    return config
def test_phase19_replay_manifest_hashes_frozen_inputs():
    frame = _frame()
    config = _config()
    assumptions = ExecutionAssumptions(fee_bps=8, spread_bps=4, slippage_bps=2)

    manifest = build_replay_manifest(
        frame=frame,
        symbol="BTC/USDT",
        timeframe="15m",
        config=config,
        assumptions=assumptions,
    )

    assert manifest["config_hash"] == fingerprint(config)
    assert manifest["execution_assumptions_hash"] == fingerprint(assumptions.to_dict())
    assert manifest["closed_candle_only"] is True
    assert manifest["future_leakage_allowed"] is False
    assert manifest["llm_called_during_backtest"] is False
    assert manifest["ai_review_mode"] == "external_annotation_only"


def test_phase19_ai_cohorts_use_recorded_reviews_only():
    attempts = [
        {"decision_id": "a", "status": "closed", "r_multiple": 1.0, "net_pnl": 10.0,
         "requested_quantity": 1.0, "filled_quantity": 1.0, "mfe_r": 1.2, "mae_r": 0.2,
         "realized_slippage_bps": 1.0, "regime": "trending", "scenario_id": "S1", "confluence": 80},
        {"decision_id": "b", "status": "closed", "r_multiple": -1.0, "net_pnl": -10.0,
         "requested_quantity": 1.0, "filled_quantity": 1.0, "mfe_r": 0.2, "mae_r": 1.0,
         "realized_slippage_bps": 1.0, "regime": "ranging", "scenario_id": "S2", "confluence": 70},
    ]
    cohorts = build_ai_review_cohorts(
        attempts,
        ai_reviews={
            "a": {"verdict": "COHERENT"},
            "b": {"verdict": "CONFLICT"},
        },
    )

    assert cohorts["llm_called_during_backtest"] is False
    assert cohorts["counts"]["all_deterministic"] == 2
    assert cohorts["counts"]["ai_coherent"] == 1
    assert cohorts["counts"]["ai_conflict"] == 1
    assert cohorts["counts"]["ai_unavailable"] == 0
def test_phase19_replay_rejects_non_chronological_or_duplicate_index():
    frame = _frame()
    config = _config()

    duplicate = frame.copy()
    duplicate.index = list(frame.index[:-1]) + [frame.index[-2]]
    with pytest.raises(ValueError, match="unique and chronological"):
        run_walk_forward_backtest(
            market_data=duplicate,
            symbol="BTC/USDT",
            timeframe="15m",
            config=config,
            assumptions=ExecutionAssumptions(),
            warmup_bars=60,
            start_index=60,
        )

    reversed_frame = frame.iloc[::-1]
    with pytest.raises(ValueError, match="unique and chronological"):
        run_walk_forward_backtest(
            market_data=reversed_frame,
            symbol="BTC/USDT",
            timeframe="15m",
            config=config,
            assumptions=ExecutionAssumptions(),
            warmup_bars=60,
            start_index=60,
        )
def test_phase19_decision_history_never_contains_execution_future(monkeypatch):
    import app.engines.backtest_engine as module

    frame = _frame(66)
    config = _config()
    original_config = config
    observed_configs = []

    def fake_analyze(*, frame, symbol, entry_mode, config_snapshot):
        observed_configs.append(deepcopy(config_snapshot))
        original_config["tri_core_policy"]["marker"] = 999
        signal = SMCSignal(
            symbol=symbol,
            timeframe="15m",
            direction="long",
            entry=100.0,
            stop_loss=99.0,
            take_profit=103.0,
            risk_reward=3.0,
            entry_type="market",
            market_regime={"regime": "trending"},
            tri_core_setup={
                "actionable": True,
                "direction": "long",
                "event_id": f"event-{len(frame)}",
                "setup_type": "breaker_retest",
                "strategy_family": "swing",
            },
        )
        return signal, StrategyResult(
            approved=True,
            direction="long",
            setup_direction="long",
        ), "15m"

    def fake_risk(*args, **kwargs):
        return SimpleNamespace(approved=True, position_size=1.0, rejection_reason=None)
    def fake_execution(*, future_bars, requested_quantity, **kwargs):
        timestamp = pd.Timestamp(future_bars.index[0]).isoformat()
        return {
            "status": "closed",
            "requested_quantity": requested_quantity,
            "filled_quantity": requested_quantity,
            "fill_rate": 1.0,
            "fills": [{"timestamp": timestamp}],
            "exit_offset": 0,
            "exit_reason": "take_profit",
            "r_multiple": 1.0,
            "net_pnl": 1.0,
            "mfe_r": 1.0,
            "mae_r": 0.0,
            "realized_slippage_bps": 0.0,
        }

    monkeypatch.setattr(module, "analyze_execution_frame", fake_analyze)
    monkeypatch.setattr(module.RiskEngine, "evaluate", fake_risk)
    monkeypatch.setattr(module, "simulate_execution", fake_execution)

    result = run_walk_forward_backtest(
        market_data=frame,
        symbol="BTC/USDT",
        timeframe="15m",
        config=config,
        assumptions=ExecutionAssumptions(latency_bars=0),
        warmup_bars=60,
        start_index=60,
        stride_bars=1,
        max_trades=2,
    )

    assert result["trades"]
    for trade in result["trades"]:
        decision = pd.Timestamp(trade["decision_time"])
        history_last = pd.Timestamp(trade["decision_history_last_timestamp"])
        future_first = pd.Timestamp(trade["future_first_timestamp"])
        assert history_last == decision
        assert future_first > decision
        assert trade["config_hash"] == result["replay_manifest"]["config_hash"]
        assert trade["setup_type"] == "breaker_retest"
        assert trade["strategy_family"] == "swing"

    assert observed_configs
    assert all(item["tri_core_policy"]["marker"] == 1 for item in observed_configs)


def test_phase19_htf_resample_excludes_open_bar_and_uses_closed_cvd_only():
    from app.engines.backtest_engine import _resample_closed_history

    index = pd.date_range("2026-01-01T00:00:00Z", periods=16, freq="15min")
    history = pd.DataFrame(
        {
            "open": [100.0] * 16,
            "high": [101.0] * 16,
            "low": [99.0] * 16,
            "close": [100.0] * 16,
            "volume": [10.0] * 16,
            "volume_delta": [1.0] * 16,
            "cvd": [float(i + 1) for i in range(16)],
        },
        index=index,
    )

    not_closed = _resample_closed_history(
        history.iloc[:15],
        target_timeframe="4h",
        trigger_timeframe="15min",
    )
    closed = _resample_closed_history(
        history,
        target_timeframe="4h",
        trigger_timeframe="15min",
    )

    assert not_closed.empty
    assert len(closed) == 1
    assert closed.index[0] == pd.Timestamp("2026-01-01T04:00:00Z")
    assert closed.iloc[0]["volume_delta"] == pytest.approx(16.0)
    assert closed.iloc[0]["cvd"] == pytest.approx(16.0)


def test_hrd05_backtest_metrics_are_attributed_by_strategy_family():
    attempts = [
        {
            "status": "closed", "r_multiple": 2.0, "net_pnl": 20.0,
            "requested_quantity": 1.0, "filled_quantity": 1.0,
            "mfe_r": 2.2, "mae_r": 0.3, "realized_slippage_bps": 1.0,
            "regime": "ranging", "scenario_id": "RANGE",
            "strategy_family": "ping_pong", "setup_type": "range_boundary_ping_pong",
            "confluence": 70,
        },
        {
            "status": "closed", "r_multiple": -1.0, "net_pnl": -10.0,
            "requested_quantity": 1.0, "filled_quantity": 1.0,
            "mfe_r": 0.4, "mae_r": 1.0, "realized_slippage_bps": 1.0,
            "regime": "trending", "scenario_id": "TREND",
            "strategy_family": "swing", "setup_type": "breaker_retest",
            "confluence": 80,
        },
    ]

    metrics = calculate_backtest_metrics(
        attempts, initial_capital=10_000.0, evaluation_mode="test"
    )

    assert metrics["by_strategy_family"]["ping_pong"]["trades"] == 1
    assert metrics["by_strategy_family"]["ping_pong"]["expectancy_r"] == 2.0
    assert metrics["by_strategy_family"]["swing"]["trades"] == 1
    assert metrics["by_strategy_family"]["swing"]["expectancy_r"] == -1.0
