"""
Unit tests validating Phase 1, Phase 2, and Phase 3 remediations from the 12-point audit.
"""

from __future__ import annotations

from decimal import Decimal
import uuid
import pandas as pd
import pytest

from app.engines.backtest_engine import (
    ExecutionAssumptions,
    ReleaseCriteria,
    evaluate_release_gate,
    run_walk_forward_backtest,
    simulate_execution,
)
from app.engines.risk_engine import RiskEngine
from app.engines.smc_engine import SMCSignal
from app.services.paper_oms import (
    PaperOMSValidation,
)


def test_risk_engine_quantity_step_decimal_quantization():
    """Issue #11: quantity_step=0.25 must quantize strictly to 0.25 multiples without rounding up."""
    risk_engine = RiskEngine()
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        direction="long",
        entry=100.0,
        stop_loss=98.0,  # 2% stop distance, well within 5% limit
        take_profit=106.0,
        risk_reward=3.0,
    )
    assessment = risk_engine.evaluate(
        signal=signal,
        account_balance=10000.0,
        risk_per_trade_pct=1.0,
        max_leverage=5.0,
        quantity_step=0.25,
        open_positions=0,
    )
    assert assessment.approved is True
    size_d = Decimal(str(assessment.position_size))
    assert size_d % Decimal("0.25") == Decimal("0")

    signal2 = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        direction="long",
        entry=100.0,
        stop_loss=97.0,
        take_profit=106.0,
        risk_reward=2.0,
    )
    assessment2 = risk_engine.evaluate(
        signal=signal2,
        account_balance=10000.0,
        risk_per_trade_pct=1.0,
        max_leverage=5.0,
        quantity_step=0.25,
        open_positions=0,
    )
    assert assessment2.approved is True
    assert assessment2.position_size == 33.25


def test_backtest_engine_start_index_parameter():
    """Issue #6: run_walk_forward_backtest accepts direct start_index without oos_fraction clamp."""
    dates = pd.date_range("2026-01-01", periods=300, freq="15min")
    frame = pd.DataFrame(
        {
            "open": [100.0] * 300,
            "high": [101.0] * 300,
            "low": [99.0] * 300,
            "close": [100.0] * 300,
            "volume": [1000.0] * 300,
        },
        index=dates,
    )
    config = {
        "timeframe_profiles": {
            "enabled": True,
            "roles": {"trigger": {"timeframe": "15m"}},
        }
    }
    result = run_walk_forward_backtest(
        market_data=frame,
        symbol="BTC/USDT",
        timeframe="15m",
        config=config,
        assumptions=ExecutionAssumptions(),
        start_index=280,
        warmup_bars=60,
    )
    assert result["status"] == "completed"
    assert result["evaluation_mode"] == "anchored_out_of_sample_replay"


def test_backtest_release_gate_allows_candidate_draft_unvalidated():
    """Issue #9: evaluate_release_gate allows candidate draft_unvalidated policy calibration."""
    metrics = {
        "completed_trades": 120,
        "expectancy_r": 0.35,
        "profit_factor": 1.6,
        "max_drawdown_pct": 5.0,
        "fill_rate": 90.0,
        "regimes_tested": 3,
        "evaluation_mode": "rolling_walk_forward_oos",
        "oos_fold_count": 3,
        "by_scenario": {
            "sweep_reversal": {"trades": 60},
            "displacement_retest": {"trades": 60},
        },
        "data_readiness": {"coverage": 95.0, "history_days": 180.0},
        "tri_core_policy": {"calibration_status": "draft_unvalidated"},
    }
    gate = evaluate_release_gate(metrics, ReleaseCriteria())
    assert gate["passed"] is True
    policy_check = next(c for c in gate["checks"] if c["name"] == "policy_calibration")
    assert policy_check["passed"] is True


def test_simulate_execution_conservative_same_bar_limit_fill():
    """Issue #7 & #8: maker limit fill clamped to entry and no same-bar take profit on limit entries."""
    bars = pd.DataFrame(
        [
            {"open": 102.0, "high": 115.0, "low": 98.0, "close": 105.0, "volume": 1000},
            {"open": 105.0, "high": 120.0, "low": 104.0, "close": 118.0, "volume": 1000},
        ],
        index=pd.date_range("2026-01-01", periods=2, freq="15min"),
    )
    result = simulate_execution(
        future_bars=bars,
        direction="long",
        entry=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        requested_quantity=10.0,
        order_type="limit",
        assumptions=ExecutionAssumptions(
            latency_bars=0,
            fee_bps=0,
            spread_bps=0,
            slippage_bps=0,
            max_volume_participation=1.0,
            max_fill_fraction_per_bar=1.0,
        ),
    )
    assert result is not None
    assert result["filled_quantity"] == 10.0
    assert result["average_entry"] <= 100.0
    # Because same-bar TP on limit fill is disallowed, the trade held into bar 1
    assert result["exit_reason"] == "take_profit"


@pytest.mark.anyio
async def test_paper_oms_close_position_validates_absurd_price(tmp_path):
    """Issue #4: close_position rejects prices deviating >20% from live market quote."""
    from tests.test_paper_oms import _make_oms, _order_payload

    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    trade = await oms.place_order(
        _order_payload(direction="long", symbol=f"P6CLOSE{uuid.uuid4().hex[:6]}/USDT")
    )
    pos_id = trade["id"]

    with pytest.raises(PaperOMSValidation, match="deviates >20%"):
        await oms.close_position(pos_id, close_price=200.0)

    closed = await oms.close_position(pos_id, close_price=110.0)
    assert closed["status"] == "closed"

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_event_trigger_cancel_sweeps_transitioned_and_replaced_limits(tmp_path, monkeypatch):
    """Issue #10: _cancel_invalidated_limit_entry cancels pending limit orders on new event replacement."""
    from app.services.event_trigger import MarketMonitor
    from tests.test_paper_oms import _make_oms, _order_payload

    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    monkeypatch.setattr("app.services.paper_oms.paper_oms", oms)
    sym = f"P6EVT{uuid.uuid4().hex[:6]}/USDT"

    order_data = _order_payload(
        direction="long",
        order_type="limit",
        symbol=sym,
    )
    order_data["entry"] = 95.0
    order_data["stop_loss"] = 90.0
    order_data["take_profit"] = 110.0
    order_data["tri_core_event_id"] = "evt-123"
    order_data["decision_snapshot_id"] = "1234567890abcdef"
    order_data["setup_grade"] = "A"
    order_data["setup_type"] = "sweep_reversal"
    order_data["setup_timeframe"] = "15m"
    placed = await oms.place_order(order_data)
    assert placed["status"] == "pending"

    monitor = MarketMonitor()
    await monitor._cancel_invalidated_limit_entry(
        symbol=sym,
        setup={
            "trigger_state": "armed",
            "event_id": "evt-456",
            "state_reason": "New sweep detected",
        },
    )

    pos = await oms.get_position(placed["id"])
    assert pos["status"] == "cancelled"
    assert "replaced by new event evt-456" in pos["close_reason"]

    await oms.stop()
    await engine.dispose()
