"""
Risk Assessment Domain Model (CORE-005)
Defines the portfolio risk acceptance contracts for both Pre-check and Final assessment.
"""

from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field

from app.domain.reason_codes import ReasonCode


class RiskStage(str, Enum):
    PRE_CHECK = "PRE_CHECK"      # Evaluated when arming a pending limit order
    FINAL_CHECK = "FINAL_CHECK"  # Evaluated immediately before order submission or fill


class RiskAssessment(BaseModel):
    """
    Portfolio-aware risk evaluation and sizing result.
    Risk authority owns portfolio acceptance and sizing.
    """

    opportunity_id: str
    lineage_id: str
    stage: RiskStage

    approved: bool
    allocated_risk_pct: float = 0.0
    risk_budget_usd: float = 0.0

    position_size: float = 0.0
    position_notional: float = 0.0

    account_equity: float
    daily_pnl_pct: float = 0.0
    open_positions_count: int = 0
    cluster_positions_count: int = 0

    reason_code: ReasonCode
    rejection_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
