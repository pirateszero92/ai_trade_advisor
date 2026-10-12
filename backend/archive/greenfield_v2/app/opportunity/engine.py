"""
Opportunity Engine (OPP-001, OPP-003, OPP-004, OPP-005)
Owns Opportunity identity, lifecycle state transitions, TTL expiration,
and structural invalidation across closed candles.
"""

from datetime import datetime, timezone, timedelta
from typing import Any

from app.domain.clock import Clock, SystemClock
from app.domain.ids import generate_lineage_id, generate_opportunity_id
from app.domain.opportunity import Opportunity, OpportunityState
from app.domain.reason_codes import ReasonCode
from app.facts.models import MarketFacts
from app.opportunity.store import OpportunityStore
from app.setups.candidate import OpportunityCandidate


class OpportunityEngine:
    """Manages the lifecycle of persistent Opportunities."""

    def __init__(self, store: OpportunityStore | None = None, clock: Clock | None = None) -> None:
        self.store = store or OpportunityStore()
        self.clock = clock or SystemClock()

    def create_from_candidate(
        self,
        candidate: OpportunityCandidate,
        symbol: str,
        timeframe: str = "15m",
        market_type: str = "crypto",
        exchange: str = "binance",
        data_quality: dict[str, Any] | None = None,
        market_state: dict[str, Any] | None = None,
    ) -> Opportunity:
        """Creates and stores a new persistent Opportunity from a detected candidate."""
        now = self.clock.now()
        # 15m default bar duration
        bar_duration = 900 if timeframe == "15m" else 3600
        valid_until = now + timedelta(seconds=bar_duration * candidate.ttl_bars)

        opp_id = generate_opportunity_id(symbol, timeframe, now)
        lin_id = generate_lineage_id(symbol, timeframe, candidate.family.value)

        opportunity = Opportunity(
            opportunity_id=opp_id,
            lineage_id=lin_id,
            symbol=symbol,
            market_type=market_type,
            exchange=exchange,
            timeframe=timeframe,
            direction=candidate.direction,
            family=candidate.family,
            thesis=candidate.thesis,
            evidence=candidate.evidence,
            entry_zone=candidate.entry_zone,
            structural_entry=candidate.structural_entry,
            stop_loss=candidate.stop_loss,
            targets=candidate.targets,
            invalidation=candidate.invalidation,
            state=OpportunityState.DETECTED,
            state_reason=ReasonCode.OPPORTUNITY_DETECTED,
            created_at=now,
            valid_until=valid_until,
            ttl_bars=candidate.ttl_bars,
            data_quality=data_quality or {},
            market_state=market_state or {},
        )

        self.store.save(opportunity)
        return opportunity

    def revalidate(self, opportunity: Opportunity, facts: MarketFacts) -> Opportunity:
        """
        Revalidates an active opportunity against closed-candle facts.
        Checks TTL, invalidation levels, and zone viability.
        """
        if opportunity.is_terminal:
            return opportunity

        now = self.clock.now()

        # 1. TTL Expiration Check (OPP-004)
        if now >= opportunity.valid_until:
            if opportunity.state in {
                OpportunityState.DETECTED,
                OpportunityState.VALIDATED,
                OpportunityState.WAITING_ENTRY,
                OpportunityState.LIMIT_ARMED,
            }:
                opportunity.transition(OpportunityState.EXPIRED, ReasonCode.OPPORTUNITY_EXPIRED)
                self.store.save(opportunity)
                return opportunity

        price = facts.current_price
        inv = opportunity.invalidation

        # 2. Structural Invalidation Check (OPP-005)
        is_invalidated = False
        if inv.condition == "close_above" and price > inv.price:
            is_invalidated = True
        elif inv.condition == "close_below" and price < inv.price:
            is_invalidated = True

        if is_invalidated:
            if opportunity.can_transition_to(OpportunityState.INVALIDATED):
                opportunity.transition(OpportunityState.INVALIDATED, ReasonCode.STRUCTURE_INVALIDATED)
                self.store.save(opportunity)
                return opportunity

        # 3. Validation Progression
        if opportunity.state == OpportunityState.DETECTED:
            opportunity.transition(OpportunityState.VALIDATED, ReasonCode.OPPORTUNITY_VALIDATED)
            self.store.save(opportunity)

        return opportunity
