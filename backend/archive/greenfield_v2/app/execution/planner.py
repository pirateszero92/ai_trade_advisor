"""
Execution Planner (EXEC-001 to EXEC-006)
Determines timing and order type (MARKET_NOW, PLACE_LIMIT, WAIT, CANCEL).
Decouples opportunity validity from execution timing.
"""

from datetime import datetime, timezone
import math

from app.domain.execution import (
    ExecutionDecision,
    ExecutionIntent,
    ExecutionOrderType,
)
from app.domain.opportunity import Opportunity, OpportunityState, TradeDirection
from app.domain.reason_codes import ReasonCode
from app.market_data.quote import MarketQuote
from app.services.instrument_rules import InstrumentRules


class ExecutionPlanner:
    """Evaluates market vs limit execution for active Opportunities."""

    def __init__(
        self,
        minimum_executable_rr: float = 1.5,
        market_max_extension_atr: float = 0.25,
        no_chase_max_extension_atr: float = 0.60,
        fee_bps: float = 10.0,
        slippage_bps: float = 3.0,
    ) -> None:
        self.minimum_executable_rr = minimum_executable_rr
        self.market_max_extension_atr = market_max_extension_atr
        self.no_chase_max_extension_atr = no_chase_max_extension_atr
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps

    def calculate_quoted_rr(
        self,
        entry: float,
        stop: float,
        target: float,
        spread: float = 0.0,
    ) -> float:
        """Calculates friction-adjusted R:R taking fees, slippage, and spread into account."""
        cost_rate = (self.fee_bps * 2.0 + self.slippage_bps * 2.0) / 10_000.0
        execution_cost = (entry * cost_rate) + spread
        risk_dist = abs(entry - stop) + execution_cost
        reward_dist = max(0.0, abs(target - entry) - execution_cost)
        return round(reward_dist / max(risk_dist, 1e-12), 4)

    def plan(
        self,
        opportunity: Opportunity,
        quote: MarketQuote,
        atr: float,
        rules: InstrumentRules | None = None,
        now: datetime | None = None,
    ) -> ExecutionIntent:
        current_time = now or datetime.now(timezone.utc)

        # 1. Base Terminal/Invalid check (EXEC-006 CANCEL)
        if opportunity.is_terminal:
            return ExecutionIntent(
                opportunity_id=opportunity.opportunity_id,
                lineage_id=opportunity.lineage_id,
                decision=ExecutionDecision.CANCEL,
                quoted_price=quote.last,
                executable_entry=opportunity.structural_entry,
                stop_loss=opportunity.stop_loss,
                take_profit=opportunity.primary_target,
                structural_rr=opportunity.structural_rr,
                quoted_rr=0.0,
                spread_pct=quote.spread_pct,
                quote_age_seconds=quote.age_seconds(now=current_time),
                reason_code=ReasonCode.EXECUTION_CANCELLED,
                reasons=[f"Opportunity is terminal: {opportunity.state}"],
            )

        # 2. Quote Quality & Freshness Validation (DATA-003)
        valid_quote, q_reason, q_msg = quote.validate_executable(now=current_time)
        if not valid_quote:
            return ExecutionIntent(
                opportunity_id=opportunity.opportunity_id,
                lineage_id=opportunity.lineage_id,
                decision=ExecutionDecision.WAIT,
                quoted_price=quote.last,
                executable_entry=opportunity.structural_entry,
                stop_loss=opportunity.stop_loss,
                take_profit=opportunity.primary_target,
                structural_rr=opportunity.structural_rr,
                quoted_rr=0.0,
                spread_pct=quote.spread_pct,
                quote_age_seconds=quote.age_seconds(now=current_time),
                reason_code=q_reason,
                reasons=[q_msg],
            )

        # Determine executable price based on side (Taker: Long buys at Ask, Short sells at Bid)
        direction = opportunity.direction
        market_entry = quote.ask if direction == TradeDirection.LONG else quote.bid
        stop = opportunity.stop_loss
        target = opportunity.primary_target

        if rules is not None:
            market_entry = rules.price(market_entry, upward=(direction == TradeDirection.LONG))

        # Check geometry of executable entry
        if direction == TradeDirection.LONG:
            geometry_ok = stop < market_entry < target
        else:
            geometry_ok = target < market_entry < stop

        spread_val = abs(quote.ask - quote.bid)
        market_rr = self.calculate_quoted_rr(market_entry, stop, target, spread=spread_val) if geometry_ok else 0.0

        # Extension relative to structural entry in ATR units
        effective_atr = max(atr, market_entry * 0.001)
        if direction == TradeDirection.LONG:
            adverse_extension = max(0.0, market_entry - opportunity.structural_entry) / effective_atr
        else:
            adverse_extension = max(0.0, opportunity.structural_entry - market_entry) / effective_atr

        # 3. MARKET_NOW Eligibility (EXEC-003)
        if (
            geometry_ok
            and market_rr >= self.minimum_executable_rr
            and adverse_extension <= self.market_max_extension_atr
        ):
            return ExecutionIntent(
                opportunity_id=opportunity.opportunity_id,
                lineage_id=opportunity.lineage_id,
                decision=ExecutionDecision.MARKET_NOW,
                order_type=ExecutionOrderType.MARKET,
                quoted_price=quote.last,
                executable_entry=market_entry,
                stop_loss=stop,
                take_profit=target,
                structural_rr=opportunity.structural_rr,
                quoted_rr=market_rr,
                limit_rr=0.0,
                spread_pct=quote.spread_pct,
                quote_age_seconds=quote.age_seconds(now=current_time),
                adverse_extension_atr=round(adverse_extension, 3),
                reason_code=ReasonCode.MARKET_NOW_READY,
                reasons=["Market executable R:R and extension within tolerance"],
            )

        # 4. PLACE_LIMIT Eligibility (EXEC-004)
        # If market R:R is degraded OR price extended, but causal entry zone is active
        causal_zone = opportunity.entry_zone
        if causal_zone and causal_zone.active:
            limit_entry = opportunity.structural_entry
            if rules is not None:
                limit_entry = rules.price(limit_entry, upward=False)

            if direction == TradeDirection.LONG:
                limit_geo_ok = stop < limit_entry < target
            else:
                limit_geo_ok = target < limit_entry < stop

            limit_rr = self.calculate_quoted_rr(limit_entry, stop, target, spread=0.0) if limit_geo_ok else 0.0

            if limit_geo_ok and limit_rr >= self.minimum_executable_rr:
                return ExecutionIntent(
                    opportunity_id=opportunity.opportunity_id,
                    lineage_id=opportunity.lineage_id,
                    decision=ExecutionDecision.PLACE_LIMIT,
                    order_type=ExecutionOrderType.LIMIT,
                    quoted_price=quote.last,
                    executable_entry=limit_entry,
                    stop_loss=stop,
                    take_profit=target,
                    structural_rr=opportunity.structural_rr,
                    quoted_rr=market_rr,
                    limit_rr=limit_rr,
                    spread_pct=quote.spread_pct,
                    quote_age_seconds=quote.age_seconds(now=current_time),
                    adverse_extension_atr=round(adverse_extension, 3),
                    reason_code=ReasonCode.LIMIT_ARMED,
                    reasons=[
                        f"Market R:R ({market_rr:.2f}) or extension ({adverse_extension:.2f} ATR) unfavorable; "
                        f"armed causal limit {causal_zone.zone_id} at structural entry (Limit R:R {limit_rr:.2f})"
                    ],
                )

        # 5. WAIT State (EXEC-005)
        # Opportunity remains valid, but neither market nor limit is ready
        return ExecutionIntent(
            opportunity_id=opportunity.opportunity_id,
            lineage_id=opportunity.lineage_id,
            decision=ExecutionDecision.WAIT,
            order_type=None,
            quoted_price=quote.last,
            executable_entry=opportunity.structural_entry,
            stop_loss=stop,
            take_profit=target,
            structural_rr=opportunity.structural_rr,
            quoted_rr=market_rr,
            limit_rr=0.0,
            spread_pct=quote.spread_pct,
            quote_age_seconds=quote.age_seconds(now=current_time),
            adverse_extension_atr=round(adverse_extension, 3),
            reason_code=ReasonCode.WAITING_ENTRY,
            reasons=[
                f"Waiting for entry conditions: Market R:R {market_rr:.2f} < {self.minimum_executable_rr:.2f}, "
                f"Extension: {adverse_extension:.2f} ATR"
            ],
        )
