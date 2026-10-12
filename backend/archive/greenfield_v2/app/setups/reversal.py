"""
Reversal Setup Detector (SETUP-001)
Detects structural liquidity sweep reversals with causal zone reclaims.
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


class ReversalDetector:
    """Detects Reversal opportunities after confirmed liquidity sweeps."""

    def detect(
        self,
        facts: MarketFacts,
        state: MarketStateClassification,
    ) -> OpportunityCandidate | None:
        liq = facts.liquidity
        if not liq.liquidity_swept or liq.sweep_age_bars > 3:
            return None

        price = facts.current_price
        atr = facts.volatility.atr or (price * 0.01)

        # Bullish Reversal (Sell-Side Liquidity Swept)
        if liq.sweep_direction == "low" and liq.sweep_price is not None:
            if price < liq.sweep_price:
                return None  # Price has not reclaimed above sweep level

            sweep_low = liq.sweep_price
            stop = round(sweep_low - (0.20 * atr), 4)
            entry = round(price, 4)

            # Target opposing liquidity or premium boundary
            target = (
                round(facts.zones.premium_boundary, 4)
                if facts.zones.premium_boundary > entry
                else round(entry + (atr * 2.5), 4)
            )

            if not (stop < entry < target):
                return None

            return OpportunityCandidate(
                family=StrategyFamily.REVERSAL,
                direction=TradeDirection.LONG,
                thesis=f"Sell-side liquidity sweep at {sweep_low:.2f} reclaimed with bullish displacement",
                evidence=[
                    f"SSL swept at {sweep_low:.2f}",
                    "Price reclaimed above sweep extreme",
                    f"CVD divergence: {facts.flow.cvd_divergence}",
                ],
                entry_zone=CausalZone(
                    zone_id=f"sweep_ssl_{int(sweep_low)}",
                    kind="liquidity_pool",
                    top=entry,
                    bottom=sweep_low,
                    active=True,
                ),
                structural_entry=entry,
                stop_loss=stop,
                targets=[TargetLevel(price=target, label="opposing_liquidity")],
                invalidation=InvalidationCriteria(
                    price=stop,
                    reason="Price broke below sweep low invalidating reversal thesis",
                    condition="close_below",
                ),
                ttl_bars=2,
            )

        # Bearish Reversal (Buy-Side Liquidity Swept)
        if liq.sweep_direction == "high" and liq.sweep_price is not None:
            if price > liq.sweep_price:
                return None  # Price has not reclaimed below sweep level

            sweep_high = liq.sweep_price
            stop = round(sweep_high + (0.20 * atr), 4)
            entry = round(price, 4)

            target = (
                round(facts.zones.discount_boundary, 4)
                if 0 < facts.zones.discount_boundary < entry
                else round(entry - (atr * 2.5), 4)
            )

            if not (target < entry < stop):
                return None

            return OpportunityCandidate(
                family=StrategyFamily.REVERSAL,
                direction=TradeDirection.SHORT,
                thesis=f"Buy-side liquidity sweep at {sweep_high:.2f} reclaimed with bearish displacement",
                evidence=[
                    f"BSL swept at {sweep_high:.2f}",
                    "Price reclaimed below sweep extreme",
                    f"CVD divergence: {facts.flow.cvd_divergence}",
                ],
                entry_zone=CausalZone(
                    zone_id=f"sweep_bsl_{int(sweep_high)}",
                    kind="liquidity_pool",
                    top=sweep_high,
                    bottom=entry,
                    active=True,
                ),
                structural_entry=entry,
                stop_loss=stop,
                targets=[TargetLevel(price=target, label="opposing_liquidity")],
                invalidation=InvalidationCriteria(
                    price=stop,
                    reason="Price broke above sweep high invalidating reversal thesis",
                    condition="close_above",
                ),
                ttl_bars=2,
            )

        return None
