"""
Volume-Synchronized Probability of Informed Trading (VPIN) Engine
Estimates order flow toxicity using volume-bucketed taker flow imbalances.
Flags institutional pre-breakout toxicity spikes to guard against fakeouts and flash crashes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd


@dataclass
class VPINResult:
    vpin: Optional[float]         # None when data is insufficient
    toxic_flow_detected: bool     # True if VPIN exceeds toxicity threshold (e.g. 0.40)
    percentile_toxicity: float    # Historical percentile of current toxicity (0 - 100)
    bucket_size: float            # Target volume per bucket
    buckets_processed: int
    mean_imbalance: float
    status: str = "ok"


class VPINEngine:
    """Computes Volume-Synchronized Probability of Informed Trading (Easley et al. 2012)."""

    def __init__(self, bucket_count: int = 50, window: int = 20, toxicity_threshold: float = 0.40):
        if bucket_count < 1 or window < 1:
            raise ValueError("bucket_count and window must be positive")
        if not 0.0 <= toxicity_threshold <= 1.0:
            raise ValueError("toxicity_threshold must be between 0 and 1")
        self.bucket_count = bucket_count
        self.window = window
        self.toxicity_threshold = toxicity_threshold

    def calculate(self, df: pd.DataFrame) -> VPINResult:
        """
        Calculate VPIN across volume-synchronized buckets from OHLCV and volume delta data.
        """
        if df is None or len(df) < 20 or "volume" not in df.columns:
            return VPINResult(
                vpin=None,
                toxic_flow_detected=False,
                percentile_toxicity=50.0,
                bucket_size=1000.0,
                buckets_processed=0,
                mean_imbalance=0.0,
                status="insufficient_data",
            )

        volumes = df["volume"].values
        closes = df["close"].values
        opens = df["open"].values
        n = len(df)
        required_values = df[["open", "close", "volume"]].apply(pd.to_numeric, errors="coerce").to_numpy()
        if not np.isfinite(required_values).all() or np.any(volumes < 0):
            raise ValueError("VPIN input contains invalid or negative OHLCV values")
        if {"buy_volume", "sell_volume"}.issubset(df.columns):
            flow = df[["buy_volume", "sell_volume"]].apply(pd.to_numeric, errors="coerce").to_numpy()
            if not np.isfinite(flow).all() or np.any(flow < 0):
                raise ValueError("VPIN aggressor volume contains invalid values")
            if not np.allclose(flow.sum(axis=1), volumes, rtol=0.02, atol=1e-8):
                raise ValueError("buy_volume + sell_volume must match total volume")

        # Estimate buy/sell volume split via tick rule / body ratio
        buy_volumes = np.zeros(n)
        sell_volumes = np.zeros(n)

        for i in range(n):
            v = volumes[i]
            if "buy_volume" in df.columns and "sell_volume" in df.columns:
                buy_volumes[i] = float(df["buy_volume"].iloc[i])
                sell_volumes[i] = float(df["sell_volume"].iloc[i])
            else:
                # Approximated BVC (Bulk Volume Classification)
                c, o = closes[i], opens[i]
                if c > o:
                    buy_v = v * 0.70
                    sell_v = v * 0.30
                elif c < o:
                    buy_v = v * 0.30
                    sell_v = v * 0.70
                else:
                    buy_v = v * 0.50
                    sell_v = v * 0.50
                buy_volumes[i] = buy_v
                sell_volumes[i] = sell_v

        total_vol = np.sum(volumes)
        bucket_size = max(total_vol / max(1, self.bucket_count), 1e-6)

        # Build volume-synchronized buckets
        bucket_imbalances = []
        curr_buy = 0.0
        curr_sell = 0.0
        curr_vol = 0.0

        for i in range(n):
            bar_buy = buy_volumes[i]
            bar_sell = sell_volumes[i]
            bar_tot = volumes[i]

            while bar_tot > 0:
                needed = bucket_size - curr_vol
                if bar_tot >= needed:
                    fraction = needed / bar_tot
                    curr_buy += bar_buy * fraction
                    curr_sell += bar_sell * fraction
                    imbalance = abs(curr_buy - curr_sell)
                    bucket_imbalances.append(imbalance)

                    bar_buy -= bar_buy * fraction
                    bar_sell -= bar_sell * fraction
                    bar_tot -= needed
                    curr_buy = 0.0
                    curr_sell = 0.0
                    curr_vol = 0.0
                else:
                    curr_buy += bar_buy
                    curr_sell += bar_sell
                    curr_vol += bar_tot
                    bar_tot = 0.0

        if not bucket_imbalances or len(bucket_imbalances) < self.window:
            return VPINResult(
                vpin=None,
                toxic_flow_detected=False,
                percentile_toxicity=50.0,
                bucket_size=bucket_size,
                buckets_processed=len(bucket_imbalances),
                mean_imbalance=float(np.mean(bucket_imbalances)) if bucket_imbalances else 0.0,
                status="insufficient_data",
            )

        # Compute rolling VPIN over window N buckets
        imb_array = np.array(bucket_imbalances)
        rolling_vpin = [
            np.sum(imb_array[i - self.window : i]) / (self.window * bucket_size)
            for i in range(self.window, len(imb_array) + 1)
        ]

        current_vpin = float(np.clip(rolling_vpin[-1], 0.0, 1.0))
        pct_toxicity = float((np.array(rolling_vpin) <= current_vpin).mean() * 100.0)
        measured_flow = {"buy_volume", "sell_volume"}.issubset(df.columns)
        is_toxic = measured_flow and current_vpin >= self.toxicity_threshold

        return VPINResult(
            vpin=round(current_vpin, 4),
            toxic_flow_detected=is_toxic,
            percentile_toxicity=round(pct_toxicity, 1),
            bucket_size=round(bucket_size, 2),
            buckets_processed=len(bucket_imbalances),
            mean_imbalance=round(float(np.mean(imb_array[-self.window:])), 2),
            status="ok" if measured_flow else "estimated_advisory",
        )
