"""
Execution Intent Domain Model (CORE-004)
Represents the discrete, deterministic output of the Execution Planner.
Decouples setup validity from timing and order type decision.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field

from app.domain.reason_codes import ReasonCode


class ExecutionDecision(str, Enum):
    MARKET_NOW = "MARKET_NOW"
    PLACE_LIMIT = "PLACE_LIMIT"
    WAIT = "WAIT"
    CANCEL = "CANCEL"


class ExecutionOrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class ExecutionIntent(BaseModel):
    """
    The canonical output of the Execution Planner.
    Answers: HOW and WHEN do we enter this opportunity?
    """

    opportunity_id: str
    lineage_id: str

    decision: ExecutionDecision
    order_type: ExecutionOrderType | None = None

    quoted_price: float
    executable_entry: float
    stop_loss: float
    take_profit: float

    # 3-tier R:R tracking
    structural_rr: float
    quoted_rr: float
    limit_rr: float = 0.0

    # Market quality at execution planning time
    spread_pct: float
    quote_age_seconds: float
    adverse_extension_atr: float = 0.0

    reason_code: ReasonCode
    reasons: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_executable(self) -> bool:
        """True if the intent can be handed off to the Risk Allocator."""
        return self.decision in {ExecutionDecision.MARKET_NOW, ExecutionDecision.PLACE_LIMIT}
