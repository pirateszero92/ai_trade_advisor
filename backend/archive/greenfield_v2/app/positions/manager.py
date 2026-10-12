"""
Position Manager (POS-001 to POS-003)
Owns active trade management post-fill: initial protection, break-even advancement,
trailing stops, and deterministic exit triggers.
"""

from datetime import datetime, timezone

from app.domain.clock import Clock, SystemClock
from app.domain.opportunity import StrategyFamily, TradeDirection
from app.domain.position import Position, PositionStatus, ProtectionStage
from app.domain.reason_codes import ReasonCode
from app.facts.models import MarketFacts
from app.market_data.quote import MarketQuote


class PositionManager:
    """Manages open positions, stop updates, break-even advances, and exits."""

    def __init__(
        self,
        clock: Clock | None = None,
        auto_be_trigger_r: float = 1.0,
        auto_be_buffer_pct: float = 0.001,
    ) -> None:
        self.clock = clock or SystemClock()
        self.auto_be_trigger_r = auto_be_trigger_r
        self.auto_be_buffer_pct = auto_be_buffer_pct

    def update_position(
        self,
        position: Position,
        quote: MarketQuote,
        family: StrategyFamily = StrategyFamily.REVERSAL,
        facts: MarketFacts | None = None,
    ) -> Position:
        """
        Evaluates real-time price against position protection rules:
        - Check stop-loss hit
        - Check take-profit hit
        - Check break-even progression (at >= 1.0 R)
        - Strategy-family specific management
        """
        if position.status == PositionStatus.CLOSED:
            return position

        is_long = position.direction.lower() == "long"
        current_price = quote.bid if is_long else quote.ask
        entry = position.average_entry_price
        initial_risk_dist = abs(entry - position.current_stop_loss)

        # 1. Stop Loss Hit
        if (is_long and current_price <= position.current_stop_loss) or (
            not is_long and current_price >= position.current_stop_loss
        ):
            pnl = (current_price - entry) * position.quantity if is_long else (entry - current_price) * position.quantity
            net_pnl = pnl - position.fees_total
            position.close(exit_price=current_price, net_pnl=round(net_pnl, 2), reason=ReasonCode.STOP_LOSS_HIT)
            return position

        # 2. Take Profit Hit
        if (is_long and current_price >= position.take_profit) or (
            not is_long and current_price <= position.take_profit
        ):
            pnl = (current_price - entry) * position.quantity if is_long else (entry - current_price) * position.quantity
            net_pnl = pnl - position.fees_total
            position.close(exit_price=current_price, net_pnl=round(net_pnl, 2), reason=ReasonCode.TAKE_PROFIT_HIT)
            return position

        # 3. Auto Break-Even Advancement (POS-002)
        if position.protection_stage == ProtectionStage.INITIAL and initial_risk_dist > 0:
            current_r = (
                (current_price - entry) / initial_risk_dist
                if is_long
                else (entry - current_price) / initial_risk_dist
            )

            if current_r >= self.auto_be_trigger_r:
                # Advance stop to entry + small buffer to cover fees
                buffer = entry * self.auto_be_buffer_pct
                new_stop = entry + buffer if is_long else entry - buffer
                position.current_stop_loss = round(new_stop, 4)
                position.protection_stage = ProtectionStage.BREAKEVEN

        return position
