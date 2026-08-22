"""
Indicators Engine Module
Provides mathematical implementations for:
1. Squeeze Momentum (John Carter TTM Squeeze / LazyBear)
2. Volume Delta & Cumulative Volume Delta (CVD)
3. Delta Absorption & Divergence detection
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional
import numpy as np
import pandas as pd


@dataclass
class SqueezeResult:
    status: Literal["squeeze_on", "squeeze_fire", "no_squeeze"]
    momentum: float
    direction: Literal["accelerating_up", "decelerating_up", "accelerating_down", "decelerating_down"]
    bb_upper: float
    bb_lower: float
    kc_upper: float
    kc_lower: float
    histogram: list[float]


@dataclass
class VolumeDeltaResult:
    delta: float
    delta_ratio: float  # -1.0 (100% sell) to +1.0 (100% buy)
    cvd: float
    is_absorption: bool
    absorption_type: Optional[Literal["bullish_absorption", "bearish_absorption"]]
    volume_spike: bool
    description: str


class AdvancedIndicatorsEngine:
    """Quantitative momentum and volume analysis engine."""

    # ------------------------------------------------------------------
    # 1. Squeeze Momentum Indicator (TTM Squeeze / LazyBear)
    # ------------------------------------------------------------------
    @staticmethod
    def compute_squeeze_momentum(
        df: pd.DataFrame,
        bb_length: int = 20,
        bb_mult: float = 2.0,
        kc_length: int = 20,
        kc_mult: float = 1.5,
    ) -> SqueezeResult:
        """
        Calculates Squeeze Momentum indicator.
        - Bollinger Bands (20, 2.0)
        - Keltner Channels (20, 1.5 ATR)
        - Momentum Histogram via linear regression / delta
        """
        if len(df) < max(bb_length, kc_length) + 5:
            return SqueezeResult(
                status="no_squeeze",
                momentum=0.0,
                direction="accelerating_up",
                bb_upper=0.0,
                bb_lower=0.0,
                kc_upper=0.0,
                kc_lower=0.0,
                histogram=[],
            )

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)

        # 1. Bollinger Bands
        bb_basis = close.rolling(window=bb_length).mean()
        bb_std = close.rolling(window=bb_length).std(ddof=0)
        bb_upper = bb_basis + (bb_mult * bb_std)
        bb_lower = bb_basis - (bb_mult * bb_std)

        # 2. Keltner Channels
        # True Range
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        kc_atr = tr.rolling(window=kc_length).mean()
        kc_basis = close.rolling(window=kc_length).mean()
        kc_upper = kc_basis + (kc_mult * kc_atr)
        kc_lower = kc_basis - (kc_mult * kc_atr)

        # 3. Squeeze condition (BB inside KC)
        squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)

        # 4. Momentum Value
        donchian_mid = (high.rolling(window=kc_length).max() + low.rolling(window=kc_length).min()) / 2.0
        kc_sma = close.rolling(window=kc_length).mean()
        avg_baseline = (donchian_mid + kc_sma) / 2.0
        delta = close - avg_baseline

        x = np.arange(kc_length)
        x_mean = x.mean()
        denom = np.sum((x - x_mean) ** 2)
        weights = (1.0 / kc_length) + ((x - x_mean) * (kc_length - 1 - x_mean) / (denom or 1.0))

        delta_vals = delta.values
        hist_vals = np.full_like(delta_vals, 0.0)
        if len(delta_vals) >= kc_length:
            hist_vals[kc_length - 1:] = np.convolve(delta_vals, weights[::-1], mode="valid")
        hist = pd.Series(hist_vals, index=delta.index).fillna(0.0)

        curr_squeeze_on = bool(squeeze_on.iloc[-1])
        prev_squeeze_on = bool(squeeze_on.iloc[-2]) if len(squeeze_on) > 1 else False

        if curr_squeeze_on:
            status = "squeeze_on"
        elif prev_squeeze_on and not curr_squeeze_on:
            status = "squeeze_fire"  # Just fired / expanding
        else:
            status = "no_squeeze"

        curr_mom = float(hist.iloc[-1])
        prev_mom = float(hist.iloc[-2]) if len(hist) > 1 else 0.0

        if curr_mom >= 0:
            if curr_mom >= prev_mom:
                direction = "accelerating_up"   # Bright green
            else:
                direction = "decelerating_up"   # Dark green
        else:
            if curr_mom <= prev_mom:
                direction = "accelerating_down" # Bright red
            else:
                direction = "decelerating_down" # Dark red

        return SqueezeResult(
            status=status,
            momentum=round(curr_mom, 6),
            direction=direction,
            bb_upper=round(float(bb_upper.iloc[-1]), 4),
            bb_lower=round(float(bb_lower.iloc[-1]), 4),
            kc_upper=round(float(kc_upper.iloc[-1]), 4),
            kc_lower=round(float(kc_lower.iloc[-1]), 4),
            histogram=[round(float(v), 6) for v in hist.tail(15).tolist()],
        )

    # ------------------------------------------------------------------
    # 2. Volume Delta & Cumulative Volume Delta (CVD)
    # ------------------------------------------------------------------
    @staticmethod
    def compute_volume_delta(
        df: pd.DataFrame,
        market_type: str = "crypto",
    ) -> VolumeDeltaResult:
        """
        Calculates bar-by-bar Volume Delta and detects Institutional Delta Absorption.
        """
        if len(df) < 10 or "volume" not in df.columns:
            return VolumeDeltaResult(
                delta=0.0,
                delta_ratio=0.0,
                cvd=0.0,
                is_absorption=False,
                absorption_type=None,
                volume_spike=False,
                description="Insufficient volume data",
            )

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        open_p = df["open"].astype(float)
        vol = df["volume"].astype(float)

        hl_range = high - low
        hl_range = hl_range.replace(0, 1e-8)

        buy_ratio = ((close - low) + (close - open_p).clip(lower=0)) / (hl_range * 1.5)
        buy_ratio = buy_ratio.clip(lower=0.05, upper=0.95)

        buy_vol = vol * buy_ratio
        sell_vol = vol * (1.0 - buy_ratio)
        delta_series = buy_vol - sell_vol

        cvd_series = delta_series.cumsum()

        vol_sma20 = vol.rolling(window=min(20, len(vol))).mean()
        curr_vol = float(vol.iloc[-1])
        avg_vol = float(vol_sma20.iloc[-1]) if not np.isnan(vol_sma20.iloc[-1]) else curr_vol
        volume_spike = curr_vol >= (avg_vol * 1.5)

        curr_delta = float(delta_series.iloc[-1])
        curr_cvd = float(cvd_series.iloc[-1])
        curr_total_vol = curr_vol if curr_vol > 0 else 1.0
        delta_ratio = float(np.clip(curr_delta / curr_total_vol, -1.0, 1.0))

        lookback = min(10, len(df))
        recent_lows = low.tail(lookback)
        recent_highs = high.tail(lookback)

        is_absorption = False
        absorption_type = None

        if close.iloc[-1] <= recent_lows.quantile(0.3) and curr_delta > 0:
            is_absorption = True
            absorption_type = "bullish_absorption"
            desc = "Bullish Absorption: Smart money absorbing sell stops at low zone"
        elif close.iloc[-1] >= recent_highs.quantile(0.7) and curr_delta < 0:
            is_absorption = True
            absorption_type = "bearish_absorption"
            desc = "Bearish Absorption: Smart money absorbing buy orders at high zone"
        else:
            if delta_ratio > 0.2:
                desc = f"Net Buying Pressure (+{delta_ratio*100:.1f}%)"
            elif delta_ratio < -0.2:
                desc = f"Net Selling Pressure ({delta_ratio*100:.1f}%)"
            else:
                desc = "Neutral Volume Delta"

        return VolumeDeltaResult(
            delta=round(curr_delta, 2),
            delta_ratio=round(delta_ratio, 3),
            cvd=round(curr_cvd, 2),
            is_absorption=is_absorption,
            absorption_type=absorption_type,
            volume_spike=volume_spike,
            description=desc,
        )
