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
    cvd: float  # raw cumulative volume delta
    is_absorption: bool
    absorption_type: Optional[Literal["bullish_absorption", "bearish_absorption"]]
    volume_spike: bool
    description: str
    cvd_zscore: float = 0.0
    source: Literal["exchange_aggressor", "estimated_candle_anatomy", "unavailable"] = "estimated_candle_anatomy"
    flow_source: str = "unavailable"
    divergence: Literal["bullish", "bearish", "none"] = "none"
    divergence_evidence: dict = None
    absorption_evidence: dict = None
    cvd_tail: list[float] = None
    cvd_values: list[float] = None
    delta_values: list[float] = None


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
        required = {"close", "high", "low"}
        if not required.issubset(df.columns):
            raise ValueError(f"OHLCV data is missing columns: {sorted(required - set(df.columns))}")
        if min(bb_length, kc_length) < 2 or bb_mult <= 0 or kc_mult <= 0:
            raise ValueError("Indicator lengths and multipliers must be positive")
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

        numeric = df[["close", "high", "low"]].apply(pd.to_numeric, errors="coerce")
        if not np.isfinite(numeric.to_numpy()).all():
            raise ValueError("OHLC data contains NaN or infinite values")
        close, high, low = numeric["close"], numeric["high"], numeric["low"]

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

        # 4. Momentum Value via Rolling Linear Regression (matches LazyBear / TradingView TTM Squeeze)
        # delta = close - avg(donchian_mid, kc_sma) as the series to regress
        donchian_mid = (high.rolling(window=kc_length).max() + low.rolling(window=kc_length).min()) / 2.0
        kc_sma = close.rolling(window=kc_length).mean()
        avg_baseline = (donchian_mid + kc_sma) / 2.0
        delta = close - avg_baseline

        delta_vals = delta.to_numpy(dtype=float)
        n_vals = len(delta_vals)
        hist_vals = np.full(n_vals, np.nan)

        # Rolling linear regression in vectorized C operations. This produces
        # the same fitted last-point value as np.polyfit without a Python loop
        # for every candle/timeframe.
        x = np.arange(kc_length, dtype=float)
        finite = np.isfinite(delta_vals)
        safe_delta = np.where(finite, delta_vals, 0.0)
        sum_y = np.convolve(safe_delta, np.ones(kc_length), mode="valid")
        sum_xy = np.convolve(safe_delta, x[::-1], mode="valid")
        valid_count = np.convolve(
            finite.astype(float), np.ones(kc_length), mode="valid"
        )
        sum_x = float(x.sum())
        sum_x2 = float(np.square(x).sum())
        denominator = (kc_length * sum_x2) - (sum_x * sum_x)
        slope = ((kc_length * sum_xy) - (sum_x * sum_y)) / denominator
        intercept = (sum_y - (slope * sum_x)) / kc_length
        fitted = intercept + (slope * x[-1])
        hist_vals[kc_length - 1:] = np.where(
            valid_count == kc_length, fitted, np.nan
        )

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
        absorption_lookback: int = 10,
        bullish_absorption_quantile: float = 0.30,
        bearish_absorption_quantile: float = 0.70,
        volume_spike_multiplier: float = 1.50,
        pressure_threshold: float = 0.20,
    ) -> VolumeDeltaResult:
        """
        Calculates bar-by-bar Volume Delta and detects Institutional Delta Absorption.
        """
        if not 3 <= absorption_lookback <= 100:
            raise ValueError("absorption_lookback must be between 3 and 100")
        if not 0 < bullish_absorption_quantile < 0.5:
            raise ValueError("bullish_absorption_quantile must be between 0 and 0.5")
        if not 0.5 < bearish_absorption_quantile < 1:
            raise ValueError("bearish_absorption_quantile must be between 0.5 and 1")
        if volume_spike_multiplier < 1:
            raise ValueError("volume_spike_multiplier must be at least 1")
        if not 0 < pressure_threshold <= 1:
            raise ValueError("pressure_threshold must be between 0 and 1")

        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            missing = sorted(required - set(df.columns))
            if "volume" not in df.columns and len(df) >= 10:
                return VolumeDeltaResult(
                    delta=0.0, delta_ratio=0.0, cvd=0.0,
                    is_absorption=False, absorption_type=None,
                    volume_spike=False,
                    description="Volume data is unavailable for this market",
                    source="unavailable",
                )
            raise ValueError(f"OHLCV data is missing columns: {missing}")
        if len(df) < 10:
            return VolumeDeltaResult(
                delta=0.0,
                delta_ratio=0.0,
                cvd=0.0,
                is_absorption=False,
                absorption_type=None,
                volume_spike=False,
                description="Insufficient volume data",
                source="unavailable",
            )

        numeric = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
        if not np.isfinite(numeric.to_numpy()).all():
            raise ValueError("OHLCV data contains NaN or infinite values")
        open_p, high, low, close, vol = (
            numeric["open"], numeric["high"], numeric["low"],
            numeric["close"], numeric["volume"],
        )
        if bool((vol < 0).any()):
            raise ValueError("OHLCV volume cannot be negative")
        if bool(((high < low) | (high < open_p) | (high < close) |
                 (low > open_p) | (low > close)).any()):
            raise ValueError("OHLCV data contains impossible candle geometry")

        hl_range = high - low
        hl_range = hl_range.replace(0, 1e-8)

        trusted_flow_sources = {"binance_taker_volume"}
        normalized_sources = (
            df["flow_source"].astype("string").str.strip().str.lower()
            if "flow_source" in df.columns else pd.Series(dtype="string")
        )
        flow_sources = set(normalized_sources.dropna())
        trusted_aggressor = (
            len(normalized_sources) == len(df)
            and bool(flow_sources)
            and not normalized_sources.isna().any()
            and bool(normalized_sources.isin(trusted_flow_sources).all())
        )

        if {"buy_volume", "sell_volume"}.issubset(df.columns) and trusted_aggressor:
            aggressor = df[["buy_volume", "sell_volume"]].apply(pd.to_numeric, errors="coerce")
            if not np.isfinite(aggressor.to_numpy()).all():
                raise ValueError("Aggressor volume contains NaN or infinite values")
            if bool((aggressor < 0).any().any()):
                raise ValueError("Aggressor volume cannot be negative")
            buy_vol = aggressor["buy_volume"]
            sell_vol = aggressor["sell_volume"]
            reported_total = buy_vol + sell_vol
            tolerance = np.maximum(vol.abs() * 0.01, 1e-8)
            if bool(((reported_total - vol).abs() > tolerance).any()):
                raise ValueError("Aggressor buy/sell volume does not reconcile with total volume")
            delta_series = buy_vol - sell_vol
            delta_source = "exchange_aggressor"
        else:
            # Fallback for providers that expose only OHLCV. This is explicitly
            # labelled as an estimate and must not be represented as true CVD.
            body_up = (close - open_p).clip(lower=0)
            body_down = (open_p - close).clip(lower=0)
            wick_up = high - close.clip(lower=open_p)
            wick_down = close.clip(upper=open_p) - low
            buy_proxy = body_up + wick_down * 0.5
            sell_proxy = body_down + wick_up * 0.5
            total_proxy = (buy_proxy + sell_proxy).replace(0, 1e-8)
            buy_ratio = (buy_proxy / total_proxy).clip(lower=0.05, upper=0.95)
            buy_vol = vol * buy_ratio
            sell_vol = vol * (1.0 - buy_ratio)
            delta_series = buy_vol - sell_vol
            delta_source = "estimated_candle_anatomy"

        # Keep actual cumulative delta separate from its normalized view.
        cvd_window = min(200, len(delta_series))
        cvd_series = delta_series.cumsum()
        cvd_mean = cvd_series.rolling(window=cvd_window, min_periods=2).mean()
        cvd_std = cvd_series.rolling(window=cvd_window, min_periods=2).std(ddof=0)
        cvd_normalized = ((cvd_series - cvd_mean) / cvd_std.replace(0, np.nan)).fillna(0.0)

        vol_sma20 = vol.rolling(window=min(20, len(vol))).mean()
        curr_vol = float(vol.iloc[-1])
        avg_vol = float(vol_sma20.iloc[-1]) if not np.isnan(vol_sma20.iloc[-1]) else curr_vol
        volume_spike = curr_vol >= (avg_vol * volume_spike_multiplier)

        curr_delta = float(delta_series.iloc[-1])
        curr_cvd = float(cvd_series.iloc[-1])
        curr_cvd_zscore = float(cvd_normalized.iloc[-1])
        curr_total_vol = curr_vol if curr_vol > 0 else 1.0
        delta_ratio = float(np.clip(curr_delta / curr_total_vol, -1.0, 1.0))

        lookback = min(absorption_lookback, len(df))
        recent_lows = low.tail(lookback)
        recent_highs = high.tail(lookback)

        is_absorption = False
        absorption_type = None
        divergence = "none"
        divergence_evidence = {}

        # Immediate, causal divergence at a closed reclaim candle. Require both
        # a meaningful price excursion and net CVD efficiency so tiny floating
        # point/noise differences cannot become Grade-S evidence.
        pivot_end = len(df) - 2
        prev_close = close.shift(1)
        tr = pd.concat(
            ((high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()),
            axis=1,
        ).max(axis=1)
        atr_length = 14
        seed_length = min(atr_length, len(tr))
        atr14 = float(tr.iloc[:seed_length].mean())
        for current_tr in tr.iloc[seed_length:]:
            atr14 += (float(current_tr) - atr14) / float(atr_length)
        if delta_source == "exchange_aggressor" and pivot_end >= 5:
            lows = []
            highs = []
            for i in range(2, pivot_end):
                if low.iloc[i] < low.iloc[i-2:i].min() and low.iloc[i] < low.iloc[i+1:i+3].min():
                    lows.append(i)
                if high.iloc[i] > high.iloc[i-2:i].max() and high.iloc[i] > high.iloc[i+1:i+3].max():
                    highs.append(i)
            if lows:
                ref = lows[-1]
                price_excursion = float(low.iloc[ref] - low.iloc[-1])
                cvd_change = float(cvd_series.iloc[-1] - cvd_series.iloc[ref])
                gross_flow = float(delta_series.iloc[ref + 1:].abs().sum())
                price_excursion_atr = price_excursion / atr14 if atr14 > 0 else 0.0
                cvd_efficiency = cvd_change / gross_flow if gross_flow > 0 else 0.0
                if (low.iloc[-1] < low.iloc[ref] and close.iloc[-1] > low.iloc[ref]
                        and cvd_change > 0 and price_excursion_atr >= 0.10
                        and cvd_efficiency >= 0.05):
                    divergence = "bullish"
                    divergence_evidence = {
                        "reference_index": ref,
                        "reference_extreme_price": float(low.iloc[ref]),
                        "reference_price": float(low.iloc[ref]),
                        "reference_cvd": float(cvd_series.iloc[ref]),
                        "sweep_extreme_price": float(low.iloc[-1]),
                        "current_low_price": float(low.iloc[-1]),
                        "reclaim_close_price": float(close.iloc[-1]),
                        "current_cvd": curr_cvd,
                        "cvd_change": cvd_change,
                        "cvd_efficiency": round(cvd_efficiency, 4),
                        "price_excursion_atr": round(price_excursion_atr, 4),
                    }
            if divergence == "none" and highs:
                ref = highs[-1]
                price_excursion = float(high.iloc[-1] - high.iloc[ref])
                cvd_change = float(cvd_series.iloc[-1] - cvd_series.iloc[ref])
                gross_flow = float(delta_series.iloc[ref + 1:].abs().sum())
                price_excursion_atr = price_excursion / atr14 if atr14 > 0 else 0.0
                cvd_efficiency = -cvd_change / gross_flow if gross_flow > 0 else 0.0
                if (high.iloc[-1] > high.iloc[ref] and close.iloc[-1] < high.iloc[ref]
                        and cvd_change < 0 and price_excursion_atr >= 0.10
                        and cvd_efficiency >= 0.05):
                    divergence = "bearish"
                    divergence_evidence = {
                        "reference_index": ref,
                        "reference_extreme_price": float(high.iloc[ref]),
                        "reference_price": float(high.iloc[ref]),
                        "reference_cvd": float(cvd_series.iloc[ref]),
                        "sweep_extreme_price": float(high.iloc[-1]),
                        "current_high_price": float(high.iloc[-1]),
                        "reclaim_close_price": float(close.iloc[-1]),
                        "current_cvd": curr_cvd,
                        "cvd_change": cvd_change,
                        "cvd_efficiency": round(cvd_efficiency, 4),
                        "price_excursion_atr": round(price_excursion_atr, 4),
                    }

        candle_range = max(float(high.iloc[-1] - low.iloc[-1]), 1e-8)
        close_location = float((close.iloc[-1] - low.iloc[-1]) / candle_range)
        absorption_evidence: dict = {}
        if (delta_source == "exchange_aggressor"
                and low.iloc[-1] <= recent_lows.quantile(bullish_absorption_quantile)
                and delta_ratio <= -pressure_threshold and close_location >= 0.60):
            is_absorption = True
            absorption_type = "bullish_absorption"
            desc = "Bullish Absorption: aggressive selling failed to hold price near the candle low"
        elif (delta_source == "exchange_aggressor"
                and high.iloc[-1] >= recent_highs.quantile(bearish_absorption_quantile)
                and delta_ratio >= pressure_threshold and close_location <= 0.40):
            is_absorption = True
            absorption_type = "bearish_absorption"
            desc = "Bearish Absorption: aggressive buying failed to hold price near the candle high"
        else:
            if delta_ratio > pressure_threshold:
                desc = f"Net Buying Pressure (+{delta_ratio*100:.1f}%)"
            elif delta_ratio < -pressure_threshold:
                desc = f"Net Selling Pressure ({delta_ratio*100:.1f}%)"
            else:
                desc = "Neutral Volume Delta"

        if is_absorption and delta_source == "exchange_aggressor":
            candle_time = df.index[-1]
            absorption_evidence = {
                "candle_index": len(df) - 1,
                "candle_time": (
                    candle_time.isoformat()
                    if hasattr(candle_time, "isoformat") else str(candle_time)
                ),
                "close_price": float(close.iloc[-1]),
                "delta": curr_delta,
                "delta_ratio": delta_ratio,
                "current_cvd": curr_cvd,
                "flow_source": next(iter(flow_sources)),
            }

        return VolumeDeltaResult(
            delta=round(curr_delta, 2),
            delta_ratio=round(delta_ratio, 3),
            cvd=round(curr_cvd, 2),
            cvd_zscore=round(curr_cvd_zscore, 4),
            is_absorption=is_absorption and delta_source == "exchange_aggressor",
            absorption_type=absorption_type,
            volume_spike=volume_spike,
            description=(desc if delta_source == "exchange_aggressor" else f"Estimated candle-volume proxy: {desc}"),
            source=delta_source,
            flow_source=(next(iter(flow_sources)) if trusted_aggressor else "unavailable"),
            divergence=divergence,
            divergence_evidence=divergence_evidence,
            absorption_evidence=absorption_evidence,
            cvd_tail=[round(float(v), 2) for v in cvd_series.tail(20)],
            cvd_values=[float(v) for v in cvd_series],
            delta_values=[float(v) for v in delta_series],
        )
