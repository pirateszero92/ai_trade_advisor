"""
Advisory-Only AI Reviewer (AI-001 to AI-003)
Provides asynchronous context explanations and conflict detection.
Strictly possesses zero execution authority; cannot modify prices, directions, or sizes.
"""

from typing import Any, Literal
from pydantic import BaseModel, Field

from app.domain.opportunity import Opportunity
from app.domain.reason_codes import ReasonCode
from app.facts.models import MarketFacts
from app.market_state.classifier import MarketStateClassification

ADVISORY_SYSTEM_PROMPT = """You are an advisory reviewer for a deterministic trading system.
You are NOT the trading authority.

The deterministic Opportunity Engine owns direction, entry, stop, targets, and invalidation.
The Execution Planner owns MARKET_NOW, PLACE_LIMIT, WAIT, CANCEL.
The Risk Allocator owns sizing and portfolio acceptance.
The OMS owns order creation and cancellations.

Your job is ONLY to:
1. inspect the supplied deterministic facts;
2. identify contradictions or missing context;
3. classify the opportunity as COHERENT, CONFLICT, or UNAVAILABLE;
4. explain the reason concisely;
5. cite evidence from the supplied context.

Never invent prices.
Never change direction.
Never change stop-loss.
Never change targets.
Never recommend position size.
Never override risk.
Never issue an order instruction.

Return strict JSON only:
{
  "verdict": "COHERENT | CONFLICT | UNAVAILABLE",
  "reason": "string",
  "conflicts": ["string"],
  "evidence": ["string"]
}
"""


class AIReviewResult(BaseModel):
    """Authoritative schema for AI advisory reviews."""

    opportunity_id: str
    verdict: Literal["COHERENT", "CONFLICT", "UNAVAILABLE"] = "UNAVAILABLE"
    reason: str = ""
    conflicts: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    @classmethod
    def sanitize_raw_response(cls, opportunity_id: str, payload: dict[str, Any]) -> "AIReviewResult":
        """
        Enforces authority guardrails (Section 30, AI-003):
        Explicitly strips any illicit fields (entry, SL, TP, size) if present.
        """
        verdict = str(payload.get("verdict", "UNAVAILABLE")).upper()
        if verdict not in {"COHERENT", "CONFLICT", "UNAVAILABLE"}:
            verdict = "UNAVAILABLE"

        reason = str(payload.get("reason", ""))[:300]
        conflicts = [str(c)[:120] for c in (payload.get("conflicts") or [])][:6]
        evidence = [str(e)[:120] for e in (payload.get("evidence") or [])][:8]

        return cls(
            opportunity_id=opportunity_id,
            verdict=verdict,  # type: ignore
            reason=reason,
            conflicts=conflicts,
            evidence=evidence,
        )


class AIReviewer:
    """Advisory reviewer evaluating deterministic facts without execution authority."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    def review_facts_locally(
        self,
        opportunity: Opportunity,
        facts: MarketFacts,
        state: MarketStateClassification,
    ) -> AIReviewResult:
        """
        Fast, deterministic heuristic review verifying consistency of facts with opportunity thesis.
        Acts as offline fallback when external LLM is disabled or unavailable.
        """
        conflicts = []
        evidence = []

        # Check opposing flow
        if opportunity.direction.value == "long" and facts.flow.cvd_direction == "bearish":
            conflicts.append("Flow direction opposes long bias")
        elif opportunity.direction.value == "short" and facts.flow.cvd_direction == "bullish":
            conflicts.append("Flow direction opposes short bias")
        else:
            evidence.append(f"Flow aligned with {opportunity.direction.value} direction")

        # Check regime suitability
        if opportunity.family.value == "range_fade" and state.state.value == "TREND":
            conflicts.append("Range fade in trending market environment")
        else:
            evidence.append(f"Setup family {opportunity.family.value} matches state {state.state.value}")

        if conflicts:
            return AIReviewResult(
                opportunity_id=opportunity.opportunity_id,
                verdict="CONFLICT",
                reason=f"Found {len(conflicts)} fact conflict(s)",
                conflicts=conflicts,
                evidence=evidence,
            )

        return AIReviewResult(
            opportunity_id=opportunity.opportunity_id,
            verdict="COHERENT",
            reason="Deterministic facts are internally consistent",
            conflicts=[],
            evidence=evidence,
        )
