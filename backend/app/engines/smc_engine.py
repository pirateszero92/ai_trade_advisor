"""
SMC (Smart Money Concepts) Engine
Implements institutional trading structures matching LuxAlgo Smart Money Concepts (v5):
- Dual-Structure Matrix: Swing Structure (50 bars) + Internal Structure (5 bars)
- Real-Time BOS (Break of Structure) & CHoCH (Change of Character)
- Extreme-Point Order Blocks with Deep Mitigation (High/Low penetration)
- Fair Value Gaps (FVG) with Liquidity Imbalance Tracking
- Trailing High/Low Extremes with 50% Equilibrium and Premium/Discount Zones
- Quantitative Overlays (Squeeze Momentum, Volume Delta CVD, Market Regime)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any, Literal, Optional

import numpy as np
import pandas as pd
from loguru import logger

from app.engines.tri_core_engine import TriCorePolicy, TriCoreSetupEngine

from app.engines.indicator_core import IndicatorDecisionCore
from app.engines.indicators import AdvancedIndicatorsEngine
from app.engines.regime_engine import MarketRegimeEngine


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SwingPoint:
    """A single swing high or swing low."""
    index: int
    price: float
    kind: Literal["high", "low"]
    timestamp: pd.Timestamp
    confirmed_index: int = 0
    confirmed_timestamp: Optional[pd.Timestamp] = None
    level_id: str = ""

    def __post_init__(self) -> None:
        if not self.level_id:
            self.level_id = (
                f"swing:{self.kind}:{int(self.index)}:{int(self.confirmed_index)}:"
                f"{float(self.price):.8f}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": round(float(self.price), 6),
            "kind": self.kind,
            "origin_index": int(self.index),
            "origin_timestamp": self.timestamp.isoformat(),
            "confirmed_index": int(self.confirmed_index),
            "confirmed_timestamp": (
                self.confirmed_timestamp.isoformat()
                if isinstance(self.confirmed_timestamp, pd.Timestamp)
                else str(self.confirmed_timestamp or "")
            ),
            "level_id": self.level_id,
        }


@dataclass
class StructureBreak:
    """A confirmed BOS or CHoCH structure breakout."""
    tag: Literal["BOS", "CHoCH"]
    kind: Literal["swing", "internal"]
    direction: Literal["bullish", "bearish"]
    level: float
    pivot_index: int
    pivot_time: Optional[pd.Timestamp] = None
    break_index: int = 0
    break_time: Optional[pd.Timestamp] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "kind": self.kind,
            "direction": self.direction,
            "level": round(float(self.level), 6),
            "pivot_index": int(self.pivot_index),
            "pivot_time": self.pivot_time.isoformat() if isinstance(self.pivot_time, pd.Timestamp) else str(self.pivot_time or ""),
            "break_index": int(self.break_index),
            "break_time": self.break_time.isoformat() if isinstance(self.break_time, pd.Timestamp) else str(self.break_time or ""),
        }


@dataclass
class Zone:
    """An Order Block, Fair Value Gap, or Breaker Block (S/R Flip) zone."""
    kind: Literal["ob", "fvg", "breaker"]
    direction: Literal["bullish", "bearish"]
    top: float
    bottom: float
    mid: float = field(init=False)
    index: int = 0
    timestamp: Optional[pd.Timestamp] = None
    mitigated: bool = False
    source: Literal["swing", "internal"] = "swing"
    # ``index`` is the historical origin candle. ``confirmed_index`` is the
    # candle on which the engine could first know this zone existed (BOS/FVG
    # confirmation). Keeping both prevents hindsight/self-referential retests.
    confirmed_index: int = 0
    confirmed_timestamp: Optional[pd.Timestamp] = None
    zone_id: str = ""
    is_extreme: bool = False
    is_inducement: bool = False
    idm_swept: bool = False

    def __post_init__(self):
        self.mid = (self.top + self.bottom) / 2
        if not self.zone_id:
            self.zone_id = (
                f"{self.kind}:{self.direction}:{self.source}:"
                f"{int(self.index)}:{int(self.confirmed_index)}:"
                f"{float(self.bottom):.8f}:{float(self.top):.8f}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "direction": self.direction,
            "top": round(float(self.top), 6),
            "bottom": round(float(self.bottom), 6),
            "mid": round(float(self.mid), 6),
            "index": int(self.index),
            "timestamp": self.timestamp.isoformat() if isinstance(self.timestamp, pd.Timestamp) else str(self.timestamp or ""),
            "mitigated": bool(self.mitigated),
            "source": self.source,
            "origin_index": int(self.index),
            "confirmed_index": int(self.confirmed_index),
            "confirmed_timestamp": (
                self.confirmed_timestamp.isoformat()
                if isinstance(self.confirmed_timestamp, pd.Timestamp)
                else str(self.confirmed_timestamp or "")
            ),
            "zone_id": self.zone_id,
            "is_extreme": bool(self.is_extreme),
            "is_inducement": bool(self.is_inducement),
            "idm_swept": bool(self.idm_swept),
        }


@dataclass
class SMCSignal:
    """Complete SMC analysis result for one symbol / timeframe."""
    symbol: str
    timeframe: str

    # Market structure
    bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    swing_bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    internal_bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    structure_bias_source: Literal[
        "fresh_structure_break", "confirmed_swing_trend", "neutral"
    ] = "neutral"
    htf_bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    bos: bool = False
    choch: bool = False
    structure_event_age: Optional[int] = None
    structure_event_id: Optional[str] = None
    structure_event_time: Optional[str] = None

    # Key levels
    order_block: Optional[Zone] = None
    fvg: Optional[Zone] = None
    order_blocks: list[Zone] = field(default_factory=list)
    breaker_blocks: list[Zone] = field(default_factory=list)
    fvgs: list[Zone] = field(default_factory=list)
    swing_structures: list[StructureBreak] = field(default_factory=list)
    internal_structures: list[StructureBreak] = field(default_factory=list)
    equal_highs: list[float] = field(default_factory=list)
    equal_lows: list[float] = field(default_factory=list)
    # Causal metadata is kept separately for backward-compatible numeric
    # consumers of ``equal_highs``/``equal_lows``.
    equal_high_levels: list[dict[str, Any]] = field(default_factory=list)
    equal_low_levels: list[dict[str, Any]] = field(default_factory=list)

    # Strong / Weak Extremes
    strong_weak_high: Optional[dict[str, Any]] = None
    strong_weak_low: Optional[dict[str, Any]] = None

    # Liquidity
    liquidity_swept: bool = False
    sweep_direction: Literal["high", "low", "none"] = "none"
    sweep_price: Optional[float] = None
    liquidity_sweep: dict[str, Any] = field(default_factory=dict)
    displacement: dict[str, Any] = field(default_factory=dict)

    # Context
    in_premium: bool = False
    in_discount: bool = False
    in_equilibrium: bool = False
    zone_position: Literal[
        "premium", "discount", "equilibrium", "mid_range", "unknown"
    ] = "unknown"
    premium_zone: dict[str, float] = field(default_factory=dict)
    discount_zone: dict[str, float] = field(default_factory=dict)
    equilibrium_zone: dict[str, float] = field(default_factory=dict)
    equilibrium: float = 0.0
    current_price: float = 0.0

    # Confluence score (0-100)
    confluence: int = 0
    indicator_decision: dict[str, Any] = field(default_factory=dict)
    market_regime: dict[str, Any] = field(default_factory=dict)

    # Suggested trade
    direction: Literal["long", "short", "wait"] = "wait"
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: float = 0.0
    entry_type: Literal["limit", "market"] = "limit"

    # Quantitative Indicators (Squeeze Momentum & Volume Delta)
    squeeze_status: Literal["squeeze_on", "squeeze_fire", "no_squeeze"] = "no_squeeze"
    squeeze_momentum: float = 0.0
    momentum_direction: Literal["accelerating_up", "decelerating_up", "accelerating_down", "decelerating_down"] = "accelerating_up"
    volume_delta: float = 0.0
    delta_ratio: float = 0.0
    cvd: float = 0.0
    cvd_zscore: float = 0.0
    cvd_divergence: Literal["bullish", "bearish", "none"] = "none"
    cvd_divergence_evidence: dict[str, Any] = field(default_factory=dict)
    cvd_tail: list[float] = field(default_factory=list)
    delta_absorption: bool = False
    delta_absorption_type: Optional[Literal["bullish_absorption", "bearish_absorption"]] = None
    delta_absorption_evidence: dict[str, Any] = field(default_factory=dict)
    delta_status: str = "Neutral"
    delta_source: str = "unavailable"
    flow_source: str = "unavailable"
    flow_granularity: Literal[
        "kline_derived", "aggtrade_derived", "estimated", "unavailable"
    ] = "unavailable"
    volume_spike: bool = False
    squeeze_data_valid: bool = False
    volume_data_valid: bool = False
    volume_quality: Literal["exchange_aggressor", "estimated", "unavailable"] = "unavailable"

    # Comprehensive 3-Indicator Scenario Matrix
    scenario: dict[str, Any] = field(default_factory=dict)
    # Independent closed-candle support/resistance observation. This never
    # authorizes execution and is deliberately separate from primary scenario.
    reaction: dict[str, Any] = field(default_factory=dict)
    ai_review: dict[str, Any] = field(default_factory=dict)
    tri_core_setup: dict[str, Any] = field(default_factory=dict)
    execution_ttl_bars: int = 2
    migration_comparison: dict[str, Any] = field(default_factory=dict)

    # Institutional Edge & Sentiment Attributes
    order_flow: dict[str, Any] = field(default_factory=dict)
    derivatives_sentiment: dict[str, Any] = field(default_factory=dict)
    inducements: list[dict[str, Any]] = field(default_factory=list)
    inducement_swept: bool = False
    active_zone_type: Literal["extreme_ob", "decisional_ob", "inducement_trap", "regular"] = "regular"

    # Raw swing data (not serialised to JSON by default)
    swing_highs: list[SwingPoint] = field(default_factory=list, repr=False)
    swing_lows: list[SwingPoint] = field(default_factory=list, repr=False)
    internal_highs: list[SwingPoint] = field(default_factory=list, repr=False)
    internal_lows: list[SwingPoint] = field(default_factory=list, repr=False)

    @property
    def confluence_score(self) -> int:
        return self.confluence

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict (excludes raw swings)."""
        return {
            "symbol": self.symbol,
            "ai_review": self.ai_review,
            "timeframe": self.timeframe,
            "bias": self.bias,
            "swing_bias": self.swing_bias,
            "internal_bias": self.internal_bias,
            "structure_bias_source": self.structure_bias_source,
            "htf_bias": self.htf_bias,
            "bos": self.bos,
            "choch": self.choch,
            "structure_event_age": self.structure_event_age,
            "structure_event_id": self.structure_event_id,
            "structure_event_time": self.structure_event_time,
            "order_block": self.order_block.to_dict() if self.order_block else None,
            "fvg": self.fvg.to_dict() if self.fvg else None,
            "order_blocks": [ob.to_dict() for ob in self.order_blocks],
            "breaker_blocks": [b.to_dict() for b in self.breaker_blocks],
            "fvgs": [fvg.to_dict() for fvg in self.fvgs],
            "swing_structures": [s.to_dict() for s in self.swing_structures],
            "internal_structures": [s.to_dict() for s in self.internal_structures],
            "strong_weak_high": self.strong_weak_high,
            "strong_weak_low": self.strong_weak_low,
            "equal_highs": self.equal_highs,
            "equal_lows": self.equal_lows,
            "equal_high_levels": self.equal_high_levels,
            "equal_low_levels": self.equal_low_levels,
            "liquidity_swept": self.liquidity_swept,
            "sweep_direction": self.sweep_direction,
            "sweep_price": self.sweep_price,
            "liquidity_sweep": self.liquidity_sweep,
            "displacement": self.displacement,
            "in_premium": self.in_premium,
            "in_discount": self.in_discount,
            "in_equilibrium": self.in_equilibrium,
            "zone_position": self.zone_position,
            "premium_zone": self.premium_zone,
            "discount_zone": self.discount_zone,
            "equilibrium_zone": self.equilibrium_zone,
            "equilibrium": self.equilibrium,
            "current_price": self.current_price,
            "confluence": self.confluence,
            "confluence_score": self.confluence_score,
            "indicator_decision": self.indicator_decision,
            "market_regime": self.market_regime,
            "direction": self.direction,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward": self.risk_reward,
            "entry_type": self.entry_type,
            "squeeze_status": self.squeeze_status,
            "squeeze_momentum": self.squeeze_momentum,
            "momentum_direction": self.momentum_direction,
            "volume_delta": self.volume_delta,
            "delta_ratio": self.delta_ratio,
            "cvd": self.cvd,
            "cvd_zscore": self.cvd_zscore,
            "cvd_divergence": self.cvd_divergence,
            "cvd_divergence_evidence": self.cvd_divergence_evidence,
            "cvd_tail": self.cvd_tail,
            "delta_absorption": self.delta_absorption,
            "delta_absorption_type": self.delta_absorption_type,
            "delta_absorption_evidence": self.delta_absorption_evidence,
            "delta_status": self.delta_status,
            "delta_source": self.delta_source,
            "flow_source": self.flow_source,
            "flow_granularity": self.flow_granularity,
            "volume_spike": self.volume_spike,
            "squeeze_data_valid": self.squeeze_data_valid,
            "volume_data_valid": self.volume_data_valid,
            "volume_quality": self.volume_quality,
            "scenario": self.scenario,
            "reaction": self.reaction,
            "tri_core_setup": self.tri_core_setup,
            "trigger_state": self.tri_core_setup.get("trigger_state", "wait") if isinstance(self.tri_core_setup, dict) else "wait",
            "event_id": self.tri_core_setup.get("event_id", "") if isinstance(self.tri_core_setup, dict) else "",
            "execution_ttl_bars": self.execution_ttl_bars,
            "migration_comparison": self.migration_comparison,
            "order_flow": self.order_flow,
            "derivatives_sentiment": self.derivatives_sentiment,
            "inducements": self.inducements,
            "inducement_swept": self.inducement_swept,
            "active_zone_type": self.active_zone_type,
        }


