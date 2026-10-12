"""
Opportunity Candidate Contract (SETUP-001 to SETUP-004)
Raw candidate output produced by deterministic Setup Detectors before
being converted into a managed, lifecycle-tracked Opportunity.
"""

from typing import Any
from pydantic import BaseModel, Field

from app.domain.opportunity import (
    CausalZone,
    InvalidationCriteria,
    StrategyFamily,
    TargetLevel,
    TradeDirection,
)


class OpportunityCandidate(BaseModel):
    """A detected structural trade candidate."""

    family: StrategyFamily
    direction: TradeDirection
    thesis: str
    evidence: list[str] = Field(default_factory=list)

    entry_zone: CausalZone | None = None
    structural_entry: float
    stop_loss: float
    targets: list[TargetLevel] = Field(default_factory=list)

    invalidation: InvalidationCriteria
    ttl_bars: int = 2

    @property
    def primary_target(self) -> float:
        return self.targets[0].price if self.targets else 0.0

    @property
    def structural_rr(self) -> float:
        risk_dist = abs(self.structural_entry - self.stop_loss)
        if risk_dist <= 0:
            return 0.0
        reward_dist = abs(self.primary_target - self.structural_entry)
        return round(reward_dist / risk_dist, 4)
