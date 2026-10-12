"""
Hermetic Tests for Greenfield Replay Engine & Counterfactual Simulator.
Verifies Section 42-44 institutional simulation guarantees.
"""

from datetime import datetime, timezone
import pandas as pd
import pytest

from app.replay.counterfactual import CounterfactualSimulator
from app.replay.engine import ReplayEngine
from app.replay.metrics import calculate_replay_metrics


def _make_synthetic_history(n_bars: int = 80) -> pd.DataFrame:
    base_ts = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
    records = []
    base_price = 100.0

    for i in range(n_bars):
        # Create a swing cycle: up 15 bars, down 15 bars, etc.
        cycle = (i % 30)
        if cycle < 15:
            delta = 0.5
        else:
            delta = -0.5

        base_price += delta
        records.append({
            "timestamp": base_ts + pd.Timedelta(minutes=15 * i),
            "open": base_price - 0.2,
            "high": base_price + 0.8,
            "low": base_price - 0.8,
            "close": base_price + 0.1,
            "volume": 2000.0,
        })

    return pd.DataFrame(records).set_index("timestamp")


def test_calculate_replay_metrics():
    """Verify that calculate_replay_metrics produces correct R-multiples and statistics."""
    closed_trades = [
        {"realized_r": 2.5, "holding_bars": 4, "strategy_family": "reversal", "market_state": "TREND"},
        {"realized_r": -1.0, "holding_bars": 2, "strategy_family": "reversal", "market_state": "TREND"},
        {"realized_r": 1.8, "holding_bars": 5, "strategy_family": "range_fade", "market_state": "RANGE"},
        {"realized_r": 0.0, "holding_bars": 3, "strategy_family": "continuation", "market_state": "TREND"},
    ]
    opps = [{"market_state": "TREND"}, {"market_state": "RANGE"}]
    orders = [
        {"order_type": "limit", "status": "FILLED", "wait_bars": 2},
        {"order_type": "limit", "status": "EXPIRED"},
        {"order_type": "market", "status": "FILLED"},
    ]
    eq_curve = [
        {"equity": 10000.0},
        {"equity": 10250.0},
        {"equity": 10150.0},
        {"equity": 10330.0},
    ]

    metrics = calculate_replay_metrics(closed_trades, opps, orders, eq_curve)

    assert metrics.total_trades == 4
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 1
    assert metrics.breakeven_trades == 1
    assert metrics.win_rate == 0.5
    assert metrics.total_realized_r == pytest.approx(3.3, abs=0.01)
    assert metrics.expectancy_r == pytest.approx(0.825, abs=0.01)
    assert metrics.profit_factor == pytest.approx(4.3, abs=0.01)

    # Execution stats
    assert metrics.execution.limit_orders_placed == 2
    assert metrics.execution.limit_orders_filled == 1
    assert metrics.execution.limit_fill_rate == 0.5
    assert metrics.execution.missed_limit_rate == 0.5


def test_counterfactual_simulator():
    """Verify that CounterfactualSimulator tests Branch A (Market) vs Branch B (Limit)."""
    df = _make_synthetic_history(50)
    
    # Create an opportunity at bar 10
    opp = {
        "id": "opp_test",
        "symbol": "SOL/USDT",
        "direction": "long",
        "strategy_family": "range_fade",
        "market_state": "RANGE",
        "stop_loss": 95.0,
        "take_profit": 115.0,
        "structural_entry": 101.0,
        "structural_rr": 2.5,
    }

    rec = CounterfactualSimulator.evaluate_opportunity(opp, start_bar_idx=10, df=df, ttl_bars=5)

    assert rec.opportunity_id == "opp_test"
    assert rec.direction == "long"
    assert rec.market_entry > 0
    assert rec.limit_entry == 101.0
    assert isinstance(rec.market_realized_r, float)

    summary = CounterfactualSimulator.summarize([rec])
    assert summary.total_evaluated == 1
    assert summary.records[0] == rec


def test_replay_engine_run():
    """Verify that ReplayEngine runs end-to-end bar-by-bar on historical data."""
    df = _make_synthetic_history(60)

    engine = ReplayEngine(
        symbol="SOL/USDT",
        historical_df=df,
        timeframe="15m",
        initial_equity=10_000.0,
        warmup_bars=25,
    )

    result = engine.run()

    assert result.symbol == "SOL/USDT"
    assert result.timeframe == "15m"
    assert result.total_bars_replayed == 35
    assert len(result.equity_curve) == 35
    assert result.metrics is not None
    assert result.counterfactual is not None
    res_dict = result.to_dict()
    assert "total_return_pct" in res_dict
    assert "metrics" in res_dict
    assert "counterfactual" in res_dict
