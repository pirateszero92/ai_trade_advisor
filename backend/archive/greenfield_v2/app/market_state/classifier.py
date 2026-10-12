"""
Market State Classifier (STATE-001)
Describes market environment (Trend, Range, Compression, etc.) without voting on trade direction.
"""

from enum import Enum
from pydantic import BaseModel, Field

from app.facts.models import MarketFacts


class MarketState(str, Enum):
    TREND = "TREND"
    RANGE = "RANGE"
    COMPRESSION = "COMPRESSION"
    EXPANSION = "EXPANSION"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    ILLIQUID = "ILLIQUID"
    UNKNOWN = "UNKNOWN"


class MarketStateClassification(BaseModel):
    """Deterministic environment classification context."""

    state: MarketState = MarketState.UNKNOWN
    confidence: float = 0.5  # 0.0 to 1.0

    trend_strength: float = 0.0      # 0.0 to 1.0
    range_efficiency: float = 0.0    # 0.0 to 1.0
    atr_percentile: float = 50.0     # 0.0 to 100.0
    liquidity_quality: float = 1.0   # 0.0 to 1.0

    reasons: list[str] = Field(default_factory=list)


class MarketStateEngine:
    """Classifies the environment deterministically from MarketFacts."""

    def classify(self, facts: MarketFacts) -> MarketStateClassification:
        # 1. High Volatility Check
        if facts.volatility.atr_percentile >= 90.0:
            return MarketStateClassification(
                state=MarketState.HIGH_VOLATILITY,
                confidence=0.85,
                atr_percentile=facts.volatility.atr_percentile,
                reasons=["ATR percentile in extreme top decile (>=90%)"],
            )

        # 2. Compression Check (e.g. Squeeze ON)
        if facts.volatility.is_compression:
            return MarketStateClassification(
                state=MarketState.COMPRESSION,
                confidence=0.80,
                atr_percentile=facts.volatility.atr_percentile,
                reasons=["Volatility squeeze active; price consolidating"],
            )

        # 3. Expansion Check
        if facts.volatility.is_expansion:
            return MarketStateClassification(
                state=MarketState.EXPANSION,
                confidence=0.75,
                atr_percentile=facts.volatility.atr_percentile,
                reasons=["Volatility expansion fired post-compression"],
            )

        # 4. Trend vs Range based on structure and zones
        has_clear_bias = facts.structure.bias in {"bullish", "bearish"}
        has_structure_breaks = facts.structure.has_bos or facts.structure.has_choch
        has_active_boundaries = (
            facts.zones.premium_boundary > 0
            and facts.zones.discount_boundary > 0
            and facts.zones.premium_boundary > facts.zones.discount_boundary
        )

        if has_clear_bias and has_structure_breaks and facts.structure.displacement_detected:
            return MarketStateClassification(
                state=MarketState.TREND,
                confidence=0.80,
                trend_strength=0.75,
                range_efficiency=0.30,
                atr_percentile=facts.volatility.atr_percentile,
                reasons=[
                    f"Confirmed {facts.structure.bias} displacement and structural break continuity"
                ],
            )

        if has_active_boundaries:
            return MarketStateClassification(
                state=MarketState.RANGE,
                confidence=0.75,
                trend_strength=0.20,
                range_efficiency=0.70,
                atr_percentile=facts.volatility.atr_percentile,
                reasons=["Price oscillating between defined premium and discount boundaries"],
            )

        return MarketStateClassification(
            state=MarketState.RANGE if has_active_boundaries else MarketState.UNKNOWN,
            confidence=0.50,
            atr_percentile=facts.volatility.atr_percentile,
            reasons=["Neutral market regime; default range behavior"],
        )
