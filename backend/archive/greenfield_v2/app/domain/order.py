"""
OMS Order Domain Model & State Machine (CORE-006)
Defines idempotent order lifecycle contracts owned exclusively by the OMS.
"""

from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field

from app.domain.reason_codes import ReasonCode


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REPLACED = "REPLACED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


VALID_ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {
        OrderStatus.PENDING,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.PENDING: {
        OrderStatus.ACCEPTED,
        OrderStatus.PARTIAL,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
    },
    OrderStatus.ACCEPTED: {
        OrderStatus.PARTIAL,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.CANCELLED,
        OrderStatus.REPLACED,
    },
    OrderStatus.PARTIAL: {
        OrderStatus.FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.CANCELLED,
    },
    OrderStatus.CANCEL_PENDING: {
        OrderStatus.CANCELLED,
        OrderStatus.FILLED,  # In flight fill race condition
    },
    OrderStatus.FILLED: {
        OrderStatus.CLOSED,
    },
    # Terminal states
    OrderStatus.CANCELLED: set(),
    OrderStatus.REPLACED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.CLOSED: set(),
}


class Order(BaseModel):
    """Authoritative Order object managed by OMS."""

    client_order_id: str
    opportunity_id: str
    lineage_id: str

    symbol: str
    side: OrderSide
    order_type: str = "market"  # market | limit

    status: OrderStatus = OrderStatus.CREATED
    state_reason: ReasonCode = ReasonCode.ORDER_CREATED

    requested_quantity: float
    filled_quantity: float = 0.0

    limit_price: float | None = None
    average_fill_price: float | None = None

    stop_loss: float
    take_profit: float

    idempotency_key: str

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def can_transition_to(self, new_status: OrderStatus) -> bool:
        allowed = VALID_ORDER_TRANSITIONS.get(self.status, set())
        return new_status in allowed

    def transition(self, new_status: OrderStatus, reason: ReasonCode) -> None:
        if not self.can_transition_to(new_status):
            raise ValueError(
                f"Invalid order state transition: {self.status} -> {new_status} "
                f"for order {self.client_order_id}"
            )
        self.status = new_status
        self.state_reason = reason
        self.updated_at = datetime.now(timezone.utc)
