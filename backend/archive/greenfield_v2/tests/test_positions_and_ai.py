"""
Unit Tests for Position Manager & Advisory AI (POS-001 to POS-003, AI-001 to AI-003)
"""

from datetime import datetime, timezone, timedelta
import pytest

from app.ai.reviewer import AIReviewer, AIReviewResult
from app.domain.opportunity import (
    InvalidationCriteria,
    Opportunity,
    StrategyFamily,
    TargetLevel,
    TradeDirection,
)
from app.domain.position import Position, PositionStatus, ProtectionStage
from app.domain.reason_codes import ReasonCode
from app.facts.models import (
    FlowFact,
    LiquidityFact,
    MarketFacts,
    StructureFact,
    VolatilityFact,
    ZonesFact,
)
from app.market_data.quote import MarketQuote
from app.market_state.classifier import MarketState, MarketStateClassification
from app.positions.manager import PositionManager


def test_position_manager_auto_break_even():
    pm = PositionManager(auto_be_trigger_r=1.0, auto_be_buffer_pct=0.001)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # Long position: entry = 100, initial stop = 98 (risk = 2 USD)
    pos = Position(
        position_id="pos-1",
        opportunity_id="opp-1",
        lineage_id="lin-1",
        symbol="SOL/USDT",
        direction="long",
        quantity=10.0,
        average_entry_price=100.0,
        current_stop_loss=98.0,
        take_profit=110.0,
    )
    assert pos.protection_stage == ProtectionStage.INITIAL

    # Price moves to 102.0 (+2 USD = +1.0 R)
    quote_gain = MarketQuote(
        symbol="SOL/USDT",
        bid=102.1,
        ask=102.2,
        last=102.15,
        timestamp=now,
    )

    pm.update_position(pos, quote_gain)
    assert pos.protection_stage == ProtectionStage.BREAKEVEN
    # Stop advanced to entry + buffer (100.1)
    assert pos.current_stop_loss == 100.1


def test_position_manager_stop_loss_and_take_profit():
    pm = PositionManager()
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # Long position: entry = 100, SL = 98, TP = 106
    pos = Position(
        position_id="pos-2",
        opportunity_id="opp-2",
        lineage_id="lin-2",
        symbol="SOL/USDT",
        direction="long",
        quantity=1.0,
        average_entry_price=100.0,
        current_stop_loss=98.0,
        take_profit=106.0,
        fees_total=0.20,
    )

    # 1. Price drops to 97.9 -> SL hit
    quote_sl = MarketQuote(symbol="SOL/USDT", bid=97.9, ask=98.0, last=97.95, timestamp=now)
    pm.update_position(pos, quote_sl)
    assert pos.status == PositionStatus.CLOSED
    assert pos.close_reason == ReasonCode.STOP_LOSS_HIT
    assert pos.realized_pnl_net < 0.0

    # 2. TP test on separate position
    pos_tp = Position(
        position_id="pos-3",
        opportunity_id="opp-3",
        lineage_id="lin-3",
        symbol="SOL/USDT",
        direction="long",
        quantity=1.0,
        average_entry_price=100.0,
        current_stop_loss=98.0,
        take_profit=106.0,
        fees_total=0.20,
    )
    quote_tp = MarketQuote(symbol="SOL/USDT", bid=106.5, ask=106.6, last=106.55, timestamp=now)
    pm.update_position(pos_tp, quote_tp)
    assert pos_tp.status == PositionStatus.CLOSED
    assert pos_tp.close_reason == ReasonCode.TAKE_PROFIT_HIT
    assert pos_tp.realized_pnl_net > 0.0


def test_ai_reviewer_advisory_guardrails():
    reviewer = AIReviewer()

    # 1. Sanitize strips any unauthorized modification attempts
    illicit_response = {
        "verdict": "COHERENT",
        "reason": "Looking good",
        "entry": 105.0,        # Illicit!
        "stop_loss": 95.0,      # Illicit!
        "position_size": 100.0, # Illicit!
    }
    sanitized = AIReviewResult.sanitize_raw_response("opp-1", illicit_response)
    assert sanitized.verdict == "COHERENT"
    assert not hasattr(sanitized, "entry")
    assert not hasattr(sanitized, "stop_loss")

    # 2. Local fact coherence check
    opp = Opportunity(
        opportunity_id="opp-test",
        lineage_id="lin-test",
        symbol="SOL/USDT",
        direction=TradeDirection.SHORT,
        family=StrategyFamily.RANGE_FADE,
        thesis="Range fade",
        structural_entry=120.0,
        stop_loss=122.0,
        targets=[TargetLevel(price=114.0)],
        invalidation=InvalidationCriteria(price=123.0, reason="x", condition="close_above"),
        valid_until=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    facts_aligned = MarketFacts(
        symbol="SOL/USDT",
        timeframe="15m",
        candle_close_time=datetime.now(timezone.utc),
        current_price=120.0,
        structure=StructureFact(),
        liquidity=LiquidityFact(),
        zones=ZonesFact(),
        flow=FlowFact(cvd_direction="bearish"),  # Aligned with Short!
        volatility=VolatilityFact(atr=2.0),
    )
    state = MarketStateClassification(state=MarketState.RANGE)

    res_coherent = reviewer.review_facts_locally(opp, facts_aligned, state)
    assert res_coherent.verdict == "COHERENT"

    # Flow opposing -> CONFLICT
    facts_opposing = MarketFacts(
        symbol="SOL/USDT",
        timeframe="15m",
        candle_close_time=datetime.now(timezone.utc),
        current_price=120.0,
        structure=StructureFact(),
        liquidity=LiquidityFact(),
        zones=ZonesFact(),
        flow=FlowFact(cvd_direction="bullish"),  # Opposing Short!
        volatility=VolatilityFact(atr=2.0),
    )
    res_conflict = reviewer.review_facts_locally(opp, facts_opposing, state)
    assert res_conflict.verdict == "CONFLICT"
