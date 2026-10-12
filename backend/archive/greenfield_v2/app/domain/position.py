"""
Position Domain Model (CORE-006)
Defines post-fill trade ownership and lifecycle managed by Position Manager.
"""

from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field

from app.domain.reason_codes import ReasonCode


class PositionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class ProtectionStage(str, Enum):
    INITIAL = "initial"
    BREAKEVEN = "breakeven"
    TRAILING = "trailing"
    PARTIAL_TP1 = "partial_tp1"


class Position(BaseModel):
    """Authoritative Position object managed post-fill."""

    position_id: str
    opportunity_id: str
    lineage_id: str

    symbol: str
    direction: str  # long | short

    status: PositionStatus = PositionStatus.OPEN
    protection_stage: ProtectionStage = ProtectionStage.INITIAL

    quantity: float
    average_entry_price: float

    current_stop_loss: float
    take_profit: float

    realized_pnl_gross: float = 0.0
    realized_pnl_net: float = 0.0
    fees_total: float = 0.0

    opened_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None
    close_reason: ReasonCode | None = None

    def close(self, exit_price: float, net_pnl: float, reason: ReasonCode) -> None:
        self.status = PositionStatus.CLOSED
        self.closed_at = datetime.now(timezone.utc)
        self.close_reason = reason
        self.realized_pnl_net = net_pnl
