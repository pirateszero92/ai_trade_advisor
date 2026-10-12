"""
Comprehensive Unit Tests for Domain Contracts (CORE-001 to CORE-008)
Verifies schema constraints, immutable state machines, and clock contracts.
"""

from datetime import datetime, timezone, timedelta
import pytest

from app.domain import (
    Clock,
    SystemClock,
    FrozenClock,
    ReplayClock,
    generate_opportunity_id,
    generate_lineage_id,
    generate_order_id,
    generate_position_id,
    generate_trace_id,
    ReasonCode,
    TradeDirection,
    StrategyFamily,
    OpportunityState,
    TargetLevel,
    CausalZone,
    InvalidationCriteria,
    Opportunity,
    ExecutionDecision,
    ExecutionOrderType,
    ExecutionIntent,
    RiskStage,
    RiskAssessment,
    OrderStatus,
    OrderSide,
    Order,
    PositionStatus,
    ProtectionStage,
    Position,
    TraceStage,
    TraceRecord,
)


def test_core_001_ids_and_clocks():
    # IDs
    opp_id = generate_opportunity_id("BTC/USDT", "15m")
    assert opp_id.startswith("opp_BTCUSDT_15m_")
    
    lin_id = generate_lineage_id("SOL/USDT", "15m", "range_fade")
    assert lin_id.startswith("lin_SOLUSDT_15m_range_fade_")

    ord_id = generate_order_id(opp_id, "buy")
    assert ord_id.startswith(f"ord_{opp_id}_buy_")

    pos_id = generate_position_id(opp_id)
    assert pos_id.startswith(f"pos_{opp_id}_")

    trc_id = generate_trace_id("execution_plan")
    assert trc_id.startswith("trc_execution_plan_")

    # Clocks
    sys_clock = SystemClock()
    t1 = sys_clock.now()
    assert t1.tzinfo == timezone.utc

    fixed = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    frozen = FrozenClock(fixed)
    assert frozen.now() == fixed
    frozen.advance(seconds=60)
    assert frozen.now() == fixed + timedelta(seconds=60)

    replay = ReplayClock(fixed)
    assert replay.now() == fixed
    bar_close = datetime(2026, 9, 23, 12, 15, 0, tzinfo=timezone.utc)
    replay.update_to_bar(bar_close)
    assert replay.now() == bar_close


def test_core_002_opportunity_schema_and_geometry():
    now = datetime.now(timezone.utc)
    
    # Valid Long
    opp_long = Opportunity(
        opportunity_id="opp-1",
        lineage_id="lin-1",
        symbol="BTC/USDT",
        direction=TradeDirection.LONG,
        family=StrategyFamily.REVERSAL,
        thesis="Bullish sweep of sell-side liquidity with causal OB demand",
        evidence=["CVD absorption", "sweep SSL"],
        structural_entry=100.0,
        stop_loss=98.0,
        targets=[TargetLevel(price=105.0, label="primary")],
        invalidation=InvalidationCriteria(
            price=97.5,
            reason="Structure broken below sweep low",
            condition="close_below",
        ),
        valid_until=now + timedelta(minutes=30),
    )
    assert opp_long.structural_rr == 2.5
    assert opp_long.primary_target == 105.0
    assert not opp_long.is_terminal

    # Invalid Long (stop above entry)
    with pytest.raises(ValueError, match="LONG geometry invalid"):
        Opportunity(
            opportunity_id="opp-2",
            lineage_id="lin-2",
            symbol="BTC/USDT",
            direction=TradeDirection.LONG,
            family=StrategyFamily.REVERSAL,
            thesis="Invalid stop",
            structural_entry=100.0,
            stop_loss=102.0,  # Invalid
            targets=[TargetLevel(price=105.0)],
            invalidation=InvalidationCriteria(price=101.0, reason="x", condition="close_below"),
            valid_until=now + timedelta(minutes=30),
        )

    # Valid Short
    opp_short = Opportunity(
        opportunity_id="opp-3",
        lineage_id="lin-3",
        symbol="ETH/USDT",
        direction=TradeDirection.SHORT,
        family=StrategyFamily.RANGE_FADE,
        thesis="Premium range boundary test with absorption",
        structural_entry=2000.0,
        stop_loss=2050.0,
        targets=[TargetLevel(price=1900.0)],
        invalidation=InvalidationCriteria(price=2060.0, reason="x", condition="close_above"),
        valid_until=now + timedelta(minutes=30),
    )
    assert opp_short.structural_rr == 2.0


