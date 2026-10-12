"""
Opportunity Domain Model & State Machine (CORE-002, CORE-003)
The central entity of the opportunity-centric trading architecture.
Replaces ephemeral indicators/signals with a persistent, lifecycle-managed Opportunity.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.reason_codes import ReasonCode


class TradeDirection(str, Enum):
    LONG = "long"
    SHORT = "short"


class StrategyFamily(str, Enum):
    REVERSAL = "reversal"
    CONTINUATION = "continuation"
    RANGE_FADE = "range_fade"
    BREAKOUT_RETEST = "breakout_retest"


class OpportunityState(str, Enum):
    # Discovery & Preparation
    DETECTED = "DETECTED"
    VALIDATED = "VALIDATED"
    WAITING_ENTRY = "WAITING_ENTRY"
    MARKET_READY = "MARKET_READY"
    LIMIT_ARMED = "LIMIT_ARMED"

    # Risk & OMS Transition
    RISK_PRECHECK = "RISK_PRECHECK"
    ORDER_PENDING = "ORDER_PENDING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"

    # Execution & Settlement
    MANAGED = "MANAGED"
    CLOSED = "CLOSED"

    # Terminal / Negative States
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    RISK_REJECTED = "RISK_REJECTED"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"
    CANCELLED = "CANCELLED"


# Valid state machine transitions (CORE-003)
VALID_TRANSITIONS: dict[OpportunityState, set[OpportunityState]] = {
    OpportunityState.DETECTED: {
        OpportunityState.VALIDATED,
        OpportunityState.EXPIRED,
        OpportunityState.INVALIDATED,
        OpportunityState.CANCELLED,
    },
    OpportunityState.VALIDATED: {
        OpportunityState.WAITING_ENTRY,
        OpportunityState.MARKET_READY,
        OpportunityState.LIMIT_ARMED,
        OpportunityState.EXPIRED,
        OpportunityState.INVALIDATED,
        OpportunityState.CANCELLED,
    },
    OpportunityState.WAITING_ENTRY: {
        OpportunityState.MARKET_READY,
        OpportunityState.LIMIT_ARMED,
        OpportunityState.EXPIRED,
        OpportunityState.INVALIDATED,
        OpportunityState.CANCELLED,
    },
    OpportunityState.MARKET_READY: {
        OpportunityState.RISK_PRECHECK,
        OpportunityState.ORDER_PENDING,
        OpportunityState.WAITING_ENTRY,
        OpportunityState.LIMIT_ARMED,
        OpportunityState.EXECUTION_REJECTED,
        OpportunityState.INVALIDATED,
        OpportunityState.CANCELLED,
    },
    OpportunityState.LIMIT_ARMED: {
        OpportunityState.RISK_PRECHECK,
        OpportunityState.ORDER_PENDING,
        OpportunityState.WAITING_ENTRY,
        OpportunityState.MARKET_READY,
        OpportunityState.EXPIRED,
        OpportunityState.INVALIDATED,
        OpportunityState.CANCELLED,
    },
    OpportunityState.RISK_PRECHECK: {
        OpportunityState.ORDER_PENDING,
        OpportunityState.RISK_REJECTED,
        OpportunityState.WAITING_ENTRY,
        OpportunityState.CANCELLED,
    },
    OpportunityState.ORDER_PENDING: {
        OpportunityState.PARTIAL,
        OpportunityState.FILLED,
        OpportunityState.CANCELLED,
        OpportunityState.EXPIRED,
    },
    OpportunityState.PARTIAL: {
        OpportunityState.FILLED,
        OpportunityState.MANAGED,
        OpportunityState.CANCELLED,
    },
    OpportunityState.FILLED: {
        OpportunityState.MANAGED,
        OpportunityState.CLOSED,
    },
    OpportunityState.MANAGED: {
        OpportunityState.CLOSED,
    },
    # Terminal states have no valid outgoing transitions
    OpportunityState.CLOSED: set(),
    OpportunityState.INVALIDATED: set(),
    OpportunityState.EXPIRED: set(),
    OpportunityState.RISK_REJECTED: set(),
    OpportunityState.EXECUTION_REJECTED: set(),
    OpportunityState.CANCELLED: set(),
}


class TargetLevel(BaseModel):
    price: float
    ratio: float = 1.0
    label: str = "primary"


class CausalZone(BaseModel):
    zone_id: str
    kind: Literal["ob", "fvg", "breaker", "boundary", "liquidity_pool"]
    top: float
    bottom: float
    active: bool = True

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0


class InvalidationCriteria(BaseModel):
    price: float
    reason: str
    condition: Literal["close_above", "close_below", "zone_mitigated", "ttl_elapsed"]


class Opportunity(BaseModel):
    """Authoritative Opportunity domain object."""

    opportunity_id: str
    lineage_id: str

    symbol: str
    market_type: str = "crypto"
    exchange: str = "binance"
    timeframe: str = "15m"

    direction: TradeDirection
    family: StrategyFamily

    thesis: str
    evidence: list[str] = Field(default_factory=list)

    entry_zone: CausalZone | None = None
    structural_entry: float
    stop_loss: float
    targets: list[TargetLevel] = Field(default_factory=list)

    invalidation: InvalidationCriteria

    state: OpportunityState = OpportunityState.DETECTED
    state_reason: ReasonCode = ReasonCode.OPPORTUNITY_DETECTED

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until: datetime
    ttl_bars: int = 2

    data_quality: dict[str, Any] = Field(default_factory=dict)
    market_state: dict[str, Any] = Field(default_factory=dict)

    version: int = 1

    @property
    def primary_target(self) -> float:
        if self.targets:
            return self.targets[0].price
        return 0.0

    @property
    def structural_rr(self) -> float:
        risk_dist = abs(self.structural_entry - self.stop_loss)
        if risk_dist <= 0:
            return 0.0
        reward_dist = abs(self.primary_target - self.structural_entry)
        return round(reward_dist / risk_dist, 4)

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            OpportunityState.CLOSED,
            OpportunityState.INVALIDATED,
            OpportunityState.EXPIRED,
            OpportunityState.RISK_REJECTED,
            OpportunityState.EXECUTION_REJECTED,
            OpportunityState.CANCELLED,
        }

    @model_validator(mode="after")
    def validate_geometry(self) -> "Opportunity":
        """Strict structural geometry validation at instantiation."""
        if self.direction == TradeDirection.LONG:
            if not (self.stop_loss < self.structural_entry):
                raise ValueError(
                    f"LONG geometry invalid: stop_loss ({self.stop_loss}) must be below entry ({self.structural_entry})"
                )
            if self.primary_target > 0 and not (self.structural_entry < self.primary_target):
                raise ValueError(
                    f"LONG geometry invalid: target ({self.primary_target}) must be above entry ({self.structural_entry})"
                )
        elif self.direction == TradeDirection.SHORT:
            if not (self.structural_entry < self.stop_loss):
                raise ValueError(
                    f"SHORT geometry invalid: entry ({self.structural_entry}) must be below stop_loss ({self.stop_loss})"
                )
            if self.primary_target > 0 and not (self.primary_target < self.structural_entry):
                raise ValueError(
                    f"SHORT geometry invalid: target ({self.primary_target}) must be below entry ({self.structural_entry})"
                )
        return self

    def can_transition_to(self, new_state: OpportunityState) -> bool:
        """Checks if a transition from current state to new_state is allowed."""
        allowed = VALID_TRANSITIONS.get(self.state, set())
        return new_state in allowed

    def transition(self, new_state: OpportunityState, reason: ReasonCode) -> None:
        """Transitions state or raises InvalidStateTransition error."""
        if not self.can_transition_to(new_state):
            raise ValueError(
                f"Invalid opportunity state transition: {self.state} -> {new_state} "
                f"for opportunity {self.opportunity_id}"
            )
        self.state = new_state
        self.state_reason = reason
        self.version += 1
