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
from typing import Any, Literal, Optional

import numpy as np
import pandas as pd
from loguru import logger

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
    """An Order Block or Fair Value Gap zone."""
    kind: Literal["ob", "fvg"]
    direction: Literal["bullish", "bearish"]
    top: float
    bottom: float
    mid: float = field(init=False)
    index: int = 0
    timestamp: Optional[pd.Timestamp] = None
    mitigated: bool = False
    source: Literal["swing", "internal"] = "swing"

    def __post_init__(self):
        self.mid = (self.top + self.bottom) / 2

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
        }


@dataclass
class SMCSignal:
    """Complete SMC analysis result for one symbol / timeframe."""
    symbol: str
    timeframe: str

    # Market structure
    bias: Literal["bullish", "bearish", "neutral"] = "neutral"
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
    fvgs: list[Zone] = field(default_factory=list)
    swing_structures: list[StructureBreak] = field(default_factory=list)
    internal_structures: list[StructureBreak] = field(default_factory=list)
    equal_highs: list[float] = field(default_factory=list)
    equal_lows: list[float] = field(default_factory=list)

    # Strong / Weak Extremes
    strong_weak_high: Optional[dict[str, Any]] = None
    strong_weak_low: Optional[dict[str, Any]] = None

    # Liquidity
    liquidity_swept: bool = False
    sweep_direction: Literal["high", "low", "none"] = "none"
    sweep_price: Optional[float] = None

    # Context
    in_premium: bool = False
    in_discount: bool = False
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
    delta_absorption: bool = False
    delta_absorption_type: Optional[Literal["bullish_absorption", "bearish_absorption"]] = None
    delta_status: str = "Neutral"
    delta_source: str = "unavailable"
    volume_spike: bool = False
    squeeze_data_valid: bool = False
    volume_data_valid: bool = False
    volume_quality: Literal["exchange_aggressor", "estimated", "unavailable"] = "unavailable"

    # Comprehensive 3-Indicator Scenario Matrix
    scenario: dict[str, Any] = field(default_factory=dict)
    # Independent closed-candle support/resistance observation. This never
    # authorizes execution and is deliberately separate from primary scenario.
    reaction: dict[str, Any] = field(default_factory=dict)

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
            "timeframe": self.timeframe,
            "bias": self.bias,
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
            "fvgs": [fvg.to_dict() for fvg in self.fvgs],
            "swing_structures": [s.to_dict() for s in self.swing_structures],
            "internal_structures": [s.to_dict() for s in self.internal_structures],
            "strong_weak_high": self.strong_weak_high,
            "strong_weak_low": self.strong_weak_low,
            "equal_highs": self.equal_highs,
            "equal_lows": self.equal_lows,
            "liquidity_swept": self.liquidity_swept,
            "sweep_direction": self.sweep_direction,
            "sweep_price": self.sweep_price,
            "in_premium": self.in_premium,
            "in_discount": self.in_discount,
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
            "delta_absorption": self.delta_absorption,
            "delta_absorption_type": self.delta_absorption_type,
            "delta_status": self.delta_status,
            "delta_source": self.delta_source,
            "volume_spike": self.volume_spike,
            "squeeze_data_valid": self.squeeze_data_valid,
            "volume_data_valid": self.volume_data_valid,
            "volume_quality": self.volume_quality,
            "scenario": self.scenario,
            "reaction": self.reaction,
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
    EQL_TOLERANCE = 0.002           # 0.2 % price tolerance for equal highs/lows

    def __init__(
        self,
        swing_length: int = 50,
        internal_swing_length: int = 5,
        eql_tolerance: float = 0.002,
        order_block_lookback: int = 100,
        fvg_lookback: int = 50,
        atr_length: int = 14,
        structure_event_ttl_bars: int = 0,
    ):
        self.swing_length = swing_length
        self.internal_swing_length = internal_swing_length
        self.eql_tolerance = eql_tolerance
        self.order_block_lookback = order_block_lookback
        self.fvg_lookback = fvg_lookback
        self.atr_length = atr_length
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
        numeric_columns = [column for column in ("open", "high", "low", "close", "volume") if column in df]
        df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")
        if not np.isfinite(df[["open", "high", "low", "close"]].to_numpy()).all():
            logger.error(f"[SMC] Non-finite OHLC data for {symbol}")
            return SMCSignal(symbol=symbol, timeframe=timeframe, htf_bias=htf_bias)

        signal = SMCSignal(symbol=symbol, timeframe=timeframe, htf_bias=htf_bias)
        signal.current_price = float(df["close"].iloc[-1])
        active_indicator_config = indicator_config or self.indicator_core.config()

        try:
            # 1. Run Chronological LuxAlgo SMC Engine
            self._run_luxalgo_smc(df, signal)

            # 2. Equal levels are produced by the independent three-bar leg
            # state with the configured price-relative tolerance.

            # 3. Liquidity sweeps
            self._detect_liquidity_sweep(df, signal)

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
                    signal.delta_absorption = vd.is_absorption
                    signal.delta_absorption_type = vd.absorption_type
                    signal.delta_status = vd.description
                    signal.delta_source = vd.source
                    signal.volume_spike = vd.volume_spike
                    has_volume = "volume" in df and bool((df["volume"] > 0).any())
                    signal.volume_quality = (
                        "exchange_aggressor"
                        if has_volume and vd.source == "exchange_aggressor"
                        else "estimated" if has_volume else "unavailable"
                    )
                    signal.volume_data_valid = signal.volume_quality != "unavailable"
                except Exception as exc:
                    logger.warning(
                        "Error computing Volume Delta for {}: {}", symbol, exc
                    )

            # 6. Trade setup (Limit OB zone vs Market price) with dynamic ATR buffer
            self._compute_trade_setup(signal, entry_mode=entry_mode, df=df)

            # 7. Preliminary regime gives the scenario classifier local market
            # context. It is recomputed after final direction selection.
            signal.market_regime = self.regime_engine.classify(
                df, signal, config=regime_config
            )

            # 8. Select the scenario and final direction before evaluating any
            # direction-dependent indicator evidence.
            from app.engines.scenario_engine import ScenarioMatrixEngine
            scenario_res = ScenarioMatrixEngine.classify(signal, df=df)
            signal.scenario = scenario_res.to_dict()
            reaction_res = ScenarioMatrixEngine.classify_reaction(signal, df=df)
            signal.reaction = reaction_res.to_dict() if reaction_res else {}
            if (
                scenario_res.actionable
                and scenario_res.entry_price
                and scenario_res.stop_loss
                and scenario_res.take_profit_1
            ):
                if scenario_res.suggested_action.startswith("buy"):
                    signal.direction = "long"
                    signal.entry = scenario_res.entry_price
                    signal.stop_loss = scenario_res.stop_loss
                    signal.take_profit = scenario_res.take_profit_1
                    signal.risk_reward = scenario_res.risk_reward
                    signal.entry_type = scenario_res.entry_style
                elif scenario_res.suggested_action.startswith("sell"):
                    signal.direction = "short"
                    signal.entry = scenario_res.entry_price
                    signal.stop_loss = scenario_res.stop_loss
                    signal.take_profit = scenario_res.take_profit_1
                    signal.risk_reward = scenario_res.risk_reward
                    signal.entry_type = scenario_res.entry_style

            # 9. Score exactly once for the final trade direction. Scenario
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

        # 1. Compute rolling ATR(200 or len(df)) for volatility filter
        atr_window = min(200, max(14, n - 1))
        tr1 = highs - lows
        tr2 = np.abs(highs - np.roll(closes, 1))
        tr2[0] = tr1[0]
        tr3 = np.abs(lows - np.roll(closes, 1))
        tr3[0] = tr1[0]
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        atr_series = pd.Series(tr).rolling(atr_window, min_periods=1).mean().to_numpy()

        # Volatility-parsed highs/lows
        high_vol_bars = (highs - lows) >= (2 * atr_series)
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

        swing_obs: list[Zone] = []
        internal_obs: list[Zone] = []
        fvgs: list[Zone] = []
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
        equal_high_levels: list[float] = []
        equal_low_levels: list[float] = []
        equal_length = min(3, max(1, n - 2))
        cumulative_fvg_delta = 0.0

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
            if c_low < trailing_bottom:
                trailing_bottom = c_low
                trailing_bottom_time = timestamps[i]

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
                        swing_lows_list.append(
                            SwingPoint(p, swing_low_lvl, "low", timestamps[p])
                        )
                    else:
                        swing_high_lvl = float(highs[p])
                        swing_high_idx = p
                        swing_high_crossed = False
                        trailing_top = swing_high_lvl
                        trailing_top_time = timestamps[p]
                        swing_highs_list.append(
                            SwingPoint(p, swing_high_lvl, "high", timestamps[p])
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
                            SwingPoint(p, internal_low_lvl, "low", timestamps[p])
                        )
                    else:
                        internal_high_lvl = float(highs[p])
                        internal_high_idx = p
                        internal_high_crossed = False
                        internal_highs_list.append(
                            SwingPoint(p, internal_high_lvl, "high", timestamps[p])
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
                    threshold = self.eql_tolerance * max(abs(c_close), 1e-9)
                    if equal_leg == 1:
                        level = float(lows[p])
                        if not np.isnan(equal_low_lvl) and abs(equal_low_lvl - level) < threshold:
                            equal_low_levels.append(round((equal_low_lvl + level) / 2.0, 6))
                        equal_low_lvl = level
                    else:
                        level = float(highs[p])
                        if not np.isnan(equal_high_lvl) and abs(equal_high_lvl - level) < threshold:
                            equal_high_levels.append(round((equal_high_lvl + level) / 2.0, 6))
                        equal_high_lvl = level

            # --- C. Structure Breakout & OB Creation (Internal) ---
            if (
                not np.isnan(internal_high_lvl)
                and not internal_high_crossed
                and previous_close <= internal_high_lvl < c_close
                and bullish_concordant
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
                    internal_obs.append(
                        Zone(
                            kind="ob",
                            direction="bullish",
                            top=float(sub_highs[min_pos]),
                            bottom=float(sub_lows[min_pos]),
                            index=ob_idx,
                            timestamp=timestamps[ob_idx],
                            source="internal",
                        )
                    )

            if (
                not np.isnan(internal_low_lvl)
                and not internal_low_crossed
                and previous_close >= internal_low_lvl > c_close
                and bearish_concordant
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
                    internal_obs.append(
                        Zone(
                            kind="ob",
                            direction="bearish",
                            top=float(sub_highs[max_pos]),
                            bottom=float(sub_lows[max_pos]),
                            index=ob_idx,
                            timestamp=timestamps[ob_idx],
                            source="internal",
                        )
                    )

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
                    swing_obs.append(
                        Zone(
                            kind="ob",
                            direction="bullish",
                            top=float(sub_highs[min_pos]),
                            bottom=float(sub_lows[min_pos]),
                            index=ob_idx,
                            timestamp=timestamps[ob_idx],
                            source="swing",
                        )
                    )

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
                    swing_obs.append(
                        Zone(
                            kind="ob",
                            direction="bearish",
                            top=float(sub_highs[max_pos]),
                            bottom=float(sub_lows[max_pos]),
                            index=ob_idx,
                            timestamp=timestamps[ob_idx],
                            source="swing",
                        )
                    )

            # --- E. Order Block Mitigation (Deep Mitigation: Low/High violation) ---
            for ob in swing_obs + internal_obs:
                if not ob.mitigated and ob.index < i:
                    if ob.direction == "bullish" and c_low < ob.bottom:
                        ob.mitigated = True
                    elif ob.direction == "bearish" and c_high > ob.top:
                        ob.mitigated = True

            # --- F. Fair Value Gaps (3-Bar Gap Detection & Mitigation) ---
            if i >= 2:
                prior_open = opens[i - 1]
                bar_delta = (
                    (closes[i - 1] - prior_open) / (prior_open * 100.0)
                    if prior_open != 0
                    else 0.0
                )
                cumulative_fvg_delta += abs(bar_delta)
                fvg_threshold = (cumulative_fvg_delta / i) * 2.0
                # Bullish FVG: current candle low > 2 bars ago high
                if (
                    lows[i] > highs[i - 2]
                    and closes[i - 1] > highs[i - 2]
                    and bar_delta > fvg_threshold
                ):
                    fvg_top = float(lows[i])
                    fvg_bottom = float(highs[i - 2])
                    if fvg_top > fvg_bottom:
                        fvgs.append(
                            Zone(
                                kind="fvg",
                                direction="bullish",
                                top=fvg_top,
                                bottom=fvg_bottom,
                                index=i - 1,
                                timestamp=timestamps[i - 1],
                            )
                        )
                # Bearish FVG: current candle high < 2 bars ago low
                elif (
                    highs[i] < lows[i - 2]
                    and closes[i - 1] < lows[i - 2]
                    and -bar_delta > fvg_threshold
                ):
                    fvg_top = float(lows[i - 2])
                    fvg_bottom = float(highs[i])
                    if fvg_top > fvg_bottom:
                        fvgs.append(
                            Zone(
                                kind="fvg",
                                direction="bearish",
                                top=fvg_top,
                                bottom=fvg_bottom,
                                index=i - 1,
                                timestamp=timestamps[i - 1],
                            )
                        )

            # FVG Mitigation
            for fvg_item in fvgs:
                if not fvg_item.mitigated and fvg_item.index < i:
                    if fvg_item.direction == "bullish" and c_low < fvg_item.bottom:
                        fvg_item.mitigated = True
                    elif fvg_item.direction == "bearish" and c_high > fvg_item.top:
                        fvg_item.mitigated = True

        # --- Assign Outputs to SMCSignal ---
        signal.swing_highs = swing_highs_list if swing_highs_list else self._detect_swing_points(df, eff_swing_length)[0]
        signal.swing_lows = swing_lows_list if swing_lows_list else self._detect_swing_points(df, eff_swing_length)[1]
        signal.internal_highs = internal_highs_list
        signal.internal_lows = internal_lows_list
        signal.equal_highs = equal_high_levels[-5:]
        signal.equal_lows = equal_low_levels[-5:]

        structure_cutoff = max(0, n - max(self.order_block_lookback, self.fvg_lookback))
        signal.swing_structures = [s for s in swing_structures if s.break_index >= structure_cutoff][-6:]
        signal.internal_structures = [s for s in internal_structures if s.break_index >= structure_cutoff][-8:]

        # Determine directional bias & fresh breaks
        all_recent_breaks = sorted(
            swing_structures + internal_structures,
            key=lambda x: x.break_index,
        )
        if all_recent_breaks:
            latest_break = all_recent_breaks[-1]
            signal.bias = latest_break.direction
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
        elif swing_trend != 0:
            signal.bias = "bullish" if swing_trend == 1 else "bearish"
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
        }
        signal.strong_weak_low = {
            "price": round(float(trailing_bottom), 6),
            "label": "Strong Low" if swing_trend == 1 else "Weak Low",
            "time": trailing_bottom_time.isoformat() if isinstance(trailing_bottom_time, pd.Timestamp) else str(trailing_bottom_time),
        }

        # LuxAlgo displays at most five recent blocks for each structure type.
        ob_cutoff = max(0, n - self.order_block_lookback)
        active_swing_obs = [ob for ob in swing_obs if not ob.mitigated and ob.index >= ob_cutoff][-5:]
        active_internal_obs = [ob for ob in internal_obs if not ob.mitigated and ob.index >= ob_cutoff][-5:]
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

        # Filter active unmitigated FVGs (up to 10 recent)
        fvg_cutoff = max(0, n - self.fvg_lookback)
        unmitigated_fvgs = [f for f in fvgs if not f.mitigated and f.index >= fvg_cutoff]
        signal.fvgs = unmitigated_fvgs[-10:]
        matching_fvgs = [f for f in unmitigated_fvgs if f.direction == target_dir]
        if matching_fvgs:
            matching_fvgs.sort(key=lambda f: abs(f.mid - signal.current_price))
            signal.fvg = matching_fvgs[0]
        elif unmitigated_fvgs:
            signal.fvg = unmitigated_fvgs[-1]
        else:
            signal.fvg = None

        # Equilibrium and Premium / Discount Zones
        range_span = trailing_top - trailing_bottom
        if range_span > 0:
            signal.equilibrium = round(float((trailing_top + trailing_bottom) / 2.0), 6)
            signal.in_premium = signal.current_price > signal.equilibrium
            signal.in_discount = signal.current_price < signal.equilibrium

    # ------------------------------------------------------------------
    # Compatibility & Helper Methods
    # ------------------------------------------------------------------

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
                swing_highs.append(
                    SwingPoint(index=i, price=float(highs[i]), kind="high", timestamp=timestamps[i])
                )

            if bool(left_lows.size and right_lows.size) and lows[i] < np.min(left_lows) and lows[i] < np.min(right_lows):
                swing_lows.append(
                    SwingPoint(index=i, price=float(lows[i]), kind="low", timestamp=timestamps[i])
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
            return current > previous + abs(previous) * self.eql_tolerance

        def meaningfully_below(current: float, previous: float) -> bool:
            return current < previous - abs(previous) * self.eql_tolerance

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
        tol = self.eql_tolerance
        equal_levels: list[tuple[int, float]] = []

        if swing_points and len(swing_points) >= 2:
            prices = [sp.price for sp in swing_points]
            for i in range(len(prices)):
                for j in range(i + 1, len(prices)):
                    ref = prices[i]
                    if ref == 0:
                        continue
                    if abs(prices[j] - ref) / ref <= tol:
                        equal_levels.append((swing_points[j].index, float(round((ref + prices[j]) / 2.0, 6))))
                        break
        else:
            series = df["high"] if kind == "high" else df["low"]
            values = series.values
            n = len(values)
            for i in range(max(0, n - 100), n):
                ref = values[i]
                if ref == 0:
                    continue
                for j in range(i + 1, min(i + 20, n)):
                    if abs(values[j] - ref) / ref <= tol:
                        equal_levels.append((j, float(round((ref + values[j]) / 2.0, 6))))
                        break

        unique: list[tuple[int, float]] = []
        for index, level in sorted(equal_levels, key=lambda item: item[0]):
            existing = next((i for i, (_, value) in enumerate(unique) if abs(level - value) / (value or 1) <= tol), None)
            if existing is None:
                unique.append((index, level))
            elif index > unique[existing][0]:
                unique[existing] = (index, level)

        return [level for _, level in sorted(unique, key=lambda item: item[0])[-5:]]

    def _detect_liquidity_sweep(self, df: pd.DataFrame, signal: SMCSignal) -> None:
        """
        Detect if recent candle swept above/below prior levels and closed back.
        """
        if len(df) < 5:
            return

        for idx in range(-1, -4, -1):
            if abs(idx) >= len(df):
                break
            c = df.iloc[idx]
            c_high = float(c["high"])
            c_low = float(c["low"])
            c_close = float(c["close"])

            p_start = max(0, len(df) + idx - 6)
            p_end = len(df) + idx
            if p_end <= p_start:
                continue
            prev_window = df.iloc[p_start:p_end]
            if prev_window.empty:
                continue
            p_high = float(prev_window["high"].max())
            p_low = float(prev_window["low"].min())

            if c_high > p_high and c_close < p_high:
                signal.liquidity_swept = True
                signal.sweep_direction = "high"
                signal.sweep_price = p_high
                break
            elif c_low < p_low and c_close > p_low:
                signal.liquidity_swept = True
                signal.sweep_direction = "low"
                signal.sweep_price = p_low
                break

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