# ---------------------------------------------------------------------------
# SMC Engine
# ---------------------------------------------------------------------------

class SMCEngine:
    """
    Analyses OHLCV data using LuxAlgo Smart Money Concepts (v5) methodology.
    """

    DEFAULT_SWING_LENGTH = 50       # LuxAlgo default swing structure length
    DEFAULT_INTERNAL_LENGTH = 5     # LuxAlgo default internal structure length
    EQL_TOLERANCE = 0.1             # LuxAlgo default: 0.1 * ATR(200)

    def __init__(
        self,
        swing_length: int = 50,
        internal_swing_length: int = 5,
        eql_tolerance: float = 0.1,
        structure_tolerance: float = 0.002,
        order_block_lookback: int = 100,
        fvg_lookback: int = 50,
        atr_length: int = 200,
        internal_confluence_filter: bool = False,
        structure_event_ttl_bars: int = 3,
    ):
        self.swing_length = swing_length
        self.internal_swing_length = internal_swing_length
        self.eql_tolerance = eql_tolerance
        self.structure_tolerance = structure_tolerance
        self.order_block_lookback = order_block_lookback
        self.fvg_lookback = fvg_lookback
        self.atr_length = atr_length
        self.internal_confluence_filter = bool(internal_confluence_filter)
        self.structure_event_ttl_bars = max(0, int(structure_event_ttl_bars))
        self.indicator_core = IndicatorDecisionCore()
        self.regime_engine = MarketRegimeEngine()

    def analyze(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        htf_bias: Literal["bullish", "bearish", "neutral"] = "neutral",
        entry_mode: Literal["limit", "market"] = "limit",
        indicator_config: dict[str, Any] | None = None,
        regime_config: dict[str, Any] | None = None,
        tri_core_policy: dict[str, Any] | None = None,
    ) -> SMCSignal:
        """
        Run full LuxAlgo SMC analysis pipeline on the given OHLCV DataFrame.
        """
        required = {"open", "high", "low", "close"}
        normalized_columns = {str(column).lower() for column in df.columns}
        if not required.issubset(normalized_columns):
            logger.error(f"[SMC] Missing OHLC columns for {symbol}: {sorted(required - normalized_columns)}")
            return SMCSignal(symbol=symbol, timeframe=timeframe, htf_bias=htf_bias)
        if df.empty or len(df) < 15:
            logger.warning(f"[SMC] Insufficient data for {symbol} ({len(df)} bars)")
            return SMCSignal(symbol=symbol, timeframe=timeframe, htf_bias=htf_bias)

        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        parsed_index = pd.to_datetime(df.index, utc=True, errors="coerce")
        if parsed_index.isna().any():
            logger.error(f"[SMC] Invalid candle timestamps for {symbol}")
            return SMCSignal(symbol=symbol, timeframe=timeframe, htf_bias=htf_bias)
        df.index = parsed_index
        df = df[~df.index.duplicated(keep="last")].sort_index()
        numeric_columns = [column for column in ("open", "high", "low", "close", "volume") if column in df]
        df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")
        if not np.isfinite(df[["open", "high", "low", "close"]].to_numpy()).all():
            logger.error(f"[SMC] Non-finite OHLC data for {symbol}")
            return SMCSignal(symbol=symbol, timeframe=timeframe, htf_bias=htf_bias)

        signal = SMCSignal(symbol=symbol, timeframe=timeframe, htf_bias=htf_bias)
        policy_ttl = TriCorePolicy.from_mapping(tri_core_policy).ttl_bars
        signal.execution_ttl_bars = min(
            max(0, policy_ttl), max(0, self.structure_event_ttl_bars)
        )
        signal.current_price = float(df["close"].iloc[-1])
        active_indicator_config = indicator_config or self.indicator_core.config()

        try:
            # 1. Run Chronological LuxAlgo SMC Engine
            self._run_luxalgo_smc(df, signal)

            # Closed-candle displacement metadata is descriptive until the
            # Tri-Core resolver pairs it with a later return and true CVD.
            atr = pd.Series(
                self._wilder_atr(
                    df["high"].to_numpy(dtype=float),
                    df["low"].to_numpy(dtype=float),
                    df["close"].to_numpy(dtype=float),
                    14,
                ),
                index=df.index,
            )
            structure_by_index: dict[int, StructureBreak] = {}
            for event in [*signal.swing_structures, *signal.internal_structures]:
                existing = structure_by_index.get(event.break_index)
                if existing is None or (existing.kind == "internal" and event.kind == "swing"):
                    structure_by_index[event.break_index] = event
            best_displacement: dict[str, Any] = {}
            for i in range(max(1, len(df) - 10), len(df)):
                candle_range = float(df["high"].iloc[i] - df["low"].iloc[i])
                body = float(abs(df["close"].iloc[i] - df["open"].iloc[i]))
                atr_i = float(atr.iloc[i]) if np.isfinite(atr.iloc[i]) else 0.0
                if atr_i > 0 and body >= 0.75 * atr_i and candle_range > 0 and (body / candle_range) >= 0.50:
                    direction = "long" if df["close"].iloc[i] > df["open"].iloc[i] else "short"
                    expected_dir = "bullish" if direction == "long" else "bearish"
                    structure = structure_by_index.get(i)
                    if structure is None or structure.direction != expected_dir:
                        s_prev = structure_by_index.get(i - 1)
                        if s_prev is not None and s_prev.direction == expected_dir:
                            structure = s_prev
                        else:
                            s_next = structure_by_index.get(i + 1)
                            if s_next is not None and s_next.direction == expected_dir:
                                structure = s_next

                    has_struct = structure is not None and structure.direction == expected_dir
                    struct_id = (
                        f"{structure.kind}:{structure.tag}:{structure.direction}:"
                        f"{structure.break_index}:{structure.level:.8f}"
                        if has_struct else None
                    )
                    struct_index = structure.break_index if has_struct else None
                    curr_displacement = {
                        "index": i,
                        "time": df.index[i].isoformat(),
                        "direction": direction,
                        "body_atr": round(body / atr_i, 3),
                        "structure_break_id": struct_id,
                        "structure_break_index": struct_index,
                    }
                    if not best_displacement:
                        best_displacement = curr_displacement
                    elif struct_id is not None:
                        best_displacement = curr_displacement
                    elif best_displacement.get("structure_break_id") is None:
                        best_displacement = curr_displacement

            if best_displacement:
                signal.displacement = best_displacement

            # 2. Equal levels are produced by the independent three-bar leg
            # state with the configured price-relative tolerance.

            # 3. Liquidity sweeps & Inducement (IDM) trap recognition
            self._detect_liquidity_sweep(df, signal)
            self._detect_inducement(df, signal)

            # 4. Squeeze Momentum
            squeeze_config = active_indicator_config["indicators"]["squeeze_momentum"]
            if squeeze_config["enabled"]:
                try:
                    sq = AdvancedIndicatorsEngine.compute_squeeze_momentum(
                        df, **squeeze_config["params"]
                    )
                    signal.squeeze_status = sq.status
                    signal.squeeze_momentum = sq.momentum
                    signal.momentum_direction = sq.direction
                    signal.squeeze_data_valid = bool(sq.histogram)
                except Exception as exc:
                    logger.warning(
                        "Error computing Squeeze Momentum for {}: {}", symbol, exc
                    )

            # 5. Volume Delta / CVD
            volume_config = active_indicator_config["indicators"]["volume_delta"]
            if volume_config["enabled"]:
                try:
                    vd = AdvancedIndicatorsEngine.compute_volume_delta(
                        df, **volume_config["params"]
                    )
                    signal.volume_delta = vd.delta
                    signal.delta_ratio = vd.delta_ratio
                    signal.cvd = vd.cvd
                    signal.cvd_zscore = vd.cvd_zscore
                    signal.cvd_divergence = vd.divergence
                    signal.cvd_divergence_evidence = vd.divergence_evidence or {}
                    signal.cvd_tail = vd.cvd_tail or []
                    signal.delta_absorption = vd.is_absorption
                    signal.delta_absorption_type = vd.absorption_type
                    signal.delta_absorption_evidence = vd.absorption_evidence or {}
                    signal.delta_status = vd.description
                    signal.delta_source = vd.source
                    signal.flow_source = vd.flow_source
                    if vd.flow_source == "binance_taker_volume":
                        signal.flow_granularity = "kline_derived"
                    elif vd.flow_source == "binance_aggressor_trade":
                        signal.flow_granularity = "aggtrade_derived"
                    elif vd.source == "estimated_candle_anatomy":
                        signal.flow_granularity = "estimated"
                    signal.volume_spike = vd.volume_spike
                    has_volume = "volume" in df and bool((df["volume"] > 0).any())
                    signal.volume_quality = (
                        "exchange_aggressor"
                        if has_volume and vd.source == "exchange_aggressor"
                        else "estimated" if has_volume else "unavailable"
                    )
                    signal.volume_data_valid = signal.volume_quality != "unavailable"
                    # Rebind divergence to the exact SMC swing and the full
                    # breach/reclaim window. Generic pivot divergence is useful
                    # for display, but it cannot authorize a Tri-Core sweep.
                    sweep = signal.liquidity_sweep or {}
                    if signal.volume_quality == "exchange_aggressor" and sweep:
                        reference_index = int(sweep.get("reference_index", -1))
                        reclaim_index = int(sweep.get("candle_index", -1))
                        if (0 <= reference_index < reclaim_index < len(df)
                                and vd.cvd_values and vd.delta_values):
                            expected = "bullish" if signal.sweep_direction == "low" else "bearish"
                            reference_price = float(sweep.get("level", 0.0))
                            extreme = float(sweep.get("extreme", 0.0))
                            cvd_change = float(vd.cvd_values[reclaim_index] - vd.cvd_values[reference_index])
                            gross_flow = float(sum(abs(value) for value in vd.delta_values[reference_index + 1:reclaim_index + 1]))
                            atr_value = float(atr.iloc[reclaim_index]) if np.isfinite(atr.iloc[reclaim_index]) else 0.0
                            excursion = ((reference_price - extreme) if expected == "bullish"
                                         else (extreme - reference_price))
                            excursion_atr = excursion / atr_value if atr_value > 0 else 0.0
                            efficiency = ((cvd_change if expected == "bullish" else -cvd_change)
                                          / gross_flow if gross_flow > 0 else 0.0)
                            confirmed = excursion_atr >= 0.10 and efficiency >= 0.05
                            signal.cvd_divergence = expected if confirmed else "none"
                            signal.cvd_divergence_evidence = {
                                "reference_index": reference_index,
                                "reference_extreme_price": reference_price,
                                "reference_cvd": float(vd.cvd_values[reference_index]),
                                "sweep_extreme_price": extreme,
                                "sweep_extreme_index": int(sweep.get("extreme_index", reclaim_index)),
                                "reclaim_close_price": float(df["close"].iloc[reclaim_index]),
                                "current_cvd": float(vd.cvd_values[reclaim_index]),
                                "cvd_change": cvd_change,
                                "cvd_efficiency": round(efficiency, 4),
                                "price_excursion_atr": round(excursion_atr, 4),
                                "causal_sweep_bound": True,
                                "flow_source": vd.flow_source,
                            }
                except Exception as exc:
                    logger.warning(
                        "Error computing Volume Delta for {}: {}", symbol, exc
                    )

            # 6. Preliminary regime is analytics/risk context only.
            # context. It is recomputed after final direction selection.
            signal.market_regime = self.regime_engine.classify(
                df, signal, config=regime_config
            )

            # Preserve an observational legacy baseline for migration metrics;
            # it is never read by Strategy/Risk/OMS.
            legacy_shadow = deepcopy(signal)
            self._compute_trade_setup(legacy_shadow, entry_mode=entry_mode, df=df)

            # 7. Tri-Core is the only entry-direction authority.
            tri_core = TriCoreSetupEngine.evaluate(signal, df, tri_core_policy)
            signal.tri_core_setup = tri_core.to_dict()
            if tri_core.actionable:
                signal.direction = tri_core.direction
                signal.entry = round(float(tri_core.entry), 6)
                signal.stop_loss = round(float(tri_core.stop_loss), 6)
                signal.take_profit = round(float(tri_core.take_profit), 6)
                signal.risk_reward = tri_core.risk_reward
                signal.entry_type = tri_core.order_type
            else:
                signal.direction = "wait"
                signal.entry = signal.stop_loss = signal.take_profit = None
                signal.risk_reward = 0.0
            signal.migration_comparison = {
                "legacy_direction": legacy_shadow.direction,
                "legacy_risk_reward": legacy_shadow.risk_reward,
                "tri_core_direction": signal.direction,
                "tri_core_actionable": tri_core.actionable,
                "direction_agreement": legacy_shadow.direction == signal.direction,
                "authority": "tri_core_only",
            }

            # 8. Scenario remains analytics-only and cannot change direction.
            from app.engines.scenario_engine import ScenarioMatrixEngine
            scenario_res = ScenarioMatrixEngine.classify(signal, df=df)
            signal.scenario = scenario_res.to_dict()
            if scenario_res.archetype in {"support_reaction", "resistance_reaction"}:
                # Keep the executable reaction and its entry-window metadata
                # consistent across the primary scenario and reaction payload.
                signal.reaction = scenario_res.to_dict()
            else:
                reaction_res = ScenarioMatrixEngine.classify_reaction(signal, df=df)
                signal.reaction = reaction_res.to_dict() if reaction_res else {}
            # 9. Score exactly once for the Tri-Core direction. Scenario
            # confidence is metadata and never manufactures confluence points.
            signal.indicator_decision = self.indicator_core.evaluate(
                signal, active_indicator_config
            )
            signal.confluence = int(signal.indicator_decision["score"])
            signal.market_regime = self.regime_engine.classify(
                df, signal, config=regime_config
            )

        except Exception as exc:
            logger.exception(f"[SMC] Analysis error for {symbol}: {exc}")

        return signal

    # ------------------------------------------------------------------
    # LuxAlgo SMC Chronological State Machine
    # ------------------------------------------------------------------

    def _run_luxalgo_smc(self, df: pd.DataFrame, signal: SMCSignal) -> None:
        """
        Chronological multi-bar scanner matching LuxAlgo Pine Script v5 logic.
        """
        n = len(df)
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        closes = df["close"].to_numpy(dtype=float)
        opens = df["open"].to_numpy(dtype=float)
        timestamps = df.index

        # LuxAlgo confirms a pivot ``size`` bars later (it does not require a
        # symmetric 2*size window). Only shorten the configured lengths when
        # the caller supplied an unusually small frame.
        eff_swing_length = min(self.swing_length, max(2, n - 2))
        eff_internal_length = min(self.internal_swing_length, max(2, n - 2))

        # 1. Pine ``ta.atr`` uses Wilder's RMA, not a rolling simple mean.
        # Keep pre-warm values as NaN so early bars are not falsely classified
        # as high-volatility bars (the same behaviour as Pine).
        atr_series = self._wilder_atr(highs, lows, closes, self.atr_length)

        # Volatility-parsed highs/lows
        high_vol_bars = np.isfinite(atr_series) & ((highs - lows) >= (2 * atr_series))
        parsed_highs = np.where(high_vol_bars, lows, highs)
        parsed_lows = np.where(high_vol_bars, highs, lows)

        # State tracking structures
        swing_high_lvl = np.nan
        swing_high_idx = -1
        swing_high_crossed = True

        swing_low_lvl = np.nan
        swing_low_idx = -1
        swing_low_crossed = True

        internal_high_lvl = np.nan
        internal_high_idx = -1
        internal_high_crossed = True

        internal_low_lvl = np.nan
        internal_low_idx = -1
        internal_low_crossed = True

        swing_trend = 0      # +1: Bullish, -1: Bearish, 0: Neutral
        internal_trend = 0

        trailing_top = float(highs[0])
        trailing_bottom = float(lows[0])
        trailing_top_time = timestamps[0]
        trailing_bottom_time = timestamps[0]
        trailing_top_origin_index = 0
        trailing_bottom_origin_index = 0
        trailing_top_confirmed_index = 0
        trailing_bottom_confirmed_index = 0

        swing_obs: list[Zone] = []
        internal_obs: list[Zone] = []
        breaker_blocks: list[Zone] = []
        fvgs: list[Zone] = []
        active_obs: list[Zone] = []
        active_breakers: list[Zone] = []
        active_fvgs: list[Zone] = []
        swing_structures: list[StructureBreak] = []
        internal_structures: list[StructureBreak] = []

        swing_highs_list: list[SwingPoint] = []
        swing_lows_list: list[SwingPoint] = []
        internal_highs_list: list[SwingPoint] = []
        internal_lows_list: list[SwingPoint] = []

        # Leg states. LuxAlgo starts in the bearish leg (0); a pivot is stored
        # only when the confirmed leg changes, preventing repeated pivots of
        # the same side and the resulting duplicate BOS/CHoCH labels.
        swing_leg = 0
        internal_leg = 0
        equal_leg = 0
        equal_high_lvl = np.nan
        equal_low_lvl = np.nan
        equal_high_origin_index = -1
        equal_low_origin_index = -1
        equal_high_levels: list[float] = []
        equal_low_levels: list[float] = []
        equal_high_metadata: list[dict[str, Any]] = []
        equal_low_metadata: list[dict[str, Any]] = []
        equal_length = min(3, max(1, n - 2))
        fvg_delta_window: list[float] = []

        for i in range(n):
            c_high = highs[i]
            c_low = lows[i]
            c_close = closes[i]
            previous_close = closes[i - 1] if i > 0 else c_close
            upper_wick = c_high - max(c_close, opens[i])
            lower_wick = min(c_close, opens[i]) - c_low
            bullish_concordant = upper_wick > lower_wick
            bearish_concordant = upper_wick < lower_wick

            # Trailing extremes
            if c_high > trailing_top:
                trailing_top = c_high
                trailing_top_time = timestamps[i]
                trailing_top_origin_index = i
                trailing_top_confirmed_index = i
            if c_low < trailing_bottom:
                trailing_bottom = c_low
                trailing_bottom_time = timestamps[i]
                trailing_bottom_origin_index = i
                trailing_bottom_confirmed_index = i

            # --- A. Confirm Swing Structure Pivot (LuxAlgo leg state) ---
            if i >= eff_swing_length:
                p = i - eff_swing_length
                confirmation_highs = highs[p + 1 : i + 1]
                confirmation_lows = lows[p + 1 : i + 1]
                next_leg = swing_leg
                if highs[p] > np.max(confirmation_highs):
                    next_leg = 0
                elif lows[p] < np.min(confirmation_lows):
                    next_leg = 1

                if next_leg != swing_leg:
                    swing_leg = next_leg
                    if swing_leg == 1:
                        swing_low_lvl = float(lows[p])
                        swing_low_idx = p
                        swing_low_crossed = False
                        trailing_bottom = swing_low_lvl
                        trailing_bottom_time = timestamps[p]
                        trailing_bottom_origin_index = p
                        trailing_bottom_confirmed_index = i
                        swing_lows_list.append(
                            SwingPoint(
                                p,
                                swing_low_lvl,
                                "low",
                                timestamps[p],
                                confirmed_index=i,
                                confirmed_timestamp=timestamps[i],
                            )
                        )
                    else:
                        swing_high_lvl = float(highs[p])
                        swing_high_idx = p
                        swing_high_crossed = False
                        trailing_top = swing_high_lvl
                        trailing_top_time = timestamps[p]
                        trailing_top_origin_index = p
                        trailing_top_confirmed_index = i
                        swing_highs_list.append(
                            SwingPoint(
                                p,
                                swing_high_lvl,
                                "high",
                                timestamps[p],
                                confirmed_index=i,
                                confirmed_timestamp=timestamps[i],
                            )
                        )

            # --- B. Confirm Internal Structure Pivot (fixed five-bar leg) ---
            if i >= eff_internal_length:
                p = i - eff_internal_length
                confirmation_highs = highs[p + 1 : i + 1]
                confirmation_lows = lows[p + 1 : i + 1]
                next_leg = internal_leg
                if highs[p] > np.max(confirmation_highs):
                    next_leg = 0
                elif lows[p] < np.min(confirmation_lows):
                    next_leg = 1

                if next_leg != internal_leg:
                    internal_leg = next_leg
                    if internal_leg == 1:
                        internal_low_lvl = float(lows[p])
                        internal_low_idx = p
                        internal_low_crossed = False
                        internal_lows_list.append(
                            SwingPoint(
                                p,
                                internal_low_lvl,
                                "low",
                                timestamps[p],
                                confirmed_index=i,
                                confirmed_timestamp=timestamps[i],
                            )
                        )
                    else:
                        internal_high_lvl = float(highs[p])
                        internal_high_idx = p
                        internal_high_crossed = False
                        internal_highs_list.append(
                            SwingPoint(
                                p,
                                internal_high_lvl,
                                "high",
                                timestamps[p],
                                confirmed_index=i,
                                confirmed_timestamp=timestamps[i],
                            )
                        )

            # Equal highs/lows use their own three-bar confirmation state and
            # the configured fractional price tolerance.
            if i >= equal_length:
                p = i - equal_length
                confirmation_highs = highs[p + 1 : i + 1]
                confirmation_lows = lows[p + 1 : i + 1]
                next_leg = equal_leg
                if highs[p] > np.max(confirmation_highs):
                    next_leg = 0
                elif lows[p] < np.min(confirmation_lows):
                    next_leg = 1
                if next_leg != equal_leg:
                    equal_leg = next_leg
                    threshold = self._equal_level_threshold(atr_series[i])
                    if equal_leg == 1:
                        level = float(lows[p])
                        if (
                            np.isfinite(threshold)
                            and not np.isnan(equal_low_lvl)
                            and abs(equal_low_lvl - level) < threshold
                        ):
                            equal_price = round((equal_low_lvl + level) / 2.0, 6)
                            equal_low_levels.append(equal_price)
                            equal_low_metadata.append({
                                "price": equal_price,
                                "kind": "equal_low",
                                "first_origin_index": int(equal_low_origin_index),
                                "first_origin_timestamp": timestamps[equal_low_origin_index].isoformat(),
                                "origin_index": int(p),
                                "origin_timestamp": timestamps[p].isoformat(),
                                "confirmed_index": int(i),
                                "confirmed_timestamp": timestamps[i].isoformat(),
                                "level_id": (
                                    f"equal:low:{int(equal_low_origin_index)}:{int(p)}:"
                                    f"{int(i)}:{equal_price:.8f}"
                                ),
                            })
                        equal_low_lvl = level
                        equal_low_origin_index = p
                    else:
                        level = float(highs[p])
                        if (
                            np.isfinite(threshold)
                            and not np.isnan(equal_high_lvl)
                            and abs(equal_high_lvl - level) < threshold
                        ):
                            equal_price = round((equal_high_lvl + level) / 2.0, 6)
                            equal_high_levels.append(equal_price)
                            equal_high_metadata.append({
                                "price": equal_price,
                                "kind": "equal_high",
                                "first_origin_index": int(equal_high_origin_index),
                                "first_origin_timestamp": timestamps[equal_high_origin_index].isoformat(),
                                "origin_index": int(p),
                                "origin_timestamp": timestamps[p].isoformat(),
                                "confirmed_index": int(i),
                                "confirmed_timestamp": timestamps[i].isoformat(),
                                "level_id": (
                                    f"equal:high:{int(equal_high_origin_index)}:{int(p)}:"
                                    f"{int(i)}:{equal_price:.8f}"
                                ),
                            })
                        equal_high_lvl = level
                        equal_high_origin_index = p

            # --- C. Structure Breakout & OB Creation (Internal) ---
            if (
                not np.isnan(internal_high_lvl)
                and not internal_high_crossed
                and previous_close <= internal_high_lvl < c_close
                and (not self.internal_confluence_filter or bullish_concordant)
                and (
                    np.isnan(swing_high_lvl)
                    or not np.isclose(internal_high_lvl, swing_high_lvl)
                )
            ):
                tag = "CHoCH" if internal_trend == -1 else "BOS"
                internal_high_crossed = True
                internal_trend = 1
                struct = StructureBreak(
                    tag=tag,
                    kind="internal",
                    direction="bullish",
                    level=float(internal_high_lvl),
                    pivot_index=internal_high_idx,
                    pivot_time=timestamps[internal_high_idx] if internal_high_idx >= 0 else None,
                    break_index=i,
                    break_time=timestamps[i],
                )
                internal_structures.append(struct)

                # Store Bullish Internal OB: Find lowest parsed low in range
                if internal_high_idx >= 0 and internal_high_idx <= i:
                    sub_lows = parsed_lows[internal_high_idx:i]
                    sub_highs = parsed_highs[internal_high_idx:i]
                    min_pos = int(np.argmin(sub_lows))
                    ob_idx = internal_high_idx + min_pos
                    new_ob = Zone(
                        kind="ob",
                        direction="bullish",
                        top=float(sub_highs[min_pos]),
                        bottom=float(sub_lows[min_pos]),
                        index=ob_idx,
                        timestamp=timestamps[ob_idx],
                        source="internal",
                        confirmed_index=i,
                        confirmed_timestamp=timestamps[i],
                    )
                    internal_obs.append(new_ob)
                    active_obs.append(new_ob)

            if (
                not np.isnan(internal_low_lvl)
                and not internal_low_crossed
                and previous_close >= internal_low_lvl > c_close
                and (not self.internal_confluence_filter or bearish_concordant)
                and (
                    np.isnan(swing_low_lvl)
                    or not np.isclose(internal_low_lvl, swing_low_lvl)
                )
            ):
                tag = "CHoCH" if internal_trend == 1 else "BOS"
                internal_low_crossed = True
                internal_trend = -1
                struct = StructureBreak(
                    tag=tag,
                    kind="internal",
                    direction="bearish",
                    level=float(internal_low_lvl),
                    pivot_index=internal_low_idx,
                    pivot_time=timestamps[internal_low_idx] if internal_low_idx >= 0 else None,
                    break_index=i,
                    break_time=timestamps[i],
                )
                internal_structures.append(struct)

                # Store Bearish Internal OB: Find highest parsed high in range
                if internal_low_idx >= 0 and internal_low_idx <= i:
                    sub_highs = parsed_highs[internal_low_idx:i]
                    sub_lows = parsed_lows[internal_low_idx:i]
                    max_pos = int(np.argmax(sub_highs))
                    ob_idx = internal_low_idx + max_pos
                    new_ob = Zone(
                        kind="ob",
                        direction="bearish",
                        top=float(sub_highs[max_pos]),
                        bottom=float(sub_lows[max_pos]),
                        index=ob_idx,
                        timestamp=timestamps[ob_idx],
                        source="internal",
                        confirmed_index=i,
                        confirmed_timestamp=timestamps[i],
                    )
                    internal_obs.append(new_ob)
                    active_obs.append(new_ob)

            # --- D. Structure Breakout & OB Creation (Swing) ---
            if (
                not np.isnan(swing_high_lvl)
                and not swing_high_crossed
                and previous_close <= swing_high_lvl < c_close
            ):
                tag = "CHoCH" if swing_trend == -1 else "BOS"
                swing_high_crossed = True
                swing_trend = 1
                struct = StructureBreak(
                    tag=tag,
                    kind="swing",
                    direction="bullish",
                    level=float(swing_high_lvl),
                    pivot_index=swing_high_idx,
                    pivot_time=timestamps[swing_high_idx] if swing_high_idx >= 0 else None,
                    break_index=i,
                    break_time=timestamps[i],
                )
                swing_structures.append(struct)

                # Store Bullish Swing OB: Find lowest parsed low in range
                if swing_high_idx >= 0 and swing_high_idx <= i:
                    sub_lows = parsed_lows[swing_high_idx:i]
                    sub_highs = parsed_highs[swing_high_idx:i]
                    min_pos = int(np.argmin(sub_lows))
                    ob_idx = swing_high_idx + min_pos
                    new_ob = Zone(
                        kind="ob",
                        direction="bullish",
                        top=float(sub_highs[min_pos]),
                        bottom=float(sub_lows[min_pos]),
                        index=ob_idx,
                        timestamp=timestamps[ob_idx],
                        source="swing",
                        confirmed_index=i,
                        confirmed_timestamp=timestamps[i],
                    )
                    swing_obs.append(new_ob)
                    active_obs.append(new_ob)

            if (
                not np.isnan(swing_low_lvl)
                and not swing_low_crossed
                and previous_close >= swing_low_lvl > c_close
            ):
                tag = "CHoCH" if swing_trend == 1 else "BOS"
                swing_low_crossed = True
                swing_trend = -1
                struct = StructureBreak(
                    tag=tag,
                    kind="swing",
                    direction="bearish",
                    level=float(swing_low_lvl),
                    pivot_index=swing_low_idx,
                    pivot_time=timestamps[swing_low_idx] if swing_low_idx >= 0 else None,
                    break_index=i,
                    break_time=timestamps[i],
                )
                swing_structures.append(struct)

                # Store Bearish Swing OB: Find highest parsed high in range
                if swing_low_idx >= 0 and swing_low_idx <= i:
                    sub_highs = parsed_highs[swing_low_idx:i]
                    sub_lows = parsed_lows[swing_low_idx:i]
                    max_pos = int(np.argmax(sub_highs))
                    ob_idx = swing_low_idx + max_pos
                    new_ob = Zone(
                        kind="ob",
                        direction="bearish",
                        top=float(sub_highs[max_pos]),
                        bottom=float(sub_lows[max_pos]),
                        index=ob_idx,
                        timestamp=timestamps[ob_idx],
                        source="swing",
                        confirmed_index=i,
                        confirmed_timestamp=timestamps[i],
                    )
                    swing_obs.append(new_ob)
                    active_obs.append(new_ob)

            # --- E. Order Block Mitigation (Deep Mitigation: Low/High violation) & Breaker Creation ---
            rem_active_obs: list[Zone] = []
            for ob in active_obs:
                if ob.index >= i:
                    rem_active_obs.append(ob)
                    continue
                if ob.direction == "bullish" and c_low < ob.bottom:
                    ob.mitigated = True
                    b = Zone(
                        kind="breaker",
                        direction="bearish",
                        top=float(ob.top),
                        bottom=float(ob.bottom),
                        index=int(ob.index),
                        timestamp=ob.timestamp,
                        source=ob.source,
                        confirmed_index=i,
                        confirmed_timestamp=timestamps[i],
                    )
                    breaker_blocks.append(b)
                    active_breakers.append(b)
                elif ob.direction == "bearish" and c_high > ob.top:
                    ob.mitigated = True
                    b = Zone(
                        kind="breaker",
                        direction="bullish",
                        top=float(ob.top),
                        bottom=float(ob.bottom),
                        index=int(ob.index),
                        timestamp=ob.timestamp,
                        source=ob.source,
                        confirmed_index=i,
                        confirmed_timestamp=timestamps[i],
                    )
                    breaker_blocks.append(b)
                    active_breakers.append(b)
                else:
                    rem_active_obs.append(ob)
            active_obs = rem_active_obs

            # Breaker Mitigation (invalidated when broken through on the other side)
            rem_active_breakers: list[Zone] = []
            for b in active_breakers:
                if b.confirmed_index >= i:
                    rem_active_breakers.append(b)
                    continue
                if b.direction == "bullish" and c_low < b.bottom:
                    b.mitigated = True
                elif b.direction == "bearish" and c_high > b.top:
                    b.mitigated = True
                else:
                    rem_active_breakers.append(b)
            active_breakers = rem_active_breakers

            # --- F. Fair Value Gaps (3-Bar Gap Detection & Mitigation) ---
            if i >= 2:
                prior_open = opens[i - 1]
                bar_delta = (
                    (closes[i - 1] - prior_open) / (prior_open * 100.0)
                    if prior_open != 0
                    else 0.0
                )
                fvg_delta_window.append(abs(bar_delta))
                if len(fvg_delta_window) > 200:
                    fvg_delta_window.pop(0)
                fvg_threshold = float(np.mean(fvg_delta_window)) * 2.0
                # Bullish FVG: current candle low > 2 bars ago high
                if (
                    lows[i] > highs[i - 2]
                    and closes[i - 1] > highs[i - 2]
                    and bar_delta > fvg_threshold
                ):
                    fvg_top = float(lows[i])
                    fvg_bottom = float(highs[i - 2])
                    if fvg_top > fvg_bottom:
                        new_fvg = Zone(
                            kind="fvg",
                            direction="bullish",
                            top=fvg_top,
                            bottom=fvg_bottom,
                            index=i - 1,
                            timestamp=timestamps[i - 1],
                            confirmed_index=i,
                            confirmed_timestamp=timestamps[i],
                        )
                        fvgs.append(new_fvg)
                        active_fvgs.append(new_fvg)
                # Bearish FVG: current candle high < 2 bars ago low
                elif (
                    highs[i] < lows[i - 2]
                    and closes[i - 1] < lows[i - 2]
                    and -bar_delta > fvg_threshold
                ):
                    fvg_top = float(lows[i - 2])
                    fvg_bottom = float(highs[i])
                    if fvg_top > fvg_bottom:
                        new_fvg = Zone(
                            kind="fvg",
                            direction="bearish",
                            top=fvg_top,
                            bottom=fvg_bottom,
                            index=i - 1,
                            timestamp=timestamps[i - 1],
                            confirmed_index=i,
                            confirmed_timestamp=timestamps[i],
                        )
                        fvgs.append(new_fvg)
                        active_fvgs.append(new_fvg)

            # FVG Mitigation
            rem_active_fvgs: list[Zone] = []
            for fvg_item in active_fvgs:
                if fvg_item.index >= i:
                    rem_active_fvgs.append(fvg_item)
                    continue
                if fvg_item.direction == "bullish" and c_low < fvg_item.bottom:
                    fvg_item.mitigated = True
                elif fvg_item.direction == "bearish" and c_high > fvg_item.top:
                    fvg_item.mitigated = True
                else:
                    rem_active_fvgs.append(fvg_item)
            active_fvgs = rem_active_fvgs

        # --- Assign Outputs to SMCSignal ---
        signal.swing_highs = swing_highs_list if swing_highs_list else self._detect_swing_points(df, eff_swing_length)[0]
        signal.swing_lows = swing_lows_list if swing_lows_list else self._detect_swing_points(df, eff_swing_length)[1]
        signal.internal_highs = internal_highs_list
        signal.internal_lows = internal_lows_list
        signal.equal_highs = equal_high_levels[-5:]
        signal.equal_lows = equal_low_levels[-5:]
        signal.equal_high_levels = equal_high_metadata[-5:]
        signal.equal_low_levels = equal_low_metadata[-5:]

        structure_cutoff = max(0, n - max(self.order_block_lookback, self.fvg_lookback))
        signal.swing_structures = [s for s in swing_structures if s.break_index >= structure_cutoff][-6:]
        signal.internal_structures = [s for s in internal_structures if s.break_index >= structure_cutoff][-8:]

        # Determine directional bias & fresh breaks
        latest_swing_break = max(swing_structures, key=lambda x: x.break_index, default=None)
        latest_internal_break = max(internal_structures, key=lambda x: x.break_index, default=None)
        signal.swing_bias = (
            latest_swing_break.direction if latest_swing_break is not None
            else "bullish" if swing_trend == 1 else "bearish" if swing_trend == -1 else "neutral"
        )
        signal.internal_bias = (
            latest_internal_break.direction if latest_internal_break is not None
            else "bullish" if internal_trend == 1 else "bearish" if internal_trend == -1 else "neutral"
        )
        # External swing structure owns the market bias. Internal structure is
        # exposed separately and can no longer silently flip the larger regime.
        latest_break = latest_swing_break
        if latest_break is not None:
            signal.bias = signal.swing_bias
            event_age = (n - 1) - latest_break.break_index
            is_fresh = 0 <= event_age <= self.structure_event_ttl_bars
            signal.bos = is_fresh and latest_break.tag == "BOS"
            signal.choch = is_fresh and latest_break.tag == "CHoCH"
            signal.structure_event_age = event_age
            signal.structure_event_time = (
                latest_break.break_time.isoformat()
                if isinstance(latest_break.break_time, pd.Timestamp)
                else str(latest_break.break_time or "")
            )
            signal.structure_event_id = (
                f"{latest_break.kind}:{latest_break.tag}:{latest_break.direction}:"
                f"{latest_break.break_index}:{latest_break.level:.8f}"
            )
            signal.structure_bias_source = "fresh_structure_break" if is_fresh else "confirmed_swing_trend"
        elif signal.swing_bias != "neutral":
            signal.bias = signal.swing_bias
            signal.structure_bias_source = "confirmed_swing_trend"
        else:
            signal.bias = self._infer_persistent_bias(signal.swing_highs, signal.swing_lows)
            if signal.bias != "neutral":
                signal.structure_bias_source = "confirmed_swing_trend"

        # Strong / Weak Extremes
        signal.strong_weak_high = {
            "price": round(float(trailing_top), 6),
            "label": "Strong High" if swing_trend == -1 else "Weak High",
            "time": trailing_top_time.isoformat() if isinstance(trailing_top_time, pd.Timestamp) else str(trailing_top_time),
            "origin_index": int(trailing_top_origin_index),
            "confirmed_index": int(trailing_top_confirmed_index),
            "confirmed_timestamp": timestamps[trailing_top_confirmed_index].isoformat(),
            "level_id": (
                f"strong_weak:high:{int(trailing_top_origin_index)}:"
                f"{int(trailing_top_confirmed_index)}:{float(trailing_top):.8f}"
            ),
        }
        signal.strong_weak_low = {
            "price": round(float(trailing_bottom), 6),
            "label": "Strong Low" if swing_trend == 1 else "Weak Low",
            "time": trailing_bottom_time.isoformat() if isinstance(trailing_bottom_time, pd.Timestamp) else str(trailing_bottom_time),
            "origin_index": int(trailing_bottom_origin_index),
            "confirmed_index": int(trailing_bottom_confirmed_index),
            "confirmed_timestamp": timestamps[trailing_bottom_confirmed_index].isoformat(),
            "level_id": (
                f"strong_weak:low:{int(trailing_bottom_origin_index)}:"
                f"{int(trailing_bottom_confirmed_index)}:{float(trailing_bottom):.8f}"
            ),
        }

        # Active LuxAlgo zones do not expire because of bar age. They remain
        # valid until price mitigates them; only the displayed count is bounded.
        active_swing_obs = [ob for ob in swing_obs if not ob.mitigated][-100:][-5:]
        active_internal_obs = [ob for ob in internal_obs if not ob.mitigated][-100:][-5:]
        unmitigated_obs = active_swing_obs + active_internal_obs
        signal.order_blocks = sorted(unmitigated_obs, key=lambda ob: ob.index)

        # Select primary actionable OB matching required direction
        target_dir = signal.bias if signal.bias != "neutral" else "bullish"
        matching_obs = [ob for ob in unmitigated_obs if ob.direction == target_dir]
        if matching_obs:
            matching_obs.sort(key=lambda ob: abs(ob.mid - signal.current_price))
            signal.order_block = matching_obs[0]
        elif unmitigated_obs:
            signal.order_block = unmitigated_obs[-1]
        else:
            signal.order_block = None

        # FVGs also live until mitigation. Bound the returned collection for
        # transport safety without changing the validity of older active gaps.
        unmitigated_fvgs = [f for f in fvgs if not f.mitigated]
        signal.fvgs = unmitigated_fvgs[-100:]
        matching_fvgs = [f for f in unmitigated_fvgs if f.direction == target_dir]
        if matching_fvgs:
            matching_fvgs.sort(key=lambda f: abs(f.mid - signal.current_price))
            signal.fvg = matching_fvgs[0]
        elif unmitigated_fvgs:
            signal.fvg = unmitigated_fvgs[-1]
        else:
            signal.fvg = None

        # Breaker blocks (S/R Flips) remain valid until mitigated
        active_breakers = [b for b in breaker_blocks if not b.mitigated][-100:]
        signal.breaker_blocks = sorted(active_breakers, key=lambda b: b.index)

        # LuxAlgo range zones: 5% extreme bands and a 5%-wide equilibrium
        # band. The remaining range is deliberately neutral/mid-range.
        range_span = trailing_top - trailing_bottom
        if range_span > 0:
            zones = self._classify_range_zones(
                trailing_top, trailing_bottom, signal.current_price
            )
            signal.equilibrium = zones["equilibrium"]
            signal.premium_zone = zones["premium_zone"]
            signal.discount_zone = zones["discount_zone"]
            signal.equilibrium_zone = zones["equilibrium_zone"]
            signal.zone_position = zones["zone_position"]
            signal.in_premium = signal.zone_position == "premium"
            signal.in_discount = signal.zone_position == "discount"
            signal.in_equilibrium = signal.zone_position == "equilibrium"

    # ------------------------------------------------------------------
    # Compatibility & Helper Methods
    # ------------------------------------------------------------------

    @staticmethod
    def _wilder_atr(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        length: int,
    ) -> np.ndarray:
        """Return Pine-compatible ``ta.atr`` values using Wilder's RMA."""
        size = len(closes)
        result = np.full(size, np.nan, dtype=float)
        if size == 0 or length <= 0:
            return result

        true_range = np.empty(size, dtype=float)
        true_range[0] = highs[0] - lows[0]
        if size > 1:
            true_range[1:] = np.maximum(
                highs[1:] - lows[1:],
                np.maximum(
                    np.abs(highs[1:] - closes[:-1]),
                    np.abs(lows[1:] - closes[:-1]),
                ),
            )
        if size < length:
            return result

        result[length - 1] = float(np.mean(true_range[:length]))
        alpha = 1.0 / float(length)
        for index in range(length, size):
            result[index] = result[index - 1] + alpha * (
                true_range[index] - result[index - 1]
            )
        return result

    def _equal_level_threshold(self, atr_value: float) -> float:
        """LuxAlgo EQH/EQL tolerance expressed as a fraction of ATR."""
        if not np.isfinite(atr_value) or atr_value <= 0:
            return float("nan")
        return float(self.eql_tolerance * atr_value)

    @staticmethod
    def _classify_range_zones(
        trailing_top: float,
        trailing_bottom: float,
        price: float,
    ) -> dict[str, Any]:
        """Build the exact Premium/Discount/Equilibrium bands used by LuxAlgo."""
        top = float(max(trailing_top, trailing_bottom))
        bottom = float(min(trailing_top, trailing_bottom))
        span = top - bottom
        if span <= 0:
            return {
                "equilibrium": round((top + bottom) / 2.0, 6),
                "premium_zone": {},
                "discount_zone": {},
                "equilibrium_zone": {},
                "zone_position": "unknown",
            }

        premium_bottom = 0.95 * top + 0.05 * bottom
        discount_top = 0.95 * bottom + 0.05 * top
        equilibrium_top = 0.525 * top + 0.475 * bottom
        equilibrium_bottom = 0.525 * bottom + 0.475 * top
        equilibrium = (top + bottom) / 2.0

        if premium_bottom <= price <= top:
            position = "premium"
        elif bottom <= price <= discount_top:
            position = "discount"
        elif equilibrium_bottom <= price <= equilibrium_top:
            position = "equilibrium"
        else:
            position = "mid_range"

        def bounds(zone_top: float, zone_bottom: float) -> dict[str, float]:
            return {
                "top": round(float(zone_top), 6),
                "bottom": round(float(zone_bottom), 6),
            }

        return {
            "equilibrium": round(float(equilibrium), 6),
            "premium_zone": bounds(top, premium_bottom),
            "discount_zone": bounds(discount_top, bottom),
            "equilibrium_zone": bounds(equilibrium_top, equilibrium_bottom),
            "zone_position": position,
        }

    def _detect_swing_points(
        self, df: pd.DataFrame, length: int = 5
    ) -> tuple[list[SwingPoint], list[SwingPoint]]:
        """
        Identify swing highs and swing lows using a rolling pivot approach.
        """
        highs = df["high"].values
        lows = df["low"].values
        timestamps = df.index
        n = len(df)

        swing_highs: list[SwingPoint] = []
        swing_lows: list[SwingPoint] = []

        eff_len = min(length, max(1, (n - 1) // 3))

        for i in range(eff_len, n - eff_len):
            left_highs = highs[i - eff_len : i]
            right_highs = highs[i + 1 : i + eff_len + 1]
            left_lows = lows[i - eff_len : i]
            right_lows = lows[i + 1 : i + eff_len + 1]

            if bool(left_highs.size and right_highs.size) and highs[i] > np.max(left_highs) and highs[i] > np.max(right_highs):
                confirmed_index = i + eff_len
                swing_highs.append(
                    SwingPoint(
                        index=i,
                        price=float(highs[i]),
                        kind="high",
                        timestamp=timestamps[i],
                        confirmed_index=confirmed_index,
                        confirmed_timestamp=timestamps[confirmed_index],
                    )
                )

            if bool(left_lows.size and right_lows.size) and lows[i] < np.min(left_lows) and lows[i] < np.min(right_lows):
                confirmed_index = i + eff_len
                swing_lows.append(
                    SwingPoint(
                        index=i,
                        price=float(lows[i]),
                        kind="low",
                        timestamp=timestamps[i],
                        confirmed_index=confirmed_index,
                        confirmed_timestamp=timestamps[confirmed_index],
                    )
                )

        return swing_highs, swing_lows

    def _infer_persistent_bias(
        self,
        swing_highs: list[SwingPoint],
        swing_lows: list[SwingPoint],
    ) -> Literal["bullish", "bearish", "neutral"]:
        """Infer the last confirmed external structure from two swing pairs."""
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "neutral"

        previous_high, current_high = swing_highs[-2].price, swing_highs[-1].price
        previous_low, current_low = swing_lows[-2].price, swing_lows[-1].price

        def meaningfully_above(current: float, previous: float) -> bool:
            return current > previous + abs(previous) * self.structure_tolerance

        def meaningfully_below(current: float, previous: float) -> bool:
            return current < previous - abs(previous) * self.structure_tolerance

        if meaningfully_above(current_high, previous_high) and meaningfully_above(
            current_low, previous_low
        ):
            return "bullish"
        if meaningfully_below(current_high, previous_high) and meaningfully_below(
            current_low, previous_low
        ):
            return "bearish"
        return "neutral"

    def _detect_equal_levels(
        self, df: pd.DataFrame, kind: Literal["high", "low"],
        swing_points: Optional[list[SwingPoint]] = None,
    ) -> list[float]:
        """
        Detect equal price levels (liquidity pools) with ATR/threshold tolerance.
        """
        equal_levels: list[tuple[int, float]] = []
        atr = self._wilder_atr(
            df["high"].to_numpy(dtype=float),
            df["low"].to_numpy(dtype=float),
            df["close"].to_numpy(dtype=float),
            self.atr_length,
        )

        if swing_points and len(swing_points) >= 2:
            prices = [sp.price for sp in swing_points]
            for i in range(len(prices)):
                for j in range(i + 1, len(prices)):
                    ref = prices[i]
                    threshold = self._equal_level_threshold(atr[swing_points[j].index])
                    if not np.isfinite(threshold):
                        continue
                    if abs(prices[j] - ref) <= threshold:
                        equal_levels.append((swing_points[j].index, float(round((ref + prices[j]) / 2.0, 6))))
                        break
        else:
            series = df["high"] if kind == "high" else df["low"]
            values = series.values
            n = len(values)
            for i in range(max(0, n - 100), n):
                ref = values[i]
                for j in range(i + 1, min(i + 20, n)):
                    threshold = self._equal_level_threshold(atr[j])
                    if np.isfinite(threshold) and abs(values[j] - ref) <= threshold:
                        equal_levels.append((j, float(round((ref + values[j]) / 2.0, 6))))
                        break

        unique: list[tuple[int, float]] = []
        for index, level in sorted(equal_levels, key=lambda item: item[0]):
            threshold = self._equal_level_threshold(atr[index])
            existing = next(
                (
                    i
                    for i, (_, value) in enumerate(unique)
                    if np.isfinite(threshold) and abs(level - value) <= threshold
                ),
                None,
            )
            if existing is None:
                unique.append((index, level))
            elif index > unique[existing][0]:
                unique[existing] = (index, level)

        return [level for _, level in sorted(unique, key=lambda item: item[0])[-5:]]

    def _detect_liquidity_sweep(self, df: pd.DataFrame, signal: SMCSignal) -> None:
        """
        Detect a fresh single-bar sweep or a contiguous false-break/reclaim.

        The event extreme covers every candle from the first breach through the
        reclaim. This prevents a later reclaim candle from hiding a deeper wick
        on the preceding breakdown candle and producing an invalid tight stop.
        """
        if len(df) < 5:
            return

        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        closes = df["close"].to_numpy(dtype=float)

        for idx in range(-1, -4, -1):
            if abs(idx) >= len(df):
                break
            c = df.iloc[idx]
            c_high = float(c["high"])
            c_low = float(c["low"])
            c_close = float(c["close"])

            absolute = len(df) + idx
            known_highs = [p for p in signal.swing_highs if p.confirmed_index < absolute]
            known_lows = [p for p in signal.swing_lows if p.confirmed_index < absolute]
            high_ref = known_highs[-1] if known_highs else None
            low_ref = known_lows[-1] if known_lows else None
            p_high = float(high_ref.price) if high_ref is not None else float("inf")
            p_low = float(low_ref.price) if low_ref is not None else 0.0

            if high_ref is not None and c_high > p_high and c_close < p_high:
                breach_start = absolute
                while (breach_start - 1 > high_ref.confirmed_index
                       and (highs[breach_start - 1] > p_high
                            or closes[breach_start - 1] > p_high)):
                    breach_start -= 1
                if breach_start <= 0 or closes[breach_start - 1] > p_high:
                    # No known pre-breach acceptance below the level.
                    continue
                extreme_index = breach_start + int(
                    np.argmax(highs[breach_start:absolute + 1])
                )
                signal.liquidity_swept = True
                signal.sweep_direction = "high"
                signal.sweep_price = p_high
                signal.liquidity_sweep = {
                    "event_id": (
                        f"sweep:{signal.symbol}:{signal.timeframe}:high:"
                        f"{high_ref.level_id}:{df.index[absolute].isoformat()}"
                    ),
                    "level_id": high_ref.level_id,
                    "reference_index": int(high_ref.index),
                    "reference_confirmed_index": int(high_ref.confirmed_index),
                    "level": p_high,
                    "event_type": "single_bar_sweep" if breach_start == absolute else "multi_bar_reclaim",
                    "breach_start_index": int(breach_start),
                    "breach_start_time": df.index[breach_start].isoformat(),
                    "extreme": float(highs[extreme_index]),
                    "extreme_index": int(extreme_index),
                    "candle_index": absolute,
                    "candle_time": df.index[absolute].isoformat(),
                    "reclaim_close": c_close,
                }
                break
            elif low_ref is not None and c_low < p_low and c_close > p_low:
                breach_start = absolute
                while (breach_start - 1 > low_ref.confirmed_index
                       and (lows[breach_start - 1] < p_low
                            or closes[breach_start - 1] < p_low)):
                    breach_start -= 1
                if breach_start <= 0 or closes[breach_start - 1] < p_low:
                    # No known pre-breach acceptance above the level.
                    continue
                extreme_index = breach_start + int(
                    np.argmin(lows[breach_start:absolute + 1])
                )
                signal.liquidity_swept = True
                signal.sweep_direction = "low"
                signal.sweep_price = p_low
                signal.liquidity_sweep = {
                    "event_id": (
                        f"sweep:{signal.symbol}:{signal.timeframe}:low:"
                        f"{low_ref.level_id}:{df.index[absolute].isoformat()}"
                    ),
                    "level_id": low_ref.level_id,
                    "reference_index": int(low_ref.index),
                    "reference_confirmed_index": int(low_ref.confirmed_index),
                    "level": p_low,
                    "event_type": "single_bar_sweep" if breach_start == absolute else "multi_bar_reclaim",
                    "breach_start_index": int(breach_start),
                    "breach_start_time": df.index[breach_start].isoformat(),
                    "extreme": float(lows[extreme_index]),
                    "extreme_index": int(extreme_index),
                    "candle_index": absolute,
                    "candle_time": df.index[absolute].isoformat(),
                    "reclaim_close": c_close,
                }
                break

    def _detect_inducement(self, df: pd.DataFrame, signal: SMCSignal) -> None:
        """
        Detect Smart Money Inducement (IDM) points and classify Order Blocks:
        - In Bullish trend: IDM is the first valid pullback low after a higher high.
          OBs above IDM are classified as Decoy/Inducement OBs (retail traps).
          OBs below IDM (near origin) are classified as Extreme OBs.
        - In Bearish trend: IDM is the first valid pullback high after a lower low.
          OBs below IDM are classified as Decoy/Inducement OBs.
          OBs above IDM (near origin) are classified as Extreme OBs.
        - If price has crossed beyond IDM, signal.inducement_swept = True.
        """
        if len(df) < 10 or not signal.order_blocks:
            return

        inducements: list[dict[str, Any]] = []
        lows = df["low"].to_numpy(dtype=float)
        highs = df["high"].to_numpy(dtype=float)
        closes = df["close"].to_numpy(dtype=float)
        n = len(df)
        curr_price = signal.current_price or float(closes[-1])

        # 1. Bullish Context (Looking for Bullish IDM Low)
        if signal.bias == "bullish" or signal.direction == "long":
            cand_idm_low = None
            cand_idx = -1
            if signal.internal_lows:
                for il in reversed(signal.internal_lows):
                    if il.index < n - 2:
                        cand_idm_low = float(il.price)
                        cand_idx = il.index
                        break
            if cand_idm_low is None:
                lookback = min(15, n - 2)
                recent_low_idx = int(np.argmin(lows[-lookback:-1])) + (n - lookback)
                cand_idm_low = float(lows[recent_low_idx])
                cand_idx = recent_low_idx

            if cand_idm_low is not None:
                swept = bool(np.any(lows[cand_idx + 1 :] < cand_idm_low) or curr_price < cand_idm_low)
                idm_info = {
                    "type": "bullish_idm",
                    "price": round(cand_idm_low, 6),
                    "index": cand_idx,
                    "swept": swept,
                }
                inducements.append(idm_info)
                if swept:
                    signal.inducement_swept = True

                for ob in signal.order_blocks:
                    if ob.direction == "bullish":
                        if ob.top > cand_idm_low:
                            ob.is_inducement = True
                            ob.is_extreme = False
                        else:
                            ob.is_extreme = True
                            ob.is_inducement = False
                            ob.idm_swept = swept

        # 2. Bearish Context (Looking for Bearish IDM High)
        elif signal.bias == "bearish" or signal.direction == "short":
            cand_idm_high = None
            cand_idx = -1
            if signal.internal_highs:
                for ih in reversed(signal.internal_highs):
                    if ih.index < n - 2:
                        cand_idm_high = float(ih.price)
                        cand_idx = ih.index
                        break
            if cand_idm_high is None:
                lookback = min(15, n - 2)
                recent_high_idx = int(np.argmax(highs[-lookback:-1])) + (n - lookback)
                cand_idm_high = float(highs[recent_high_idx])
                cand_idx = recent_high_idx

            if cand_idm_high is not None:
                swept = bool(np.any(highs[cand_idx + 1 :] > cand_idm_high) or curr_price > cand_idm_high)
                idm_info = {
                    "type": "bearish_idm",
                    "price": round(cand_idm_high, 6),
                    "index": cand_idx,
                    "swept": swept,
                }
                inducements.append(idm_info)
                if swept:
                    signal.inducement_swept = True

                for ob in signal.order_blocks:
                    if ob.direction == "bearish":
                        if ob.bottom < cand_idm_high:
                            ob.is_inducement = True
                            ob.is_extreme = False
                        else:
                            ob.is_extreme = True
                            ob.is_inducement = False
                            ob.idm_swept = swept

        signal.inducements = inducements
        if signal.order_block:
            if getattr(signal.order_block, "is_extreme", False):
                signal.active_zone_type = "extreme_ob"
            elif getattr(signal.order_block, "is_inducement", False):
                signal.active_zone_type = "inducement_trap"
            else:
                signal.active_zone_type = "regular"

    def _compute_trade_setup(
        self,
        signal: SMCSignal,
        entry_mode: Literal["limit", "market"] = "limit",
        df: Optional[pd.DataFrame] = None,
    ) -> None:
        """
        Derive entry, SL, TP, and R:R from detected SMC structures with dynamic ATR buffer.
        """
        price = signal.current_price
        ob = signal.order_block
        fvg = signal.fvg
        signal.entry_type = entry_mode

        if signal.bias != "neutral":
            direction = signal.bias
        else:
            signal.direction = "wait"
            return

        atr = 0.0
        if df is not None and len(df) >= self.atr_length:
            tr1 = df["high"] - df["low"]
            tr2 = (df["high"] - df["close"].shift()).abs()
            tr3 = (df["low"] - df["close"].shift()).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = float(tr.rolling(self.atr_length).mean().iloc[-1])

        base_buffer = max(price * 0.002, atr * 0.25) if atr > 0 else (price * 0.002)

        if direction == "bullish":
            signal.direction = "long"
            if entry_mode == "limit":
                if ob and ob.direction == "bullish":
                    entry = ob.mid
                    sl = ob.bottom - base_buffer
                elif fvg and fvg.direction == "bullish":
                    entry = fvg.mid
                    sl = fvg.bottom - base_buffer
                else:
                    entry = price
                    sl = entry - max(entry * 0.008, atr * 1.2) if atr > 0 else (entry * 0.992)
            else:
                entry = price
                sl = (ob.bottom - base_buffer) if (ob and ob.direction == "bullish") else (entry - max(entry * 0.008, atr * 1.2) if atr > 0 else (entry * 0.992))

            if sl >= entry:
                sl = entry - max(entry * 0.008, atr * 1.2) if atr > 0 else (entry * 0.992)
            sl_dist = abs(entry - sl)

            target_candidates = [float(level) for level in signal.equal_highs if float(level) > entry]
            target_candidates.extend(
                float(point.price) for point in signal.swing_highs if float(point.price) > entry
            )
            target_candidates.extend(
                float(zone.bottom)
                for zone in signal.order_blocks
                if zone.direction == "bearish" and float(zone.bottom) > entry
            )
            if not target_candidates:
                signal.direction = "wait"
                signal.entry = round(entry, 6)
                signal.stop_loss = round(sl, 6)
                signal.take_profit = None
                signal.risk_reward = 0.0
                return
            tp = min(target_candidates)

        else:
            signal.direction = "short"
            if entry_mode == "limit":
                if ob and ob.direction == "bearish":
                    entry = ob.mid
                    sl = ob.top + base_buffer
                elif fvg and fvg.direction == "bearish":
                    entry = fvg.mid
                    sl = fvg.top + base_buffer
                else:
                    entry = price
                    sl = entry + max(entry * 0.008, atr * 1.2) if atr > 0 else (entry * 1.008)
            else:
                entry = price
                sl = (ob.top + base_buffer) if (ob and ob.direction == "bearish") else (entry + max(entry * 0.008, atr * 1.2) if atr > 0 else (entry * 1.008))

            if sl <= entry:
                sl = entry + max(entry * 0.008, atr * 1.2) if atr > 0 else (entry * 1.008)
            sl_dist = abs(entry - sl)

            target_candidates = [
                float(level) for level in signal.equal_lows if 0 < float(level) < entry
            ]
            target_candidates.extend(
                float(point.price)
                for point in signal.swing_lows
                if 0 < float(point.price) < entry
            )
            target_candidates.extend(
                float(zone.top)
                for zone in signal.order_blocks
                if zone.direction == "bullish" and 0 < float(zone.top) < entry
            )
            if not target_candidates:
                signal.direction = "wait"
                signal.entry = round(entry, 6)
                signal.stop_loss = round(sl, 6)
                signal.take_profit = None
                signal.risk_reward = 0.0
                return
            tp = max(target_candidates)

        signal.entry = round(entry, 6)
        signal.stop_loss = round(sl, 6)
        signal.take_profit = round(tp, 6)

        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        signal.risk_reward = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    def _compute_confluence(self, signal: SMCSignal) -> int:
        """Compatibility wrapper for callers of the previous private scorer."""
        signal.indicator_decision = self.indicator_core.evaluate(signal)
        return int(signal.indicator_decision["score"])
