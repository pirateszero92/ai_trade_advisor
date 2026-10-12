"""
Unit Tests for Risk Allocator & Paper OMS (RISK-001 to RISK-004, OMS-001 to OMS-005)
"""

from datetime import datetime, timezone, timedelta
import pytest

from app.domain.clock import FrozenClock
from app.domain.execution import ExecutionDecision, ExecutionIntent, ExecutionOrderType
from app.domain.opportunity import (
    CausalZone,
    InvalidationCriteria,
    Opportunity,
    OpportunityState,
    StrategyFamily,
    TargetLevel,
    TradeDirection,
)
from app.domain.order import OrderStatus
from app.domain.position import PositionStatus
from app.domain.reason_codes import ReasonCode
from app.domain.risk import RiskStage
from app.facts.models import (
    FlowFact,
    LiquidityFact,
    MarketFacts,
    StructureFact,
    VolatilityFact,
    ZonesFact,
)
from app.oms.paper import PaperOMS
from app.risk.allocator import RiskAllocator


def _create_sample_opp() -> Opportunity:
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    return Opportunity(
        opportunity_id="opp-risk-1",
        lineage_id="lin-risk-1",
        symbol="BTC/USDT",
        direction=TradeDirection.LONG,
        family=StrategyFamily.REVERSAL,
        thesis="Bullish reversal",
        structural_entry=50000.0,
        stop_loss=49000.0,  # 1000 USD risk distance
        targets=[TargetLevel(price=53000.0)],
        invalidation=InvalidationCriteria(
            price=48900.0,
            reason="Stop break",
            condition="close_below",
        ),
        valid_until=now + timedelta(hours=1),
    )


def _create_sample_intent(opp: Opportunity) -> ExecutionIntent:
    return ExecutionIntent(
        opportunity_id=opp.opportunity_id,
        lineage_id=opp.lineage_id,
        decision=ExecutionDecision.PLACE_LIMIT,
        order_type=ExecutionOrderType.LIMIT,
        quoted_price=50100.0,
        executable_entry=50000.0,
        stop_loss=49000.0,
        take_profit=53000.0,
        structural_rr=3.0,
        quoted_rr=2.8,
        limit_rr=2.95,
        spread_pct=0.0001,
        quote_age_seconds=1.0,
        reason_code=ReasonCode.LIMIT_ARMED,
    )


def test_risk_allocator_normal_and_cluster_scaling():
    allocator = RiskAllocator(base_risk_per_trade_pct=1.0, max_daily_loss_pct=3.0)
    opp = _create_sample_opp()
    intent = _create_sample_intent(opp)

    # 1. First trade in crypto_l1 cluster (1% of 10,000 = $100 risk; stop_dist = 1000; qty = 0.1)
    risk_res = allocator.allocate(
        opportunity=opp,
        intent=intent,
        account_equity=10000.0,
        daily_pnl_pct=0.0,
        active_positions=[],
        stage=RiskStage.FINAL_CHECK,
    )
    assert risk_res.approved is True
    assert risk_res.allocated_risk_pct == 1.0
    assert risk_res.risk_budget_usd == 100.0
    assert risk_res.position_size == 0.1

    # 2. Second trade in same cluster (crypto_l1: e.g. ETH/USDT long active) -> 50% scale down
    active = [{"symbol": "ETH/USDT", "direction": "long"}]
    risk_res_2 = allocator.allocate(
        opportunity=opp,
        intent=intent,
        account_equity=10000.0,
        daily_pnl_pct=0.0,
        active_positions=active,
        stage=RiskStage.FINAL_CHECK,
    )
    assert risk_res_2.approved is True
    assert risk_res_2.allocated_risk_pct == 0.5  # 50% scale down!
    assert risk_res_2.position_size == 0.05


def test_risk_allocator_circuit_breaker():
    allocator = RiskAllocator(max_daily_loss_pct=3.0)
    opp = _create_sample_opp()
    intent = _create_sample_intent(opp)

    # Daily loss breached (-3.5% <= -3.0%)
    risk_halt = allocator.allocate(
        opportunity=opp,
        intent=intent,
        account_equity=10000.0,
        daily_pnl_pct=-3.5,
        active_positions=[],
    )
    assert risk_halt.approved is False
    assert risk_halt.reason_code == ReasonCode.DAILY_LOSS_LIMIT


def test_paper_oms_idempotency_and_fill():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(now)
    oms = PaperOMS(clock=clock)

    opp = _create_sample_opp()
    intent = _create_sample_intent(opp)
    risk = RiskAllocator().allocate(
        opportunity=opp,
        intent=intent,
        account_equity=10000.0,
        daily_pnl_pct=0.0,
        active_positions=[],
    )

    # 1. Submit order
    idemp_key = "idemp-test-123"
    order1 = oms.submit_order(opp, intent, risk, idempotency_key=idemp_key)
    assert order1.status == OrderStatus.PENDING

    # 2. Idempotent re-submission returns exact same order without duplication
    order2 = oms.submit_order(opp, intent, risk, idempotency_key=idemp_key)
    assert order1.client_order_id == order2.client_order_id

    # 3. Execute fill
    pos = oms.execute_fill(order1.client_order_id, fill_price=50000.0)
    assert pos.status == PositionStatus.OPEN
    assert pos.average_entry_price == 50000.0
    assert order1.status == OrderStatus.FILLED
    assert oms.get_position(pos.position_id) is not None


def test_paper_oms_pending_limit_revalidation():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(now)
    oms = PaperOMS(clock=clock)

    opp = _create_sample_opp()
    intent = _create_sample_intent(opp)
    risk = RiskAllocator().allocate(
        opportunity=opp,
        intent=intent,
        account_equity=10000.0,
        daily_pnl_pct=0.0,
        active_positions=[],
    )

    order = oms.submit_order(opp, intent, risk, idempotency_key="idemp-lim-1")
    assert order.status == OrderStatus.PENDING

    # Closed candle facts where price broke below invalidation price (48900)
    facts_broken = MarketFacts(
        symbol="BTC/USDT",
        timeframe="15m",
        candle_close_time=now + timedelta(minutes=15),
        current_price=48800.0,  # Broke structure!
        structure=StructureFact(),
        liquidity=LiquidityFact(),
        zones=ZonesFact(),
        flow=FlowFact(),
        volatility=VolatilityFact(atr=500.0),
    )

    cancelled = oms.revalidate_pending_limits({"BTC/USDT": facts_broken})
    assert order.client_order_id in cancelled
    assert order.status == OrderStatus.CANCELLED
    assert order.state_reason == ReasonCode.STRUCTURE_INVALIDATED
