"""
Greenfield Opportunity-Centric Domain Layer.
Provides typed contracts, immutable models, and deterministic state machines.
"""

from app.domain.clock import Clock, FrozenClock, ReplayClock, SystemClock
from app.domain.execution import ExecutionDecision, ExecutionIntent, ExecutionOrderType
from app.domain.ids import (
    generate_lineage_id,
    generate_opportunity_id,
    generate_order_id,
    generate_position_id,
    generate_trace_id,
)
from app.domain.opportunity import (
    CausalZone,
    InvalidationCriteria,
    Opportunity,
    OpportunityState,
    StrategyFamily,
    TargetLevel,
    TradeDirection,
)
from app.domain.order import Order, OrderSide, OrderStatus
from app.domain.position import Position, PositionStatus, ProtectionStage
from app.domain.reason_codes import ReasonCode
from app.domain.risk import RiskAssessment, RiskStage
from app.domain.trace import TraceRecord, TraceStage

__all__ = [
    "Clock",
    "SystemClock",
    "FrozenClock",
    "ReplayClock",
    "generate_opportunity_id",
    "generate_lineage_id",
    "generate_order_id",
    "generate_position_id",
    "generate_trace_id",
    "ReasonCode",
    "TradeDirection",
    "StrategyFamily",
    "OpportunityState",
    "TargetLevel",
    "CausalZone",
    "InvalidationCriteria",
    "Opportunity",
    "ExecutionDecision",
    "ExecutionOrderType",
    "ExecutionIntent",
    "RiskStage",
    "RiskAssessment",
    "OrderStatus",
    "OrderSide",
    "Order",
    "PositionStatus",
    "ProtectionStage",
    "Position",
    "TraceStage",
    "TraceRecord",
]
