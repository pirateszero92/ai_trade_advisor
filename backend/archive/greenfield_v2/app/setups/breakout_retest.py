"""
Breakout Retest Setup Detector (SETUP-004)
Detects confirmed displacement breakouts followed by controlled structural retests.
Never chases breakout candles directly; strictly awaits the retest.
"""

from app.domain.opportunity import (
    CausalZone,
    InvalidationCriteria,
    StrategyFamily,
    TargetLevel,
    TradeDirection,
)
from app.facts.models import MarketFacts
from app.market_state.classifier import MarketStateClassification
from app.setups.candidate import OpportunityCandidate


class BreakoutRetestDetector:
    """Detects Breakout Retest entries into breaker zones post-displacement."""

    def detect(
        self,
        facts: MarketFacts,
        state: MarketStateClassification,
    ) -> OpportunityCandidate | None:
        if not facts.structure.displacement_detected:
            return None

        # Must have active breaker block formed by breakout
        if not facts.zones.active_breakers:
            return None

        price = facts.current_price
        atr = facts.volatility.atr or (price * 0.01)

        for breaker in facts.zones.active_breakers:
            # Bullish Breaker Retest (prior supply broken and flipped to demand)
            if breaker.bottom <= price <= breaker.top + (0.25 * atr):
                entry = round(price, 4)
                stop = round(breaker.bottom - (0.20 * atr), 4)
                target = round(entry + (atr * 2.5), 4)

                if stop < entry < target:
                    return OpportunityCandidate(
                        family=StrategyFamily.BREAKOUT_RETEST,
                        direction=TradeDirection.LONG,
                        thesis=f"Bullish retest of broken structure / breaker zone {breaker.zone_id}",
                        evidence=[
                            "Displacement breakout confirmed",
                            f"Breaker retest at {breaker.top:.2f}",
                            f"Flow aligned: {facts.flow.cvd_direction}",
                        ],
                        entry_zone=breaker,
                        structural_entry=entry,
                        stop_loss=stop,
                        targets=[TargetLevel(price=target, label="breakout_continuation")],
                        invalidation=InvalidationCriteria(
                            price=stop,
                            reason=f"Price failed and closed below breaker zone {breaker.zone_id}",
                            condition="close_below",
                        ),
                        ttl_bars=2,
                    )

            # Bearish Breaker Retest (prior demand broken and flipped to supply)
            if breaker.bottom - (0.25 * atr) <= price <= breaker.top:
                entry = round(price, 4)
                stop = round(breaker.top + (0.20 * atr), 4)
                target = round(entry - (atr * 2.5), 4)

                if target < entry < stop:
                    return OpportunityCandidate(
                        family=StrategyFamily.BREAKOUT_RETEST,
                        direction=TradeDirection.SHORT,
                        thesis=f"Bearish retest of broken structure / breaker zone {breaker.zone_id}",
                        evidence=[
                            "Displacement breakout confirmed",
                            f"Breaker retest at {breaker.bottom:.2f}",
                            f"Flow aligned: {facts.flow.cvd_direction}",
                        ],
                        entry_zone=breaker,
                        structural_entry=entry,
                        stop_loss=stop,
                        targets=[TargetLevel(price=target, label="breakout_continuation")],
                        invalidation=InvalidationCriteria(
                            price=stop,
                            reason=f"Price failed and closed above breaker zone {breaker.zone_id}",
                            condition="close_above",
                        ),
                        ttl_bars=2,
                    )

        return None
