"""
Unit Tests for Setup Detectors (SETUP-001 to SETUP-004)
"""

from datetime import datetime, timezone
import pytest

from app.domain.opportunity import (
    CausalZone,
    StrategyFamily,
    TradeDirection,
)
from app.facts.models import (
    FlowFact,
    LiquidityFact,
    MarketFacts,
    StructureFact,
    VolatilityFact,
    ZonesFact,
)
from app.market_state.classifier import (
    MarketState,
    MarketStateClassification,
)
from app.setups import (
    BreakoutRetestDetector,
    CompositeSetupDetector,
    ContinuationDetector,
    RangeFadeDetector,
    ReversalDetector,
)


def _base_facts(price: float = 100.0) -> MarketFacts:
    return MarketFacts(
        symbol="SOL/USDT",
        timeframe="15m",
        candle_close_time=datetime.now(timezone.utc),
        current_price=price,
        structure=StructureFact(),
        liquidity=LiquidityFact(),
        zones=ZonesFact(
            equilibrium=100.0,
            premium_boundary=110.0,
            discount_boundary=90.0,
        ),
        flow=FlowFact(),
        volatility=VolatilityFact(atr=2.0),
    )


def test_range_fade_detector():
    detector = RangeFadeDetector()
    range_state = MarketStateClassification(state=MarketState.RANGE)

    # 1. At premium boundary (price = 109.5, boundary = 110.0, atr = 2.0)
    facts_prem = _base_facts(price=109.5)
    cand_short = detector.detect(facts_prem, range_state)
    assert cand_short is not None
    assert cand_short.family == StrategyFamily.RANGE_FADE
    assert cand_short.direction == TradeDirection.SHORT
    assert cand_short.structural_entry == 109.5
    assert cand_short.stop_loss > 109.5
    assert cand_short.primary_target < 109.5
    assert cand_short.structural_rr > 1.5

    # 2. At discount boundary (price = 90.5, boundary = 90.0)
    facts_disc = _base_facts(price=90.5)
    cand_long = detector.detect(facts_disc, range_state)
    assert cand_long is not None
    assert cand_long.direction == TradeDirection.LONG
    assert cand_long.stop_loss < 90.5
    assert cand_long.primary_target > 90.5

    # 3. Trending market should ignore range fade
    trend_state = MarketStateClassification(state=MarketState.TREND)
    assert detector.detect(facts_prem, trend_state) is None


def test_reversal_detector():
    detector = ReversalDetector()
    state = MarketStateClassification(state=MarketState.RANGE)

    # Sweep Sell-Side Liquidity
    facts = _base_facts(price=99.0)
    facts.liquidity.liquidity_swept = True
    facts.liquidity.sweep_direction = "low"
    facts.liquidity.sweep_price = 98.0  # swept at 98, now price is 99 (reclaimed!)
    facts.liquidity.sweep_age_bars = 1

    cand = detector.detect(facts, state)
    assert cand is not None
    assert cand.family == StrategyFamily.REVERSAL
    assert cand.direction == TradeDirection.LONG
    assert cand.stop_loss < 98.0  # stop placed below sweep extreme


def test_continuation_detector():
    detector = ContinuationDetector()
    trend_state = MarketStateClassification(state=MarketState.TREND)

    facts = _base_facts(price=105.0)
    facts.structure.has_bos = True
    facts.structure.bias = "bullish"
    # Active demand OB at 104.5 - 105.5
    facts.zones.active_order_blocks = [
        CausalZone(zone_id="ob_1", kind="ob", top=105.5, bottom=104.5, active=True)
    ]

    cand = detector.detect(facts, trend_state)
    assert cand is not None
    assert cand.family == StrategyFamily.CONTINUATION
    assert cand.direction == TradeDirection.LONG
    assert cand.structural_entry == 105.0
    assert cand.stop_loss < 104.5


def test_breakout_retest_detector():
    detector = BreakoutRetestDetector()
    trend_state = MarketStateClassification(state=MarketState.EXPANSION)

    facts = _base_facts(price=101.0)
    facts.structure.displacement_detected = True
    facts.zones.active_breakers = [
        CausalZone(zone_id="brk_1", kind="breaker", top=101.2, bottom=100.8, active=True)
    ]

    cand = detector.detect(facts, trend_state)
    assert cand is not None
    assert cand.family == StrategyFamily.BREAKOUT_RETEST
    assert cand.direction == TradeDirection.LONG


def test_composite_setup_detector():
    composite = CompositeSetupDetector()
    range_state = MarketStateClassification(state=MarketState.RANGE)

    facts = _base_facts(price=109.5)
    best = composite.detect_best(facts, range_state)
    assert best is not None
    assert best.family == StrategyFamily.RANGE_FADE
