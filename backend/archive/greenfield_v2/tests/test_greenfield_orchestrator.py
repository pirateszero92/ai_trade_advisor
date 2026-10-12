"""
End-to-End Integration Tests for Greenfield Decision Orchestrator
Verifies the complete flow from closed candles through OMS execution and position management.
"""

from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd
import pytest

from app.domain.clock import FrozenClock
from app.domain.execution import ExecutionDecision
from app.domain.opportunity import OpportunityState, StrategyFamily, TradeDirection
from app.domain.order import OrderStatus
from app.engines.smc_engine import SMCEngine
from app.facts.engine import MarketFactsEngine
from app.market_data.quote import MarketQuote
from app.orchestrator import DecisionOrchestrator


def _create_synthetic_15m_dataset(now: datetime, bars: int = 120) -> pd.DataFrame:
    """Generates synthetic 15m closed candles oscillating in a defined range [91, 110], closing at `now`."""
    end_open = now - timedelta(minutes=15)
    start = end_open - timedelta(minutes=15 * (bars - 1))
    timestamps = [start + timedelta(minutes=15 * i) for i in range(bars)]

    records = []
    for i in range(bars):
        cycle = np.sin(i / 8.0) * 8.0
        base = 100.0 + cycle
        o = base
        c = base + 0.2
        if i == bars - 1:
            o = 109.2
            c = 109.6  # touches premium boundary (110.0)
            h = 110.0
            l = 109.0
            v = 100.0
        else:
            h = max(o, c) + 0.5
            l = min(o, c) - 0.5
            v = 500.0
        records.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
    return pd.DataFrame(records, index=timestamps)


def test_orchestrator_end_to_end_market_execution():
    now = datetime(2026, 9, 23, 12, 35, 0, tzinfo=timezone.utc)
    clock = FrozenClock(now)
    smc = SMCEngine(swing_length=15, internal_swing_length=5)
    facts_engine = MarketFactsEngine(smc_engine=smc)
    orchestrator = DecisionOrchestrator(clock=clock, facts_engine=facts_engine)

    df = _create_synthetic_15m_dataset(now=now, bars=120)

    # Executable quote right at the premium boundary (109.60) -> ready for Short Market Entry
    quote = MarketQuote(
        symbol="SOL/USDT",
        bid=109.60,
        ask=109.65,
        last=109.62,
        timestamp=now - timedelta(seconds=1),
    )

    result = orchestrator.run_cycle(
        symbol="SOL/USDT",
        raw_ohlcv=df,
        quote=quote,
        account_equity=10000.0,
        daily_pnl_pct=0.0,
        timeframe="15m",
    )

    assert result["status"] == "order_placed"
    opp = result["opportunity"]
    assert opp is not None
    assert opp.family == StrategyFamily.RANGE_FADE
    assert opp.direction == TradeDirection.SHORT

    order = result["order"]
    assert order is not None
    assert order.status == OrderStatus.ACCEPTED
    assert order.requested_quantity > 0

    # Execute fill in OMS
    pos = orchestrator.oms.execute_fill(order.client_order_id, fill_price=109.60)
    assert pos.symbol == "SOL/USDT"
    assert pos.quantity == order.requested_quantity

    # Verify Decision Trace recorded all critical stages
    stages_recorded = [t.stage.value for t in orchestrator.traces]
    assert "OPPORTUNITY_CREATED" in stages_recorded
    assert "EXECUTION_PLAN" in stages_recorded
    assert "RISK_FINAL" in stages_recorded
    assert "OMS_SUBMIT" in stages_recorded


def test_orchestrator_sol_scenario_arms_causal_limit_without_rejection():
    """
    Directly tests the SOL/USDT case that caused user frustration:
    When current market price is unfavorable/extended, the system does NOT reject.
    It arms a pending limit at the causal structural entry.
    """
    now = datetime(2026, 9, 23, 12, 35, 0, tzinfo=timezone.utc)
    clock = FrozenClock(now)
    smc = SMCEngine(swing_length=15, internal_swing_length=5)
    facts_engine = MarketFactsEngine(smc_engine=smc)
    orchestrator = DecisionOrchestrator(clock=clock, facts_engine=facts_engine)

    df = _create_synthetic_15m_dataset(now=now, bars=120)

    # Market price ran down away to 105.0 (extended far from structural entry at 109.6)
    quote_extended = MarketQuote(
        symbol="SOL/USDT",
        bid=105.00,
        ask=105.05,
        last=105.02,
        timestamp=now - timedelta(seconds=1),
    )

    result = orchestrator.run_cycle(
        symbol="SOL/USDT",
        raw_ohlcv=df,
        quote=quote_extended,
        account_equity=10000.0,
        daily_pnl_pct=0.0,
        timeframe="15m",
    )

    assert result["status"] == "order_placed"
    intent = result["intent"]
    assert intent.decision == ExecutionDecision.PLACE_LIMIT
    assert intent.order_type.value == "limit"
    assert intent.executable_entry == 109.6  # Arms limit at causal boundary

    order = result["order"]
    assert order.order_type == "limit"
    assert order.limit_price == 109.6
    assert order.status == OrderStatus.PENDING

    # Opportunity is in ORDER_PENDING (or LIMIT_ARMED), NOT REJECTED!
    opp = result["opportunity"]
    assert opp.state == OpportunityState.ORDER_PENDING
    assert not opp.is_terminal


def test_opportunities_api_endpoints():
    """Verify FastAPI routes for listing opportunities and running replay."""
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock, patch
    from app.main import app
    from app.core.security import verify_api_key

    app.dependency_overrides[verify_api_key] = lambda: "test_user"
    client = TestClient(app)

    # 1. Test GET /api/v1/opportunities/
    res = client.get("/api/v1/opportunities/")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "opportunities" in data

    # 2. Test POST /api/v1/opportunities/replay
    mock_df = _create_synthetic_15m_dataset(now=datetime.now(timezone.utc), bars=60)
    with patch("app.api.opportunities_api._market.get_ohlcv", new=AsyncMock(return_value=mock_df)):
        res_replay = client.post(
            "/api/v1/opportunities/replay",
            json={
                "symbol": "SOL/USDT",
                "timeframe": "15m",
                "market_type": "crypto",
                "exchange": "binance",
                "initial_equity": 10000.0,
                "warmup_bars": 25,
                "limit": 60,
            },
        )
        assert res_replay.status_code == 200
        replay_data = res_replay.json()
        assert replay_data["status"] == "ok"
        assert "result" in replay_data
        assert "metrics" in replay_data["result"]
        assert "counterfactual" in replay_data["result"]

