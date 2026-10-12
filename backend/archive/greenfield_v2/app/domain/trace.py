"""
Decision Trace Contract (CORE-008)
Immutable, append-only audit trail recording every critical fact and state transition.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

from app.domain.reason_codes import ReasonCode


class TraceStage(str, Enum):
    MARKET_DATA = "MARKET_DATA"
    DATA_QUALITY = "DATA_QUALITY"
    FACTS = "FACTS"
    MARKET_STATE = "MARKET_STATE"
    SETUP_DETECTED = "SETUP_DETECTED"
    OPPORTUNITY_CREATED = "OPPORTUNITY_CREATED"
    OPPORTUNITY_STATE_CHANGE = "OPPORTUNITY_STATE_CHANGE"
    AI_REVIEW = "AI_REVIEW"
    EXECUTION_PLAN = "EXECUTION_PLAN"
    RISK_PRECHECK = "RISK_PRECHECK"
    RISK_FINAL = "RISK_FINAL"
    OMS_SUBMIT = "OMS_SUBMIT"
    OMS_RESULT = "OMS_RESULT"
    POSITION_UPDATE = "POSITION_UPDATE"
    EXIT = "EXIT"


class TraceRecord(BaseModel):
    """An immutable audit record for the decision trace log."""

    trace_id: str
    opportunity_id: str
    lineage_id: str
    stage: TraceStage
    reason_code: ReasonCode
    facts: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
