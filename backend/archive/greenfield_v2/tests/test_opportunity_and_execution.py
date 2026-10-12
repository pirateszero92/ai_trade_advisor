"""
Unit Tests for Opportunity Engine & Execution Planner (OPP-001 to OPP-005, EXEC-001 to EXEC-006)
"""

from datetime import datetime, timezone, timedelta
import pytest

from app.domain.clock import FrozenClock
from app.domain.execution import ExecutionDecision, ExecutionOrderType
from app.domain.opportunity import (
    CausalZone,
    InvalidationCriteria,
    Opportunity,
    OpportunityState,
    StrategyFamily,
    TargetLevel,
    TradeDirection,
)
from app.domain.reason_codes import ReasonCode
from app.execution.planner import ExecutionPlanner
from app.facts.models import (
    FlowFact,
    LiquidityFact,
    MarketFacts,
    StructureFact,
    VolatilityFact,
    ZonesFact,
)
from app.market_data.quote import MarketQuote
from app.opportunity.engine import OpportunityEngine
from app.opportunity.store import OpportunityStore
from app.setups.candidate import OpportunityCandidate


def _create_candidate() -> OpportunityCandidate:
    return OpportunityCandidate(
        family=StrategyFamily.RANGE_FADE,
        direction=TradeDirection.SHORT,
        thesis="Premium range boundary fade",
        evidence=["Tested premium boundary"],
        entry_zone=CausalZone(
            zone_id="premium_zone_1",
            kind="boundary",
            top=120.5,
            bottom=119.5,
            active=True,
        ),
        structural_entry=119.5,
        stop_loss=122.0,
        targets=[TargetLevel(price=112.0, label="discount_target")],
        invalidation=InvalidationCriteria(
            price=122.5,
            reason="Closed above range boundary",
            condition="close_above",
        ),
        ttl_bars=2,
    )


def test_opportunity_engine_lifecycle_and_revalidation():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(now)
    store = OpportunityStore()
    engine = OpportunityEngine(store=store, clock=clock)

    candidate = _create_candidate()
    opp = engine.create_from_candidate(candidate, symbol="SOL/USDT", timeframe="15m")

    assert opp.state == OpportunityState.DETECTED
    assert store.get(opp.opportunity_id) is not None

    facts = MarketFacts(
        symbol="SOL/USDT",
        timeframe="15m",
        candle_close_time=now,
        current_price=119.8,
        structure=StructureFact(),
        liquidity=LiquidityFact(),
        zones=ZonesFact(),
        flow=FlowFact(),
        volatility=VolatilityFact(atr=2.0),
    )

    # 1. Revalidation advances to VALIDATED
    opp = engine.revalidate(opp, facts)
    assert opp.state == OpportunityState.VALIDATED

    # 2. Structural Invalidation when price > 122.5
    facts_invalid = MarketFacts(
        symbol="SOL/USDT",
        timeframe="15m",
        candle_close_time=now,
        current_price=123.0,  # Broke above 122.5
        structure=StructureFact(),
        liquidity=LiquidityFact(),
        zones=ZonesFact(),
        flow=FlowFact(),
        volatility=VolatilityFact(atr=2.0),
    )
    opp = engine.revalidate(opp, facts_invalid)
    assert opp.state == OpportunityState.INVALIDATED
    assert opp.is_terminal

    # 3. TTL Expiration check
    opp2 = engine.create_from_candidate(candidate, symbol="SOL/USDT", timeframe="15m")
    clock.advance(seconds=3600)  # Advance past valid_until (30m)
    opp2 = engine.revalidate(opp2, facts)
    assert opp2.state == OpportunityState.EXPIRED
    assert opp2.is_terminal


def test_execution_planner_market_now():
    planner = ExecutionPlanner(minimum_executable_rr=1.5, market_max_extension_atr=0.25)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    candidate = _create_candidate()
    store = OpportunityStore()
    opp = OpportunityEngine(store=store).create_from_candidate(candidate, symbol="SOL/USDT")

    # Short order: Bid is market fill price
    # Structural entry is 119.5, price currently at 119.6 (adverse extension = 0.05 ATR < 0.25)
    quote = MarketQuote(
        symbol="SOL/USDT",
        bid=119.55,
        ask=119.60,
        last=119.58,
        timestamp=now - timedelta(seconds=1),
    )

    intent = planner.plan(opp, quote, atr=2.0, now=now)
    assert intent.decision == ExecutionDecision.MARKET_NOW
    assert intent.order_type == ExecutionOrderType.MARKET
    assert intent.quoted_rr >= 1.5
    assert intent.reason_code == ReasonCode.MARKET_NOW_READY


def test_execution_planner_place_limit_when_market_rr_degraded():
    """The exact SOL/USDT scenario: market entry extended/poor, arms causal limit."""
    planner = ExecutionPlanner(minimum_executable_rr=1.5, market_max_extension_atr=0.25)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    candidate = _create_candidate()
    store = OpportunityStore()
    opp = OpportunityEngine(store=store).create_from_candidate(candidate, symbol="SOL/USDT")

    # Price ran down away to 118.0 (adverse extension = 1.5 ATR > 0.25 ATR, market R:R degraded)
    quote = MarketQuote(
        symbol="SOL/USDT",
        bid=118.00,
        ask=118.05,
        last=118.02,
        timestamp=now - timedelta(seconds=1),
    )

    intent = planner.plan(opp, quote, atr=2.0, now=now)
    assert intent.decision == ExecutionDecision.PLACE_LIMIT
    assert intent.order_type == ExecutionOrderType.LIMIT
    assert intent.executable_entry == opp.structural_entry  # 119.5
    assert intent.limit_rr >= 1.5
    assert intent.reason_code == ReasonCode.LIMIT_ARMED
