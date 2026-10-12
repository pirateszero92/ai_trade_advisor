"""
Idempotent Paper Order Management System (OMS-001 to OMS-005)
Owns order creation, idempotent lifecycle transitions, pending limit revalidation,
and execution fill simulation with realistic modeled friction.
"""

from datetime import datetime, timezone
from typing import Any

from app.domain.clock import Clock, SystemClock
from app.domain.execution import ExecutionIntent, ExecutionOrderType
from app.domain.ids import generate_order_id, generate_position_id
from app.domain.opportunity import Opportunity, OpportunityState, TradeDirection
from app.domain.order import Order, OrderSide, OrderStatus
from app.domain.position import Position, PositionStatus
from app.domain.reason_codes import ReasonCode
from app.domain.risk import RiskAssessment, RiskStage
from app.facts.models import MarketFacts
from app.market_data.quote import MarketQuote
from app.risk.allocator import RiskAllocator


class PaperOMS:
    """Authoritative Order Management System for Paper Execution."""

    def __init__(
        self,
        clock: Clock | None = None,
        risk_allocator: RiskAllocator | None = None,
        fee_bps: float = 10.0,
        slippage_bps: float = 3.0,
    ) -> None:
        self.clock = clock or SystemClock()
        self.risk_allocator = risk_allocator or RiskAllocator()
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps

        self._orders: dict[str, Order] = {}
        self._idempotency_map: dict[str, str] = {}  # idempotency_key -> client_order_id
        self._positions: dict[str, Position] = {}
        self._pending_limits: dict[str, Opportunity] = {}  # client_order_id -> Opportunity

    @property
    def orders(self) -> dict[str, Order]:
        return self._orders

    @property
    def positions(self) -> dict[str, Position]:
        return self._positions

    def submit_order(
        self,
        opportunity: Opportunity,
        intent: ExecutionIntent,
        risk: RiskAssessment,
        idempotency_key: str,
    ) -> Order:
        """
        Submits an order idempotently. If the idempotency key already exists,
        returns the existing order without creating a duplicate.
        """
        if not risk.approved:
            raise ValueError(f"Cannot submit order without approved risk assessment: {risk.rejection_reasons}")

        # Idempotency check (OMS-002)
        if idempotency_key in self._idempotency_map:
            existing_id = self._idempotency_map[idempotency_key]
            return self._orders[existing_id]

        now = self.clock.now()
        side = OrderSide.BUY if opportunity.direction == TradeDirection.LONG else OrderSide.SELL
        order_type_str = "market" if intent.order_type == ExecutionOrderType.MARKET else "limit"

        order_id = generate_order_id(opportunity.opportunity_id, side.value)

        order = Order(
            client_order_id=order_id,
            opportunity_id=opportunity.opportunity_id,
            lineage_id=opportunity.lineage_id,
            symbol=opportunity.symbol,
            side=side,
            order_type=order_type_str,
            status=OrderStatus.CREATED,
            requested_quantity=risk.position_size,
            limit_price=intent.executable_entry if order_type_str == "limit" else None,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )

        # Transition to PENDING
        order.transition(OrderStatus.PENDING, ReasonCode.ORDER_SUBMITTED)

        self._orders[order_id] = order
        self._idempotency_map[idempotency_key] = order_id

        if order_type_str == "limit":
            # Track pending limit order for closed-candle revalidation (OMS-003)
            self._pending_limits[order_id] = opportunity
            if opportunity.can_transition_to(OpportunityState.ORDER_PENDING):
                opportunity.transition(OpportunityState.ORDER_PENDING, ReasonCode.ORDER_SUBMITTED)
        else:
            # Market order is immediately accepted by the execution venue
            order.transition(OrderStatus.ACCEPTED, ReasonCode.ORDER_ACCEPTED)

        return order

    def execute_fill(
        self,
        order_id: str,
        fill_price: float,
        quote: MarketQuote | None = None,
    ) -> Position:
        """
        Fills an active order and instantiates a managed Position (OMS-005).
        """
        order = self._orders.get(order_id)
        if not order:
            raise KeyError(f"Order {order_id} not found")

        if order.status not in {OrderStatus.PENDING, OrderStatus.ACCEPTED}:
            raise ValueError(f"Order {order_id} in status {order.status} cannot be filled")

        now = self.clock.now()
        order.filled_quantity = order.requested_quantity
        order.average_fill_price = fill_price
        order.transition(OrderStatus.FILLED, ReasonCode.ORDER_FILLED)

        # Remove from pending limits if was pending
        self._pending_limits.pop(order_id, None)

        # Calculate modeled fee
        notional = fill_price * order.filled_quantity
        fee = notional * (self.fee_bps / 10_000.0)

        pos_id = generate_position_id(order.opportunity_id)
        position = Position(
            position_id=pos_id,
            opportunity_id=order.opportunity_id,
            lineage_id=order.lineage_id,
            symbol=order.symbol,
            direction="long" if order.side == OrderSide.BUY else "short",
            status=PositionStatus.OPEN,
            quantity=order.filled_quantity,
            average_entry_price=fill_price,
            current_stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            fees_total=round(fee, 4),
            opened_at=now,
        )

        self._positions[pos_id] = position
        return position

    def revalidate_pending_limits(
        self,
        facts_by_symbol: dict[str, MarketFacts],
        daily_pnl_pct: float = 0.0,
    ) -> list[str]:
        """
        Revalidates all pending limit orders on closed candle (Section 27, OMS-004).
        Cancels orders whose causal zone mitigated, thesis invalidated, or TTL expired.
        Returns list of cancelled order IDs.
        """
        cancelled_orders = []
        now = self.clock.now()

        for order_id, opportunity in list(self._pending_limits.items()):
            order = self._orders.get(order_id)
            if not order or order.status != OrderStatus.PENDING:
                self._pending_limits.pop(order_id, None)
                continue

            symbol = opportunity.symbol
            facts = facts_by_symbol.get(symbol)
            if not facts:
                continue

            cancel_reason: ReasonCode | None = None

            # 1. Check TTL
            if now >= opportunity.valid_until:
                cancel_reason = ReasonCode.ORDER_EXPIRED

            # 2. Check Daily Risk Halt
            elif daily_pnl_pct <= -self.risk_allocator.max_daily_loss_pct:
                cancel_reason = ReasonCode.DAILY_LOSS_LIMIT

            # 3. Check Structural Invalidation
            elif opportunity.invalidation.condition == "close_above" and facts.current_price > opportunity.invalidation.price:
                cancel_reason = ReasonCode.STRUCTURE_INVALIDATED
            elif opportunity.invalidation.condition == "close_below" and facts.current_price < opportunity.invalidation.price:
                cancel_reason = ReasonCode.STRUCTURE_INVALIDATED

            # 4. Check Zone Viability
            elif opportunity.entry_zone and not opportunity.entry_zone.active:
                cancel_reason = ReasonCode.ZONE_INVALIDATED

            if cancel_reason is not None:
                order.transition(OrderStatus.CANCELLED, cancel_reason)
                self._pending_limits.pop(order_id, None)
                if opportunity.can_transition_to(OpportunityState.CANCELLED):
                    opportunity.transition(OpportunityState.CANCELLED, cancel_reason)
                cancelled_orders.append(order_id)

        return cancelled_orders

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_position(self, position_id: str) -> Position | None:
        return self._positions.get(position_id)

    def get_open_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.status == PositionStatus.OPEN]
