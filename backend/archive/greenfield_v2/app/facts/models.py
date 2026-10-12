"""
Market Facts Domain Models (FACT-001 to FACT-008)
Deterministic market facts representing structure, liquidity, zones, flow, and volatility.
Contains NO trade direction votes or execution decisions.
"""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field

from app.domain.opportunity import CausalZone
from app.market_data.quality import DataQuality, DataQualityMetadata


class SwingFact(BaseModel):
    index: int
    price: float
    kind: Literal["high", "low"]
    timestamp: datetime
    level_id: str


class StructureBreakFact(BaseModel):
    tag: Literal["BOS", "CHoCH"]
    kind: Literal["swing", "internal"]
    direction: Literal["bullish", "bearish"]
    level: float
    pivot_index: int
    break_index: int


class StructureFact(BaseModel):
    bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    swing_highs: list[SwingFact] = Field(default_factory=list)
    swing_lows: list[SwingFact] = Field(default_factory=list)
    breaks: list[StructureBreakFact] = Field(default_factory=list)
    has_bos: bool = False
    has_choch: bool = False
    displacement_detected: bool = False


class LiquidityFact(BaseModel):
    equal_highs: list[float] = Field(default_factory=list)
    equal_lows: list[float] = Field(default_factory=list)
    liquidity_swept: bool = False
    sweep_direction: Literal["high", "low", "none"] = "none"
    sweep_price: float | None = None
    sweep_age_bars: int = 0


class ZonesFact(BaseModel):
    active_order_blocks: list[CausalZone] = Field(default_factory=list)
    active_fvgs: list[CausalZone] = Field(default_factory=list)
    active_breakers: list[CausalZone] = Field(default_factory=list)
    equilibrium: float = 0.0
    premium_boundary: float = 0.0
    discount_boundary: float = 0.0
    in_premium: bool = False
    in_discount: bool = False


class FlowFact(BaseModel):
    cvd_direction: Literal["bullish", "bearish", "neutral"] = "neutral"
    cvd_divergence: Literal["bullish", "bearish", "none"] = "none"
    delta_absorption: bool = False
    delta_absorption_type: Literal["bullish_absorption", "bearish_absorption"] | None = None
    current_delta: float = 0.0
    quality: DataQuality = DataQuality.DERIVED


class VolatilityFact(BaseModel):
    atr: float = 0.0
    realized_volatility: float = 0.0
    atr_percentile: float = 50.0  # 0 to 100
    is_compression: bool = False
    is_expansion: bool = False


class MarketFacts(BaseModel):
    """Aggregate deterministic facts snapshot for a canonical closed candle."""

    symbol: str
    timeframe: str = "15m"
    candle_close_time: datetime
    current_price: float

    structure: StructureFact
    liquidity: LiquidityFact
    zones: ZonesFact
    flow: FlowFact
    volatility: VolatilityFact

    data_quality: DataQualityMetadata = Field(default_factory=lambda: DataQualityMetadata())
