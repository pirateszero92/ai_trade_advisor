"""
Continuation Setup Detector (SETUP-002)
Detects structural trend continuation retests of causal Order Blocks or FVGs.
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


class ContinuationDetector:
    """Detects trend continuation entries into unmitigated causal zones."""

    def detect(
        self,
        facts: MarketFacts,
        state: MarketStateClassification,
    ) -> OpportunityCandidate | None:
        if not facts.structure.has_bos:
            return None

        bias = facts.structure.bias
        if bias not in {"bullish", "bearish"}:
            return None

        price = facts.current_price
        atr = facts.volatility.atr or (price * 0.01)

        # Bullish Continuation
        if bias == "bullish":
            # Search for active demand OB or FVG below price
            causal_zone = None
            for z in facts.zones.active_order_blocks + facts.zones.active_fvgs:
                if z.bottom <= price <= z.top + (0.35 * atr):
                    causal_zone = z
                    break

            if not causal_zone:
                return None

            entry = round(price, 4)
            stop = round(causal_zone.bottom - (0.20 * atr), 4)
            target = round(entry + (atr * 2.5), 4)

            if not (stop < entry < target):
                return None

            return OpportunityCandidate(
                family=StrategyFamily.CONTINUATION,
                direction=TradeDirection.LONG,
                thesis=f"Bullish continuation retest of causal zone {causal_zone.zone_id} post-BOS",
                evidence=[
                    "Confirmed bullish BOS",
                    f"Retest of active {causal_zone.kind.upper()} zone",
                    f"Flow direction: {facts.flow.cvd_direction}",
                ],
                entry_zone=causal_zone,
                structural_entry=entry,
                stop_loss=stop,
                targets=[TargetLevel(price=target, label="trend_extension")],
                invalidation=InvalidationCriteria(
                    price=stop,
                    reason=f"Price broke below causal zone {causal_zone.zone_id}",
                    condition="close_below",
                ),
                ttl_bars=3,
            )

        # Bearish Continuation
        if bias == "bearish":
            causal_zone = None
            for z in facts.zones.active_order_blocks + facts.zones.active_fvgs:
                if z.bottom - (0.35 * atr) <= price <= z.top:
                    causal_zone = z
                    break

            if not causal_zone:
                return None

            entry = round(price, 4)
            stop = round(causal_zone.top + (0.20 * atr), 4)
            target = round(entry - (atr * 2.5), 4)

            if not (target < entry < stop):
                return None

            return OpportunityCandidate(
                family=StrategyFamily.CONTINUATION,
                direction=TradeDirection.SHORT,
                thesis=f"Bearish continuation retest of causal zone {causal_zone.zone_id} post-BOS",
                evidence=[
                    "Confirmed bearish BOS",
                    f"Retest of active {causal_zone.kind.upper()} zone",
                    f"Flow direction: {facts.flow.cvd_direction}",
                ],
                entry_zone=causal_zone,
                structural_entry=entry,
                stop_loss=stop,
                targets=[TargetLevel(price=target, label="trend_extension")],
                invalidation=InvalidationCriteria(
                    price=stop,
                    reason=f"Price broke above causal zone {causal_zone.zone_id}",
                    condition="close_above",
                ),
                ttl_bars=3,
            )

        return None
