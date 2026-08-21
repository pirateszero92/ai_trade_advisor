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

    # Confluence score (0-10)
    confluence: int = 0

    # Suggested trade
    direction: Literal["long", "short", "wait"] = "wait"
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: float = 0.0

    # Raw swing data (not serialised to JSON by default)
    swing_highs: list[SwingPoint] = field(default_factory=list, repr=False)
    swing_lows: list[SwingPoint] = field(default_factory=list, repr=False)

    @property
    def confluence_score(self) -> int:
        return self.confluence * 10 if self.confluence <= 10 else self.confluence

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
            "direction": self.direction,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward": self.risk_reward,
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
    ) -> SMCSignal:
        """
        Run full SMC analysis on the supplied OHLCV DataFrame.

        Parameters
        ----------
        df:
            DataFrame with columns open, high, low, close, volume and a
            DatetimeIndex.  Minimum 50 rows recommended.
        symbol:
            Trading pair / symbol string e.g. ``"BTCUSDT"``.
        timeframe:
            Timeframe label e.g. ``"1H"``, ``"4H"``, ``"D"``.
        htf_bias:
            Higher-timeframe directional bias passed from the caller.

        Returns
        -------
        SMCSignal
            Populated signal object.
        """
        if df is None or df.empty or len(df) < 20:
            logger.warning(f"[SMC] Insufficient data for {symbol} {timeframe}")
            return SMCSignal(symbol=symbol, timeframe=timeframe, htf_bias=htf_bias)

        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        signal = SMCSignal(symbol=symbol, timeframe=timeframe, htf_bias=htf_bias)
        signal.current_price = float(df["close"].iloc[-1])

        try:
            # 1. Swing points
            swing_highs, swing_lows = self._detect_swing_points(df, self.DEFAULT_SWING_LENGTH)
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

            # 6. Equal levels (liquidity pools)
            signal.equal_highs = self._detect_equal_levels(df, "high")
            signal.equal_lows = self._detect_equal_levels(df, "low")

            # 7. Liquidity sweeps
            self._detect_liquidity_sweep(df, signal)

            # 8. Trade setup
            self._compute_trade_setup(signal)

            # 9. Confluence score
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

        BOS: price breaks in the same direction as the prevailing trend,
        confirming continuation.
        CHoCH: price breaks in the opposite direction, signalling a potential
        reversal.

        Modifies ``signal`` in-place.
        """
        closes = df["close"].values
        last_close = closes[-1]

        if kind == "bullish" and len(swing_highs) >= 2:
            prev_high = swing_highs[-2].price
            last_high = swing_highs[-1].price
            if last_close > last_high:
                if last_high > prev_high:
                    signal.bos = True
                    if signal.bias == "neutral":
                        signal.bias = "bullish"
                else:
                    signal.choch = True
                    signal.bias = "bullish"

        elif kind == "bearish" and len(swing_lows) >= 2:
            prev_low = swing_lows[-2].price
            last_low = swing_lows[-1].price
            if last_close < last_low:
                if last_low < prev_low:
                    signal.bos = True
                    if signal.bias == "neutral":
                        signal.bias = "bearish"
                else:
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
                        # Not mitigated: last close is above OB bottom
                        if last_close > lows[i]:
                            return Zone(
                                kind="ob",
                                direction="bullish",
                                top=float(opens[i]),
                                bottom=float(lows[i]),
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
                        if last_close < highs[i]:
                            return Zone(
                                kind="ob",
                                direction="bearish",
                                top=float(highs[i]),
                                bottom=float(opens[i]),
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
        self, df: pd.DataFrame, kind: Literal["high", "low"]
    ) -> list[float]:
        """
        Detect clusters of swing highs or lows that form equal price levels
        (buy-side / sell-side liquidity pools).

        Parameters
        ----------
        df:
            OHLCV DataFrame.
        kind:
            ``"high"`` for equal highs, ``"low"`` for equal lows.

        Returns
        -------
        List of price levels (floats).
        """
        series = df["high"] if kind == "high" else df["low"]
        values = series.values
        n = len(values)
        tol = self.EQL_TOLERANCE
        equal_levels: list[float] = []

        for i in range(1, n):
            for j in range(i + 1, min(i + 20, n)):
                ref = values[i]
                if ref == 0:
                    continue
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

        last_high = swing_highs[-1].price if swing_highs else df["high"].max()
        last_low = swing_lows[-1].price if swing_lows else df["low"].min()
        range_size = last_high - last_low
        if range_size <= 0:
            return

        price = signal.current_price
        eq = last_low + range_size * 0.5
        premium_line = last_low + range_size * 0.618
        discount_line = last_low + range_size * 0.382

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

    def _compute_trade_setup(self, signal: SMCSignal) -> None:
        """
        Derive entry, SL, TP, and R:R from detected SMC structures.
        Modifies ``signal`` in-place.
        """
        price = signal.current_price
        ob = signal.order_block
        fvg = signal.fvg

        # Determine directional bias
        if signal.htf_bias != "neutral":
            direction = signal.htf_bias
        elif signal.bias != "neutral":
            direction = signal.bias
        else:
            signal.direction = "wait"
            return

        if direction == "bullish":
            # Entry at OB mid or FVG mid
            if ob and ob.direction == "bullish":
                entry = ob.mid
                sl = ob.bottom * 0.999  # just below OB
                tp = price + (price - sl) * 2.5
            elif fvg and fvg.direction == "bullish":
                entry = fvg.mid
                sl = fvg.bottom * 0.999
                tp = price + (price - sl) * 2.5
            else:
                signal.direction = "wait"
                return
            signal.direction = "long"

        else:  # bearish
            if ob and ob.direction == "bearish":
                entry = ob.mid
                sl = ob.top * 1.001  # just above OB
                tp = price - (sl - price) * 2.5
            elif fvg and fvg.direction == "bearish":
                entry = fvg.mid
                sl = fvg.top * 1.001
                tp = price - (sl - price) * 2.5
            else:
                signal.direction = "wait"
                return
            signal.direction = "short"

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
        Score the strength of the trade setup on a 0-10 scale.

        Each confirming factor adds points:

        +1  HTF bias aligns with LTF bias
        +1  BOS confirmed
        +1  CHoCH present (reversal context)
        +1  Order Block detected
        +1  FVG detected
        +1  Liquidity swept before setup
        +1  Price in premium (short setup) / discount (long setup)
        +1  Equal highs/lows detected (liquidity target)
        +1  R:R >= 2.0
        +1  R:R >= 3.0

        Returns
        -------
        int
            Score in the range 0-10.
        """
        score = 0

        if signal.htf_bias != "neutral" and signal.htf_bias == signal.bias:
            score += 1
        if signal.bos:
            score += 1
        if signal.choch:
            score += 1
        if signal.order_block:
            score += 1
        if signal.fvg:
            score += 1
        if signal.liquidity_swept:
            score += 1
        if signal.direction == "long" and signal.in_discount:
            score += 1
        elif signal.direction == "short" and signal.in_premium:
            score += 1
        if signal.equal_highs or signal.equal_lows:
            score += 1
        if signal.risk_reward >= 2.0:
            score += 1
        if signal.risk_reward >= 3.0:
            score += 1

        return min(score, 10)