def test_core_003_opportunity_state_machine():
    now = datetime.now(timezone.utc)
    opp = Opportunity(
        opportunity_id="opp-sm",
        lineage_id="lin-sm",
        symbol="SOL/USDT",
        direction=TradeDirection.SHORT,
        family=StrategyFamily.RANGE_FADE,
        thesis="Range fade",
        structural_entry=120.0,
        stop_loss=122.0,
        targets=[TargetLevel(price=114.0)],
        invalidation=InvalidationCriteria(price=123.0, reason="break", condition="close_above"),
        valid_until=now + timedelta(minutes=30),
    )
    assert opp.state == OpportunityState.DETECTED

    # Valid lifecycle progression
    opp.transition(OpportunityState.VALIDATED, ReasonCode.OPPORTUNITY_VALIDATED)
    assert opp.state == OpportunityState.VALIDATED
    assert opp.version == 2

    # Poor market entry -> WAITING_ENTRY or LIMIT_ARMED (Not REJECTED!)
    opp.transition(OpportunityState.LIMIT_ARMED, ReasonCode.LIMIT_ARMED)
    assert opp.state == OpportunityState.LIMIT_ARMED

    opp.transition(OpportunityState.RISK_PRECHECK, ReasonCode.RISK_PRECHECK_OK)
    opp.transition(OpportunityState.ORDER_PENDING, ReasonCode.ORDER_SUBMITTED)
    opp.transition(OpportunityState.FILLED, ReasonCode.ORDER_FILLED)
    opp.transition(OpportunityState.MANAGED, ReasonCode.POSITION_OPENED)
    opp.transition(OpportunityState.CLOSED, ReasonCode.POSITION_CLOSED)
    assert opp.is_terminal

    # Terminal state cannot transition anywhere
    with pytest.raises(ValueError, match="Invalid opportunity state transition"):
        opp.transition(OpportunityState.WAITING_ENTRY, ReasonCode.WAITING_ENTRY)


def test_core_004_execution_intent():
    intent = ExecutionIntent(
        opportunity_id="opp-1",
        lineage_id="lin-1",
        decision=ExecutionDecision.PLACE_LIMIT,
        order_type=ExecutionOrderType.LIMIT,
        quoted_price=120.5,
        executable_entry=120.0,
        stop_loss=122.0,
        take_profit=114.0,
        structural_rr=3.0,
        quoted_rr=0.95,  # market RR too low
        limit_rr=2.85,   # limit RR exceeds floor
        spread_pct=0.0005,
        quote_age_seconds=1.2,
        reason_code=ReasonCode.LIMIT_ARMED,
        reasons=["Market executable R:R below floor; armed causal limit at structural entry"],
    )
    assert intent.is_executable
    assert intent.decision == ExecutionDecision.PLACE_LIMIT
    assert intent.limit_rr >= 2.0


def test_core_005_risk_assessment():
    risk = RiskAssessment(
        opportunity_id="opp-1",
        lineage_id="lin-1",
        stage=RiskStage.PRE_CHECK,
        approved=True,
        allocated_risk_pct=0.75,
        risk_budget_usd=75.0,
        position_size=1.5,
        position_notional=180.0,
        account_equity=10000.0,
        daily_pnl_pct=0.5,
        open_positions_count=1,
        cluster_positions_count=0,
        reason_code=ReasonCode.RISK_PRECHECK_OK,
    )
    assert risk.approved
    assert risk.stage == RiskStage.PRE_CHECK
    assert risk.allocated_risk_pct == 0.75


def test_core_006_order_and_position_lifecycle():
    order = Order(
        client_order_id="ord-1",
        opportunity_id="opp-1",
        lineage_id="lin-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type="limit",
        requested_quantity=0.1,
        limit_price=50000.0,
        stop_loss=49000.0,
        take_profit=53000.0,
        idempotency_key="idemp-key-1",
    )
    assert order.status == OrderStatus.CREATED

    order.transition(OrderStatus.PENDING, ReasonCode.ORDER_SUBMITTED)
    order.transition(OrderStatus.ACCEPTED, ReasonCode.ORDER_ACCEPTED)
    order.transition(OrderStatus.FILLED, ReasonCode.ORDER_FILLED)
    assert order.status == OrderStatus.FILLED

    pos = Position(
        position_id="pos-1",
        opportunity_id="opp-1",
        lineage_id="lin-1",
        symbol="BTC/USDT",
        direction="long",
        quantity=0.1,
        average_entry_price=50000.0,
        current_stop_loss=49000.0,
        take_profit=53000.0,
    )
    assert pos.status == PositionStatus.OPEN
    assert pos.protection_stage == ProtectionStage.INITIAL

    pos.close(exit_price=52000.0, net_pnl=200.0, reason=ReasonCode.TAKE_PROFIT_HIT)
    assert pos.status == PositionStatus.CLOSED
    assert pos.realized_pnl_net == 200.0


def test_core_008_decision_trace():
    trace = TraceRecord(
        trace_id=generate_trace_id("execution_plan"),
        opportunity_id="opp-1",
        lineage_id="lin-1",
        stage=TraceStage.EXECUTION_PLAN,
        reason_code=ReasonCode.LIMIT_ARMED,
        facts={
            "market_rr": 0.95,
            "limit_rr": 2.85,
            "decision": "PLACE_LIMIT",
        },
    )
    assert trace.stage == TraceStage.EXECUTION_PLAN
    assert trace.facts["decision"] == "PLACE_LIMIT"
