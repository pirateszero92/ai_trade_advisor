"""Micro-Timeframe Sniper Trigger Engine (3M / 5M).

Detects lower-timeframe internal structure shifts (micro-CHoCH) and delta absorption
within active 15M macro SMC zones (Breaker Blocks, Order Blocks, FVGs) to trigger
immediate execution with tighter stops and boosted R:R.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal
import numpy as np
import pandas as pd


@dataclass
class MicroTriggerSetup:
    actionable: bool = False
    direction: Literal["long", "short", "wait"] = "wait"
    micro_timeframe: str = "3m"
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward: float = 0.0
    micro_choch: bool = False
    micro_absorption: bool = False
    trigger_type: str = "none"
    micro_swing_level: float | None = None
    evidence: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _micro_atr(frame: pd.DataFrame, length: int = 14) -> float:
    """Wilder ATR on micro timeframe."""
    if len(frame) < 2:
        return 0.0
    prev = frame["close"].shift(1)
    tr = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - prev).abs(),
        (frame["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1.0 / max(1, min(length, len(frame))), adjust=False).mean().iloc[-1])


class MicroTriggerEngine:
    """Evaluates micro-timeframe 3M/5M price action for high-precision entry."""

    @classmethod
    def evaluate(
        cls,
        *,
        side: Literal["long", "short"],
        macro_zone: Any,
        macro_target: float | None,
        frame_micro: pd.DataFrame,
        micro_timeframe: str = "3m",
        min_rr: float = 2.0,
        macro_atr: float = 0.0,
    ) -> MicroTriggerSetup:
        if frame_micro is None or len(frame_micro) < 5:
            return MicroTriggerSetup(rejection_reasons=["Insufficient micro-timeframe candles"])

        if macro_target is None or macro_target <= 0:
            return MicroTriggerSetup(rejection_reasons=["No valid macro take-profit target"])

        current_entry = float(frame_micro["close"].iloc[-1])
        micro_atr = _micro_atr(frame_micro)
        buffer = max(current_entry * 0.0003, micro_atr * 0.20)

        zone_top = float(macro_zone.top)
        zone_bottom = float(macro_zone.bottom)
        zone_kind = getattr(macro_zone, "kind", "ob")

        # 1. Zone Interaction Check
        front_run_buffer = 0.15 * macro_atr if (macro_atr > 0 and zone_kind == "breaker") else 0.0
        recent_window = frame_micro.iloc[-15:]

        if side == "long":
            zone_contact = any(
                float(row["low"]) <= (zone_top + front_run_buffer) and float(row["high"]) >= zone_bottom
                for _, row in recent_window.iterrows()
            )
        else:
            zone_contact = any(
                float(row["high"]) >= (zone_bottom - front_run_buffer) and float(row["low"]) <= zone_top
                for _, row in recent_window.iterrows()
            )

        if not zone_contact:
            return MicroTriggerSetup(
                rejection_reasons=[f"Micro price action has not touched macro {zone_kind.upper()} zone"]
            )

        # 2. Micro-CHoCH (Internal Structure Shift)
        highs = recent_window["high"].to_numpy()
        lows = recent_window["low"].to_numpy()
        closes = recent_window["close"].to_numpy()
        n = len(recent_window)

        micro_choch = False
        micro_swing_level: float | None = None
        micro_extreme: float | None = None

        if side == "long":
            micro_extreme = float(np.min(lows))
            if n >= 4:
                peaks = [
                    (i, highs[i]) for i in range(1, n - 1)
                    if highs[i] > highs[i - 1] and highs[i] >= highs[i + 1]
                ]
                if peaks:
                    pivot_idx, pivot_high = max(peaks, key=lambda p: p[1])
                    micro_swing_level = float(pivot_high)
                    if closes[-1] >= pivot_high or (highs[-1] > pivot_high and closes[-1] > closes[-2]):
                        micro_choch = True
                else:
                    threshold = float(np.percentile(highs[:-1], 70))
                    if closes[-1] > threshold:
                        micro_choch = True
                        micro_swing_level = threshold
        else:
            micro_extreme = float(np.max(highs))
            if n >= 4:
                troughs = [
                    (i, lows[i]) for i in range(1, n - 1)
                    if lows[i] < lows[i - 1] and lows[i] <= lows[i + 1]
                ]
                if troughs:
                    pivot_idx, pivot_low = min(troughs, key=lambda p: p[1])
                    micro_swing_level = float(pivot_low)
                    if closes[-1] <= pivot_low or (lows[-1] < pivot_low and closes[-1] < closes[-2]):
                        micro_choch = True
                else:
                    threshold = float(np.percentile(lows[:-1], 30))
                    if closes[-1] < threshold:
                        micro_choch = True
                        micro_swing_level = threshold

        # 3. Micro Delta Absorption / Flow Confirmation
        has_delta = "volume_delta" in frame_micro.columns
        if not has_delta:
            return MicroTriggerSetup(
                rejection_reasons=["Exchange-derived micro volume_delta is required for micro flow confirmation"]
            )

        last_row = frame_micro.iloc[-1]
        prev_row = frame_micro.iloc[-2] if len(frame_micro) >= 2 else last_row

        curr_delta = float(last_row.get("volume_delta", 0.0))
        prev_delta = float(prev_row.get("volume_delta", 0.0))
        if not (np.isfinite(curr_delta) and np.isfinite(prev_delta)):
            return MicroTriggerSetup(
                rejection_reasons=["Non-finite micro delta values detected"]
            )

        if side == "long":
            micro_absorption = curr_delta > 0 or (prev_delta < 0 and curr_delta > prev_delta)
        else:
            micro_absorption = curr_delta < 0 or (prev_delta > 0 and curr_delta < prev_delta)

        if not micro_absorption:
            return MicroTriggerSetup(
                rejection_reasons=[f"Micro CVD absorption not confirmed on {micro_timeframe}"]
            )

        if not micro_choch:
            return MicroTriggerSetup(
                rejection_reasons=[f"Awaiting micro-CHoCH structure shift on {micro_timeframe}"]
            )

        # 4. Compute Tight Micro Stop Loss and R:R
        if side == "long":
            stop_loss = (micro_extreme if micro_extreme is not None else zone_bottom) - buffer
            valid_geom = stop_loss < current_entry < macro_target
        else:
            stop_loss = (micro_extreme if micro_extreme is not None else zone_top) + buffer
            valid_geom = macro_target < current_entry < stop_loss

        if not valid_geom:
            return MicroTriggerSetup(
                rejection_reasons=["Micro geometry invalid (stop or target direction inverted)"]
            )

        rr = abs(macro_target - current_entry) / abs(current_entry - stop_loss)
        if rr < min_rr:
            return MicroTriggerSetup(
                rejection_reasons=[f"Micro R:R {rr:.2f} < required {min_rr:.2f}"]
            )

        evidence = [
            f"Micro-CHoCH confirmed on {micro_timeframe} (pivot {micro_swing_level:.2f})",
            f"Micro flow absorption confirmed on {micro_timeframe}",
            f"Tight micro invalidation at {stop_loss:.2f} boosts R:R to {rr:.2f}x targeting {macro_target:.2f}",
        ]

        return MicroTriggerSetup(
            actionable=True,
            direction=side,
            micro_timeframe=micro_timeframe,
            entry=round(current_entry, 6),
            stop_loss=round(stop_loss, 6),
            take_profit=round(macro_target, 6),
            risk_reward=round(rr, 2),
            micro_choch=micro_choch,
            micro_absorption=micro_absorption,
            trigger_type="micro_sniper",
            micro_swing_level=micro_swing_level,
            evidence=evidence,
        )
