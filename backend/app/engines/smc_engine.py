"""
SMC (Smart Money Concepts) Engine
Detects institutional trading structures: Order Blocks, FVGs, Liquidity Sweeps,
Break of Structure (BOS), Change of Character (CHoCH), and premium/discount zones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np
import pandas as pd
from loguru import logger

from app.engines.indicators import AdvancedIndicatorsEngine


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

    def __post_init__(self):
        self.mid = (self.top + self.bottom) / 2


@dataclass
class SMCSignal:
    """Complete SMC analysis result for one symbol / timeframe."""
    symbol: str
    timeframe: str

    # Market structure
    bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    htf_bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    bos: bool = False
    choch: bool = False

    # Key levels
    order_block: Optional[Zone] = None
    fvg: Optional[Zone] = None
    equal_highs: list[float] = field(default_factory=list)
    equal_lows: list[float] = field(default_factory=list)

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
    delta_absorption: bool = False
    delta_status: str = "Neutral"
    volume_spike: bool = False

    # Raw swing data (not serialised to JSON by default)
    swing_highs: list[SwingPoint] = field(default_factory=list, repr=False)
    swing_lows: list[SwingPoint] = field(default_factory=list, repr=False)

    @property
    def confluence_score(self) -> int:
        return self.confluence

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict (excludes raw swings)."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "bias": self.bias,
            "htf_bias": self.htf_bias,
            "bos": self.bos,
            "choch": self.choch,
            "order_block": {
                "kind": self.order_block.kind,
                "direction": self.order_block.direction,
                "top": self.order_block.top,
                "bottom": self.order_block.bottom,
                "mid": self.order_block.mid,
                "mitigated": self.order_block.mitigated,
            } if self.order_block else None,
            "fvg": {
                "kind": self.fvg.kind,
                "direction": self.fvg.direction,
                "top": self.fvg.top,
                "bottom": self.fvg.bottom,
                "mid": self.fvg.mid,
                "mitigated": self.fvg.mitigated,
            } if self.fvg else None,
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
            "delta_absorption": self.delta_absorption,
            "delta_status": self.delta_status,
            "volume_spike": self.volume_spike,
        }


# ---------------------------------------------------------------------------
# SMC Engine
# ---------------------------------------------------------------------------

class SMCEngine:
    """
    Analyses OHLCV data using Smart Money Concepts methodology.

    Typical usage::

        engine = SMCEngine()
        signal = engine.analyze(df, symbol="BTCUSDT", timeframe="1H", htf_bias="bullish")
        print(signal.to_dict())
    """

    DEFAULT_SWING_LENGTH = 5   # bars each side for swing detection
    EQL_TOLERANCE = 0.002      # 0.2 % price tolerance for equal highs/lows

    def __init__(
        self,
        swing_length: int = 5,
        internal_swing_length: int = 3,
        eql_tolerance: float = 0.002,
    ):
        self.swing_length = swing_length
        self.internal_swing_length = internal_swing_length
        self.eql_tolerance = eql_tolerance

    def analyze(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        htf_bias: Literal["bullish", "bearish", "neutral"] = "neutral",
        entry_mode: Literal["limit", "market"] = "limit",
    ) -> SMCSignal:
        """
        Run full SMC analysis pipeline on the given OHLCV DataFrame.

        Parameters
        ----------
        df:
            DataFrame with columns: open, high, low, close, volume.
        symbol:
            Ticker symbol (e.g. "BTC/USDT").
        timeframe:
            Chart timeframe string (e.g. "1h", "4h").
        htf_bias:
            Higher Timeframe trend bias for confluence checking.
        entry_mode:
            "limit" (anchored to Order Block / FVG zone) or "market" (current price).

        Returns
        -------
        SMCSignal
            Complete analysis result object.
        """
        if df.empty or len(df) < self.DEFAULT_SWING_LENGTH * 2 + 1:
            logger.warning(f"[SMC] Insufficient data for {symbol} ({len(df)} bars)")
            return SMCSignal(symbol=symbol, timeframe=timeframe, htf_bias=htf_bias)

        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        signal = SMCSignal(symbol=symbol, timeframe=timeframe, htf_bias=htf_bias)
        signal.current_price = float(df["close"].iloc[-1])

        try:
            # 1. Swing points
            swing_highs, swing_lows = self._detect_swing_points(df, self.swing_length)
            signal.swing_highs = swing_highs
            signal.swing_lows = swing_lows

            # 2. Market structure (BOS / CHoCH)
            self._detect_structure(df, swing_highs, swing_lows, signal, "bullish")
            self._detect_structure(df, swing_highs, swing_lows, signal, "bearish")

            # 3. Premium / Discount
            self._compute_premium_discount(df, swing_highs, swing_lows, signal)

            # 4. Order Blocks
            ob_dir = "bullish" if signal.bias in ("bullish", "neutral") else "bearish"
            if htf_bias != "neutral":
                ob_dir = htf_bias
            signal.order_block = self._detect_order_block(df, ob_dir)

            # 5. Fair Value Gaps
            signal.fvg = self._detect_fvg(df, ob_dir)

            # 6. Equal levels (liquidity pools) — use swing points for accuracy & performance
            signal.equal_highs = self._detect_equal_levels(df, "high", swing_points=swing_highs)
            signal.equal_lows = self._detect_equal_levels(df, "low", swing_points=swing_lows)

            # 7. Liquidity sweeps
            self._detect_liquidity_sweep(df, signal)

            # 8. Squeeze Momentum & Volume Delta quantitative analysis
            try:
                sq = AdvancedIndicatorsEngine.compute_squeeze_momentum(df)
                vd = AdvancedIndicatorsEngine.compute_volume_delta(df)

                signal.squeeze_status = sq.status
                signal.squeeze_momentum = sq.momentum
                signal.momentum_direction = sq.direction

                signal.volume_delta = vd.delta
                signal.delta_ratio = vd.delta_ratio
                signal.cvd = vd.cvd
                signal.delta_absorption = vd.is_absorption
                signal.delta_status = vd.description
                signal.volume_spike = vd.volume_spike
            except Exception as e:
                logger.warning(f"Error computing advanced indicators for {symbol}: {e}")

            # 9. Trade setup (Limit OB zone vs Market price) with dynamic ATR buffer
            self._compute_trade_setup(signal, entry_mode=entry_mode, df=df)

            # 10. Confluence score (0-100 scale)
            signal.confluence = self._compute_confluence(signal)

        except Exception as exc:
            logger.exception(f"[SMC] Analysis error for {symbol}: {exc}")

        return signal

    # ------------------------------------------------------------------
    # Swing Point Detection
    # ------------------------------------------------------------------

    def _detect_swing_points(
        self, df: pd.DataFrame, length: int = 5
    ) -> tuple[list[SwingPoint], list[SwingPoint]]:
        """
        Identify swing highs and swing lows using a rolling pivot approach.

        A swing high is a bar whose high is the highest in a window of
        ``length`` bars on each side.  Swing lows are the inverse.

        Parameters
        ----------
        df:
            OHLCV DataFrame.
        length:
            Number of bars to look left and right for swing detection.

        Returns
        -------
        tuple of (swing_highs, swing_lows)
        """
        highs = df["high"].values
        lows = df["low"].values
        timestamps = df.index
        n = len(df)

        swing_highs: list[SwingPoint] = []
        swing_lows: list[SwingPoint] = []

        for i in range(length, n - length):
            left_highs = highs[i - length : i]
            right_highs = highs[i + 1 : i + length + 1]
            left_lows = lows[i - length : i]
            right_lows = lows[i + 1 : i + length + 1]

            if highs[i] > np.max(left_highs) and highs[i] > np.max(right_highs):
                swing_highs.append(
                    SwingPoint(index=i, price=float(highs[i]), kind="high", timestamp=timestamps[i])
                )

            if lows[i] < np.min(left_lows) and lows[i] < np.min(right_lows):
                swing_lows.append(
                    SwingPoint(index=i, price=float(lows[i]), kind="low", timestamp=timestamps[i])
                )

        return swing_highs, swing_lows

    # ------------------------------------------------------------------
    # Market Structure (BOS / CHoCH)
    # ------------------------------------------------------------------

    def _detect_structure(
        self,
        df: pd.DataFrame,
        swing_highs: list[SwingPoint],
        swing_lows: list[SwingPoint],
        signal: SMCSignal,
        kind: Literal["bullish", "bearish"],
    ) -> None:
        """
        Detect Break of Structure (BOS) and Change of Character (CHoCH).

        BOS (Break of Structure): Price closes beyond a prior swing level in the SAME
        direction as the existing bias — confirms trend continuation (Higher Highs / Lower Lows).

        CHoCH (Change of Character): Price closes beyond a prior swing level AGAINST the
        existing bias — signals a potential reversal. First sign institutional money is
        repositioning.

        Modifies ``signal`` in-place.
        """
        closes = df["close"].values
        last_close = closes[-1]

        if kind == "bullish" and len(swing_highs) >= 2:
            # Reference the PRIOR swing high (second-to-last) as the structural level
            prev_swing_high = swing_highs[-2].price

            if last_close > prev_swing_high:
                # Price closed above prior swing high
                if signal.bias == "bullish":
                    # Continuation: Higher High in existing bullish trend → BOS
                    signal.bos = True
                elif signal.bias in ("bearish", "neutral"):
                    # Reversal: busts above swing high against bearish/neutral bias → CHoCH
                    signal.choch = True
                    signal.bias = "bullish"

        elif kind == "bearish" and len(swing_lows) >= 2:
            # Reference the PRIOR swing low (second-to-last) as the structural level
            prev_swing_low = swing_lows[-2].price

            if last_close < prev_swing_low:
                # Price closed below prior swing low
                if signal.bias == "bearish":
                    # Continuation: Lower Low in existing bearish trend → BOS
                    signal.bos = True
                elif signal.bias in ("bullish", "neutral"):
                    # Reversal: breaks below swing low against bullish/neutral bias → CHoCH
                    signal.choch = True
                    signal.bias = "bearish"

    # ------------------------------------------------------------------
    # Order Block Detection
    # ------------------------------------------------------------------

    def _detect_order_block(
        self, df: pd.DataFrame, direction: Literal["bullish", "bearish"]
    ) -> Optional[Zone]:
        """
        Find the most recent unmitigated order block.

        A bullish OB is the last bearish candle before a strong bullish move.
        A bearish OB is the last bullish candle before a strong bearish move.

        Parameters
        ----------
        df:
            OHLCV DataFrame.
        direction:
            Whether to look for a bullish or bearish OB.

        Returns
        -------
        Zone or None
        """
        if len(df) < 10:
            return None

        opens = df["open"].values
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        opens = df["open"].values
        n = len(df)
        last_close = closes[-1]

        # Look for OB in the last 50 bars (exclude last 3 for confirmation)
        lookback = min(50, n - 4)

        if direction == "bullish":
            for i in range(n - 4, n - lookback, -1):
                # Bearish candle (OB candidate)
                if closes[i] < opens[i]:
                    # Check that subsequent move is strongly bullish
                    future_high = np.max(highs[i + 1 : i + 4])
                    body_size = opens[i] - closes[i]
                    if future_high > opens[i] + body_size * 2 and last_close > opens[i]:
                        ob_top = float(opens[i])
                        ob_bottom = float(lows[i])

                        # Mitigation check: has price re-entered the OB zone after formation?
                        mitigated = False
                        for j in range(i + 4, n - 1):
                            if lows[j] <= ob_top:  # Price re-entered OB zone
                                mitigated = True
                                break

                        # Only return unmitigated OBs — mitigated ones are less reliable
                        if not mitigated and last_close > ob_bottom:
                            return Zone(
                                kind="ob",
                                direction="bullish",
                                top=ob_top,
                                bottom=ob_bottom,
                                index=i,
                                timestamp=df.index[i],
                            )

        else:  # bearish
            for i in range(n - 4, n - lookback, -1):
                # Bullish candle (OB candidate)
                if closes[i] > opens[i]:
                    # Check that subsequent move is strongly bearish
                    future_low = np.min(lows[i + 1 : i + 4])
                    body_size = closes[i] - opens[i]
                    if future_low < opens[i] - body_size * 2 and last_close < opens[i]:
                        ob_top = float(highs[i])
                        ob_bottom = float(opens[i])

                        # Mitigation check: has price re-entered the OB zone after formation?
                        mitigated = False
                        for j in range(i + 4, n - 1):
                            if highs[j] >= ob_bottom:  # Price re-entered OB zone
                                mitigated = True
                                break

                        if not mitigated and last_close < ob_top:
                            return Zone(
                                kind="ob",
                                direction="bearish",
                                top=ob_top,
                                bottom=ob_bottom,
                                index=i,
                                timestamp=df.index[i],
                            )

        return None

    # ------------------------------------------------------------------
    # Fair Value Gap Detection
    # ------------------------------------------------------------------

    def _detect_fvg(
        self, df: pd.DataFrame, direction: Literal["bullish", "bearish"]
    ) -> Optional[Zone]:
        """
        Identify the most recent Fair Value Gap (imbalance).

        An FVG is a 3-candle pattern where candle[i-1].high < candle[i+1].low
        (bullish) or candle[i-1].low > candle[i+1].high (bearish), leaving an
        untouched price gap.

        Parameters
        ----------
        df:
            OHLCV DataFrame.
        direction:
            Direction bias for which FVG to return.

        Returns
        -------
        Zone or None
        """
        highs = df["high"].values
        lows = df["low"].values
        n = len(df)
        last_close = float(df["close"].iloc[-1])

        lookback = min(30, n - 2)

        if direction == "bullish":
            for i in range(n - 2, n - lookback, -1):
                gap_low = highs[i - 1]
                gap_high = lows[i + 1] if i + 1 < n else lows[i]
                if i + 1 >= n:
                    continue
                gap_high = lows[i + 1]
                if gap_high > gap_low and last_close > gap_low:
                    return Zone(
                        kind="fvg",
                        direction="bullish",
                        top=float(gap_high),
                        bottom=float(gap_low),
                        index=i,
                        timestamp=df.index[i],
                    )

        else:
            for i in range(n - 2, n - lookback, -1):
                if i + 1 >= n:
                    continue
                gap_high = lows[i - 1]
                gap_low = highs[i + 1]
                if gap_high > gap_low and last_close < gap_high:
                    return Zone(
                        kind="fvg",
                        direction="bearish",
                        top=float(gap_high),
                        bottom=float(gap_low),
                        index=i,
                        timestamp=df.index[i],
                    )

        return None

    # ------------------------------------------------------------------
    # Equal Highs / Equal Lows (Liquidity Pools)
    # ------------------------------------------------------------------

    def _detect_equal_levels(
        self, df: pd.DataFrame, kind: Literal["high", "low"],
        swing_points: Optional[list[SwingPoint]] = None,
    ) -> list[float]:
        """
        Detect clusters of confirmed swing highs or swing lows that form equal price
        levels (buy-side / sell-side liquidity pools).

        Uses pre-computed swing points instead of every bar to avoid O(n^2) scanning
        and to eliminate false positives from random bar-level clusters.

        Parameters
        ----------
        df:
            OHLCV DataFrame.
        kind:
            ``"high"`` for equal highs, ``"low"`` for equal lows.
        swing_points:
            Pre-computed swing highs or lows. If provided, only these levels are compared.

        Returns
        -------
        List of price levels (floats), most recent 5.
        """
        tol = self.EQL_TOLERANCE
        equal_levels: list[float] = []

        if swing_points and len(swing_points) >= 2:
            # Efficient: compare only swing point prices
            prices = [sp.price for sp in swing_points]
            for i in range(len(prices)):
                for j in range(i + 1, len(prices)):
                    ref = prices[i]
                    if ref == 0:
                        continue
                    if abs(prices[j] - ref) / ref <= tol:
                        equal_levels.append(float(round(ref, 6)))
                        break
        else:
            # Fallback: scan all bars (slower but works without swing data)
            series = df["high"] if kind == "high" else df["low"]
            values = series.values
            n = len(values)
            for i in range(max(0, n - 100), n):   # Only scan last 100 bars
                ref = values[i]
                if ref == 0:
                    continue
                for j in range(i + 1, min(i + 20, n)):
                    if abs(values[j] - ref) / ref <= tol:
                        equal_levels.append(float(round(ref, 6)))
                        break

        # Deduplicate clusters
        unique: list[float] = []
        for lvl in sorted(set(equal_levels)):
            if not unique or abs(lvl - unique[-1]) / (unique[-1] or 1) > tol:
                unique.append(lvl)

        return unique[-5:]  # return most recent 5

    # ------------------------------------------------------------------
    # Premium / Discount Computation
    # ------------------------------------------------------------------

    def _compute_premium_discount(
        self,
        df: pd.DataFrame,
        swing_highs: list[SwingPoint],
        swing_lows: list[SwingPoint],
        signal: SMCSignal,
    ) -> None:
        """
        Determine whether current price is in a premium, discount, or
        equilibrium zone relative to the most recent swing range.

        Premium (>61.8 % of range) — good for shorts.
        Discount (<38.2 % of range) — good for longs.
        Equilibrium — 38.2–61.8 %.

        Modifies ``signal`` in-place.
        """
        if not swing_highs or not swing_lows:
            return

        # Use the active structural swing range across recent swing clusters
        recent_highs = [sp.price for sp in swing_highs[-3:]]
        recent_lows = [sp.price for sp in swing_lows[-3:]]
        high_bound = max(recent_highs) if recent_highs else float(df["high"].max())
        low_bound = min(recent_lows) if recent_lows else float(df["low"].min())

        range_size = high_bound - low_bound
        if range_size <= 0:
            return

        price = signal.current_price
        eq = low_bound + range_size * 0.5
        premium_line = low_bound + range_size * 0.618
        discount_line = low_bound + range_size * 0.382

        signal.equilibrium = round(eq, 6)
        signal.in_premium = price > premium_line
        signal.in_discount = price < discount_line

    # ------------------------------------------------------------------
    # Liquidity Sweep Detection
    # ------------------------------------------------------------------

    def _detect_liquidity_sweep(self, df: pd.DataFrame, signal: SMCSignal) -> None:
        """
        Detect if the most recent candle(s) swept above a prior swing high
        or below a prior swing low (stop-hunt / liquidity grab).

        Modifies ``signal`` in-place.
        """
        if len(df) < 5:
            return

        # Check last 3 candles for liquidity sweep against prior window
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

            # Swept above and closed back below — bearish sweep
            if c_high > p_high and c_close < p_high:
                signal.liquidity_swept = True
                signal.sweep_direction = "high"
                signal.sweep_price = p_high
                break

            # Swept below and closed back above — bullish sweep
            elif c_low < p_low and c_close > p_low:
                signal.liquidity_swept = True
                signal.sweep_direction = "low"
                signal.sweep_price = p_low
                break

    # ------------------------------------------------------------------
    # Trade Setup Computation
    # ------------------------------------------------------------------

    def _compute_trade_setup(
        self,
        signal: SMCSignal,
        entry_mode: Literal["limit", "market"] = "limit",
        df: Optional[pd.DataFrame] = None,
    ) -> None:
        """
        Derive entry, SL, TP, and R:R from detected SMC structures with dynamic ATR buffer.
        Modifies ``signal`` in-place.
        """
        price = signal.current_price
        ob = signal.order_block
        fvg = signal.fvg
        signal.entry_type = entry_mode

        # Determine directional bias
        if signal.htf_bias != "neutral":
            direction = signal.htf_bias
        elif signal.bias != "neutral":
            direction = signal.bias
        else:
            signal.direction = "wait"
            return

        # Compute 14-period ATR for volatility-adaptive SL buffer
        atr = 0.0
        if df is not None and len(df) >= 14:
            tr1 = df["high"] - df["low"]
            tr2 = (df["high"] - df["close"].shift()).abs()
            tr3 = (df["low"] - df["close"].shift()).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])

        # Buffer: max(0.2% price, 0.25 * ATR)
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
            else:  # market entry
                entry = price
                sl = (ob.bottom - base_buffer) if (ob and ob.direction == "bullish") else (entry - max(entry * 0.008, atr * 1.2) if atr > 0 else (entry * 0.992))

            if sl >= entry:
                sl = entry - max(entry * 0.008, atr * 1.2) if atr > 0 else (entry * 0.992)
            sl_dist = abs(entry - sl)

            # TP: Use Buy-side Liquidity (Equal Highs above entry) as structural target
            # Falls back to 2.5R minimum if no structural level found above entry
            rr_based_tp = entry + (sl_dist * 2.5)
            structural_tp = None
            if signal.equal_highs:
                above_entry = [lvl for lvl in signal.equal_highs if lvl > entry + sl_dist]
                if above_entry:
                    structural_tp = min(above_entry)  # Nearest liquidity pool above

            if structural_tp and structural_tp > rr_based_tp:
                tp = structural_tp   # Structural target offers better R:R
            else:
                tp = rr_based_tp     # Minimum 2.5R

            # Crucial Guard: If live price already ran up past the old OB TP target,
            # project fresh target forward from current live price!
            if tp <= price:
                tp = price + max(price * 0.015, sl_dist * 2.0)

        else:  # bearish
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
            else:  # market entry
                entry = price
                sl = (ob.top + base_buffer) if (ob and ob.direction == "bearish") else (entry + max(entry * 0.008, atr * 1.2) if atr > 0 else (entry * 1.008))

            if sl <= entry:
                sl = entry + max(entry * 0.008, atr * 1.2) if atr > 0 else (entry * 1.008)
            sl_dist = abs(entry - sl)

            # TP: Use Sell-side Liquidity (Equal Lows below entry) as structural target
            rr_based_tp = entry - (sl_dist * 2.5)
            structural_tp = None
            if signal.equal_lows:
                below_entry = [lvl for lvl in signal.equal_lows if lvl < entry - sl_dist]
                if below_entry:
                    structural_tp = max(below_entry)  # Nearest liquidity pool below

            if structural_tp and structural_tp < rr_based_tp:
                tp = structural_tp   # Structural target offers better R:R
            else:
                tp = rr_based_tp     # Minimum 2.5R

            # Crucial Guard: If live price already dumped past the old OB TP target,
            # project fresh target forward below current live price!
            if tp >= price:
                tp = price - max(price * 0.015, sl_dist * 2.0)

        signal.entry = round(entry, 6)
        signal.stop_loss = round(sl, 6)
        signal.take_profit = round(tp, 6)

        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        signal.risk_reward = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    # ------------------------------------------------------------------
    # Confluence Scoring
    # ------------------------------------------------------------------

    def _compute_confluence(self, signal: SMCSignal) -> int:
        """
        Score the strength of the trade setup on a 0-100 scale using the 3-Layer Confluence Matrix:
        1. SMC Structural Location (Max 40 pts)
        2. Volume Delta & Absorption (Max 30 pts)
        3. Squeeze Momentum Timing (Max 30 pts)

        Returns
        -------
        int
            Score in the range 0-100.
        """
        score = 0

        # ---- Layer 1: SMC Location (Max 40 pts) ----
        if signal.htf_bias != "neutral" and signal.htf_bias == signal.bias:
            score += 8

        # Direction-aligned Order Block
        if signal.order_block:
            if (signal.direction == "long" and signal.order_block.direction == "bullish") or \
               (signal.direction == "short" and signal.order_block.direction == "bearish"):
                score += 10

        # Direction-aligned Fair Value Gap
        if signal.fvg:
            if (signal.direction == "long" and signal.fvg.direction == "bullish") or \
               (signal.direction == "short" and signal.fvg.direction == "bearish"):
                score += 6

        # Direction-aligned Liquidity Sweep
        if signal.liquidity_swept:
            # Bullish sweep: swept below prior low -> Long confluence
            # Bearish sweep: swept above prior high -> Short confluence
            if (signal.direction == "long" and signal.sweep_direction == "low") or \
               (signal.direction == "short" and signal.sweep_direction == "high"):
                score += 8

        if signal.direction == "long" and signal.in_discount:
            score += 5
        elif signal.direction == "short" and signal.in_premium:
            score += 5
        if signal.bos or signal.choch:
            score += 3

        # ---- Layer 2: Volume Delta & Absorption (Max 30 pts) ----
        # Direction alignment
        if signal.direction == "long" and signal.volume_delta > 0:
            score += 10
        elif signal.direction == "short" and signal.volume_delta < 0:
            score += 10

        # Absorption / Smart Money Footprint
        if signal.delta_absorption:
            score += 15
        elif abs(signal.delta_ratio) >= 0.2:
            score += 8

        # Volume Spike
        if signal.volume_spike:
            score += 5

        # ---- Layer 3: Squeeze Momentum Breakout (Max 30 pts) ----
        if signal.squeeze_status == "squeeze_fire":
            score += 15
        elif signal.squeeze_status == "no_squeeze":
            score += 8
        elif signal.squeeze_status == "squeeze_on":
            score -= 5  # Penalize entering right into a dead squeeze compression

        # Momentum acceleration
        if signal.direction == "long" and signal.momentum_direction == "accelerating_up":
            score += 15
        elif signal.direction == "short" and signal.momentum_direction == "accelerating_down":
            score += 15
        elif signal.direction == "long" and signal.momentum_direction == "decelerating_up":
            score += 8
        elif signal.direction == "short" and signal.momentum_direction == "decelerating_down":
            score += 8

        # Bound score between 0 and 100
        return max(0, min(score, 100))
