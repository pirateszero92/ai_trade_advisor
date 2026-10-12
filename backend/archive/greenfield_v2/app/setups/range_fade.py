"""
Range Fade Setup Detector (SETUP-003)
Detects structural mean-reversion fades at validated range boundaries.
"""

from app.domain.opportunity import (
    CausalZone,
    InvalidationCriteria,
    StrategyFamily,
    TargetLevel,
    TradeDirection,
)
from app.facts.models import MarketFacts
from app.market_state.classifier import MarketState, MarketStateClassification
from app.setups.candidate import OpportunityCandidate


class RangeFadeDetector:
    """Detects Range Fade opportunities at premium/discount extremes."""

    def detect(
        self,
        facts: MarketFacts,
        state: MarketStateClassification,
    ) -> OpportunityCandidate | None:
        # Range fade requires explicit range context or active boundaries
        if state.state == MarketState.TREND:
            return None

        zones = facts.zones
        p_top = zones.premium_boundary
        d_bot = zones.discount_boundary
        price = facts.current_price
        atr = facts.volatility.atr or (price * 0.01)

        if p_top <= 0 or d_bot <= 0 or p_top <= d_bot:
            return None

        # Short Range Fade (Testing Premium Boundary)
        if price >= p_top - (0.35 * atr):
            # Verify flow is not strongly opposing (e.g. explosive buyer flow)
            if facts.flow.cvd_direction == "bullish" and facts.flow.current_delta > (atr * 100):
                return None  # Flow strongly opposing fade

            stop = round(p_top + (0.25 * atr), 4)
            entry = round(price, 4)
            target = round(d_bot + (0.15 * atr), 4)

            if not (target < entry < stop):
                return None

            return OpportunityCandidate(
                family=StrategyFamily.RANGE_FADE,
                direction=TradeDirection.SHORT,
                thesis=f"Testing premium range boundary ({p_top:.2f}) with fade toward discount ({d_bot:.2f})",
                evidence=[
                    "Price at premium range boundary",
                    "Range market environment",
                    f"CVD flow: {facts.flow.cvd_direction}",
                ],
                entry_zone=CausalZone(
                    zone_id="range_premium_boundary",
                    kind="boundary",
                    top=p_top + (0.1 * atr),
                    bottom=p_top - (0.1 * atr),
                    active=True,
                ),
                structural_entry=entry,
                stop_loss=stop,
                targets=[TargetLevel(price=target, label="discount_boundary")],
                invalidation=InvalidationCriteria(
                    price=stop,
                    reason="Price closed above premium range boundary",
                    condition="close_above",
                ),
                ttl_bars=3,
            )

        # Long Range Fade (Testing Discount Boundary)
        if price <= d_bot + (0.35 * atr):
            if facts.flow.cvd_direction == "bearish" and facts.flow.current_delta < -(atr * 100):
                return None

            stop = round(d_bot - (0.25 * atr), 4)
            entry = round(price, 4)
            target = round(p_top - (0.15 * atr), 4)

            if not (stop < entry < target):
                return None

            return OpportunityCandidate(
                family=StrategyFamily.RANGE_FADE,
                direction=TradeDirection.LONG,
                thesis=f"Testing discount range boundary ({d_bot:.2f}) with fade toward premium ({p_top:.2f})",
                evidence=[
                    "Price at discount range boundary",
                    "Range market environment",
                    f"CVD flow: {facts.flow.cvd_direction}",
                ],
                entry_zone=CausalZone(
                    zone_id="range_discount_boundary",
                    kind="boundary",
                    top=d_bot + (0.1 * atr),
                    bottom=d_bot - (0.1 * atr),
                    active=True,
                ),
                structural_entry=entry,
                stop_loss=stop,
                targets=[TargetLevel(price=target, label="premium_boundary")],
                invalidation=InvalidationCriteria(
                    price=stop,
                    reason="Price closed below discount range boundary",
                    condition="close_below",
                ),
                ttl_bars=3,
            )

        return None
