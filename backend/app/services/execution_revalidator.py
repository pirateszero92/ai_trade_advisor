"""Execution-time validation for an already-approved Tri-Core candidate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from enum import StrEnum
from typing import Any

import pandas as pd


class ExecutionIntent(StrEnum):
    MARKET_NOW = "market_now"
    PLACE_LIMIT = "place_limit"
    WAIT = "wait"
    CANCEL = "cancel"


@dataclass
class ExecutionValidationResult:
    passed: bool = False
    intent: ExecutionIntent = ExecutionIntent.CANCEL
    reason_code: str = "REJECTED"
    reasons: list[str] = field(default_factory=list)
    original_entry: float = 0.0
    executable_entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    order_type: str = ""
    entry_policy: str = ""
    auto_armed_limit: bool = False
    limit_zone_id: str = ""
    structural_rr: float = 0.0
    executable_rr: float = 0.0
    minimum_executable_rr: float = 0.0
    quote_age_seconds: float = 0.0
    spread_pct: float = 0.0
    extension_atr: float = 0.0
    snapshot_id: str = ""
    event_id: str = ""


def _safe_timestamp(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except (ValueError, TypeError):
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                return dt.timestamp()
            except (ValueError, TypeError):
                return 0.0
    return 0.0


class ExecutionRevalidator:
    """Fail-closed boundary between reviewed candidate and portfolio risk."""

    MAX_QUOTE_AGE_SECONDS = 10.0
    MAX_FUTURE_QUOTE_SKEW_SECONDS = 1.0
    MAX_SPREAD_PCT = 0.01

    @staticmethod
    def _atr(frame: pd.DataFrame, period: int = 14) -> float:
        if frame is None or frame.empty or len(frame) < 2:
            return 0.0
        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        close = frame["close"].astype(float)
        prev = close.shift(1)
        tr = pd.concat([(high-low).abs(), (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
        value = float(tr.tail(period).mean())
        return value if math.isfinite(value) and value > 0 else 0.0

    @staticmethod
    def _active_limit_zone(signal: Any, zone_id: str) -> bool:
        if not zone_id:
            return False
        pools = [
            getattr(signal, "order_blocks", []) or [],
            getattr(signal, "fvgs", []) or [],
            getattr(signal, "breaker_blocks", []) or [],
        ]
        for zone in [z for pool in pools for z in pool]:
            if str(getattr(zone, "zone_id", "")) == zone_id:
                return not bool(getattr(zone, "mitigated", False))
        # Range-boundary setups can be causal even when the boundary is
        # derived from the canonical premium/discount range rather than a
        # concrete OB/FVG object.  Require the exact boundary to still exist
        # in the same immutable signal snapshot.
        range_zones = getattr(signal, "range_zones", {}) or {}
        if zone_id == "discount_boundary":
            zone = range_zones.get("discount_zone", {}) or getattr(signal, "discount_zone", {}) or {}
            return float(zone.get("bottom", 0.0) or 0.0) > 0
        if zone_id == "premium_boundary":
            zone = range_zones.get("premium_zone", {}) or getattr(signal, "premium_zone", {}) or {}
            return float(zone.get("top", 0.0) or 0.0) > 0
        return False

    def validate(
        self,
        *,
        analysis: Any,
        decision_snapshot_id: str,
        tri_core_event_id: str,
        quote: dict[str, Any],
        direction: str,
        minimum_executable_rr: float,
        instrument_rules: Any | None = None,
        fee_bps: float = 0.0,
        slippage_bps: float = 0.0,
        max_quote_age_seconds: float | None = None,
    ) -> ExecutionValidationResult:
        result = ExecutionValidationResult(
            snapshot_id=decision_snapshot_id,
            event_id=tri_core_event_id,
            minimum_executable_rr=float(minimum_executable_rr),
        )
        now = datetime.now(timezone.utc)

        if analysis is None or str(getattr(analysis, "snapshot_id", "")) != str(decision_snapshot_id):
            result.reason_code = "SNAPSHOT_MISMATCH"
            result.reasons.append("Canonical execution snapshot is missing or replaced")
            return result
        valid_until = getattr(analysis, "valid_until", None)
        if valid_until is None or now >= valid_until:
            result.reason_code = "SNAPSHOT_EXPIRED"
            result.reasons.append("Canonical execution snapshot expired")
            return result

        signal = analysis.signal
        setup = getattr(signal, "tri_core_setup", {}) or {}
        if setup.get("actionable") is not True:
            result.reason_code = "SETUP_NOT_ACTIONABLE"
            result.reasons.append("Tri-Core candidate is no longer actionable")
            return result
        current_event = str(setup.get("event_id", ""))
        if not tri_core_event_id or current_event != str(tri_core_event_id):
            result.reason_code = "EVENT_REPLACED"
            result.reasons.append("Tri-Core event identity changed")
            return result
        if str(setup.get("direction", "")).lower() != direction:
            result.reason_code = "DIRECTION_MISMATCH"
            result.reasons.append("Execution direction differs from Tri-Core direction")
            return result

        bid = float(quote.get("bid") or 0.0)
        ask = float(quote.get("ask") or 0.0)
        if bid <= 0 or ask <= 0 or ask < bid:
            result.reason_code = "QUOTE_INVALID"
            result.intent = ExecutionIntent.WAIT
            result.reasons.append("Executable bid/ask quote is invalid")
            return result
        quote_ts = _safe_timestamp(quote.get("received_timestamp") or quote.get("timestamp"))
        if quote_ts <= 0:
            result.reason_code = "QUOTE_TIMESTAMP_MISSING"
            result.intent = ExecutionIntent.WAIT
            result.reasons.append("Executable quote has no freshness timestamp")
            return result
        raw_quote_age = now.timestamp() - quote_ts
        if raw_quote_age < -self.MAX_FUTURE_QUOTE_SKEW_SECONDS:
            result.reason_code = "QUOTE_TIMESTAMP_FUTURE"
            result.intent = ExecutionIntent.WAIT
            result.reasons.append(
                f"Executable quote timestamp is {abs(raw_quote_age):.2f}s in the future"
            )
            return result
        result.quote_age_seconds = max(0.0, raw_quote_age)
        max_age = float(max_quote_age_seconds or self.MAX_QUOTE_AGE_SECONDS)
        if result.quote_age_seconds > max_age:
            result.reason_code = "QUOTE_STALE"
            result.intent = ExecutionIntent.WAIT
            result.reasons.append(f"Executable quote is stale ({result.quote_age_seconds:.2f}s)")
            return result

        mid = (bid + ask) / 2.0
        result.spread_pct = (ask - bid) / mid if mid > 0 else math.inf
        if not math.isfinite(result.spread_pct) or result.spread_pct > self.MAX_SPREAD_PCT:
            result.reason_code = "SPREAD_TOO_WIDE"
            result.intent = ExecutionIntent.WAIT
            result.reasons.append(f"Spread too wide ({result.spread_pct:.4%})")
            return result

        structural_entry = float(signal.entry or setup.get("entry") or 0.0)
        stop = float(signal.stop_loss or setup.get("stop_loss") or 0.0)
        target = float(signal.take_profit or setup.get("take_profit") or 0.0)
        structural_rr = float(setup.get("risk_reward", getattr(signal, "risk_reward", 0.0)) or 0.0)
        result.original_entry = structural_entry
        result.stop_loss = stop
        result.take_profit = target
        result.structural_rr = structural_rr

        entry_policy = str(setup.get("entry_policy", "none"))
        order_type = str(setup.get("order_type", "none"))
        result.entry_policy = entry_policy
        result.limit_zone_id = str(setup.get("limit_zone_id", ""))

        # Respect user configured entry_mode ("limit" vs "market").
        # If entry_mode is "limit", do not execute as immediate market chase;
        # require a limit entry at structural entry or wait for limit retest.
        configured_entry_mode = getattr(analysis, "entry_mode", "")
        if configured_entry_mode == "limit" and order_type == "market":
            zone_id = result.limit_zone_id
            if self._active_limit_zone(signal, zone_id):
                executable_entry = structural_entry
                order_type = "limit"
                entry_policy = "limit_retest_only"
                result.order_type = order_type
                result.entry_policy = entry_policy
                result.auto_armed_limit = True
                result.reasons.append(
                    f"Entry mode is 'limit'; armed causal limit {zone_id} at structural entry {structural_entry}"
                )
            else:
                # If no causal limit zone is attached to this market setup, arm limit at structural entry
                executable_entry = structural_entry
                order_type = "limit"
                entry_policy = "limit_retest_only"
                result.order_type = order_type
                result.entry_policy = entry_policy
                result.auto_armed_limit = True
                result.reasons.append(
                    f"Entry mode is 'limit'; armed limit at structural entry {structural_entry}"
                )
        elif entry_policy == "market_eligible" and order_type == "market":
            executable_entry = ask if direction == "long" else bid
            atr = self._atr(analysis.frame)
            if atr <= 0:
                result.reason_code = "ATR_UNAVAILABLE"
                result.intent = ExecutionIntent.WAIT
                result.reasons.append("ATR unavailable for no-chase revalidation")
                return result
            # Only adverse extension is chasing: a higher entry hurts LONG and
            # a lower entry hurts SHORT.  A better-than-planned quote must not
            # be rejected merely because it is far from the structural entry.
            adverse_extension = (
                max(0.0, executable_entry - structural_entry)
                if direction == "long"
                else max(0.0, structural_entry - executable_entry)
            )
            result.extension_atr = adverse_extension / atr
            policy = analysis.config_snapshot.get("tri_core_policy", {}) if isinstance(analysis.config_snapshot, dict) else {}
            max_extension = float(policy.get("market_entry_max_extension_atr", 0.25))
            no_chase = float(policy.get("no_chase_max_extension_atr", 0.60))
            if result.extension_atr > max_extension:
                zone_id = result.limit_zone_id
                if self._active_limit_zone(signal, zone_id):
                    # Do not mutate the cached canonical signal.  Resolve this
                    # execution attempt to a pending causal limit locally; the
                    # caller persists the resolved policy in OMS metadata.
                    executable_entry = structural_entry
                    order_type = "limit"
                    entry_policy = "limit_retest_only"
                    result.order_type = order_type
                    result.entry_policy = entry_policy
                    result.auto_armed_limit = True
                    result.reasons.append(
                        f"Market extended {result.extension_atr:.2f} ATR; armed causal limit {zone_id}"
                    )
                else:
                    if result.extension_atr > no_chase:
                        result.reason_code = "NO_CHASE"
                        result.intent = ExecutionIntent.CANCEL
                    else:
                        result.reason_code = "MARKET_EXTENSION_EXCEEDED"
                        result.intent = ExecutionIntent.WAIT
                    result.reasons.append(
                        f"Price extended {result.extension_atr:.2f} ATR; no active causal retest zone"
                    )
                    return result
        elif entry_policy == "limit_retest_only" and order_type == "limit":
            zone_id = str(setup.get("limit_zone_id", ""))
            if not self._active_limit_zone(signal, zone_id):
                result.reason_code = "LIMIT_ZONE_INVALID"
                result.reasons.append("Causal limit zone is missing or mitigated")
                return result
            executable_entry = structural_entry
        else:
            result.reason_code = "ENTRY_POLICY_INVALID"
            result.reasons.append("Tri-Core entry policy/order type is not executable")
            return result

        if instrument_rules is not None:
            if direction == "long":
                executable_entry = instrument_rules.price(
                    executable_entry, upward=order_type == "market"
                )
                stop = instrument_rules.price(stop, upward=False)
                target = instrument_rules.price(target, upward=False)
            else:
                executable_entry = instrument_rules.price(executable_entry, upward=False)
                stop = instrument_rules.price(stop, upward=True)
                target = instrument_rules.price(target, upward=True)

        result.order_type = order_type
        result.entry_policy = entry_policy
        result.executable_entry = executable_entry
        result.stop_loss = stop
        result.take_profit = target
        # Geometry check: verify the executable entry still fits between SL and TP.
        # This catches two cases: (a) instrument_rules rounding that breaks geometry,
        # and (b) standalone / test-mode use where RiskEngine is not in the call chain.
        # In the normal auto-pilot pipeline RiskEngine also checks geometry, so this is
        # intentional belt-and-suspenders — it provides a fast, clear rejection code
        # before any cost calculations rather than a second hard gate.
        if direction == "long":
            geometry_ok = stop < executable_entry < target
        else:
            geometry_ok = target < executable_entry < stop
        if not geometry_ok:
            result.reason_code = "GEOMETRY_INVALID"
            result.reasons.append("Executable entry no longer fits structural SL/TP geometry")
            return result

        # Minimum SL Cushion Guard: for market execution, ensure entry is not dangerously close to Stop Loss.
        sl_cushion_dist = abs(executable_entry - stop)
        sl_cushion_pct = sl_cushion_dist / executable_entry if executable_entry > 0 else 0.0
        MIN_SL_CUSHION_PCT = 0.001  # 0.10% minimum stop cushion (aligned with tight SMC stops)

        if order_type == "market" and sl_cushion_pct < MIN_SL_CUSHION_PCT:
            zone_id = result.limit_zone_id
            if self._active_limit_zone(signal, zone_id):
                executable_entry = structural_entry
                order_type = "limit"
                entry_policy = "limit_retest_only"
                result.order_type = order_type
                result.entry_policy = entry_policy
                result.auto_armed_limit = True
                result.reasons.append(
                    f"Market quote too close to SL ({sl_cushion_pct:.2%}; min {MIN_SL_CUSHION_PCT:.2%}); "
                    f"armed causal limit {zone_id} at {structural_entry}"
                )
            else:
                result.reason_code = "SL_CUSHION_TOO_TIGHT"
                result.intent = ExecutionIntent.WAIT
                result.reasons.append(
                    f"Market entry too close to Stop Loss ({sl_cushion_pct:.3%}; "
                    f"minimum safe cushion is {MIN_SL_CUSHION_PCT:.2%})"
                )
                return result

        cost_rate = (float(fee_bps) * 2.0 + float(slippage_bps) * 2.0) / 10_000.0

        def executable_rr_for(entry_value: float) -> float:
            execution_cost = entry_value * cost_rate + (ask - bid)
            risk_dist = abs(entry_value - stop) + execution_cost
            reward_dist = max(abs(target - entry_value) - execution_cost, 0.0)
            return round(reward_dist / max(risk_dist, 1e-12), 4)

        result.executable_rr = executable_rr_for(executable_entry)

        # R:R gate: if market entry is below the floor, attempt to arm a causal
        # limit at the structural entry before rejecting.  This preserves the
        # opportunity without accepting a degraded fill price.
        if result.executable_rr < result.minimum_executable_rr:
            market_rr = result.executable_rr
            zone_id = result.limit_zone_id
            original_policy = str(setup.get("entry_policy", "none"))
            original_order_type = str(setup.get("order_type", "none"))

            can_arm_causal_limit = (
                original_policy == "market_eligible"
                and original_order_type == "market"
                and self._active_limit_zone(signal, zone_id)
            )

            if can_arm_causal_limit:
                limit_entry = structural_entry
                if instrument_rules is not None:
                    limit_entry = instrument_rules.price(limit_entry, upward=False)

                limit_geometry_ok = (
                    stop < limit_entry < target
                    if direction == "long"
                    else target < limit_entry < stop
                )
                limit_rr = executable_rr_for(limit_entry) if limit_geometry_ok else 0.0

                if limit_geometry_ok and limit_rr >= result.minimum_executable_rr:
                    # Upgrade to causal limit — caller persists this in OMS metadata.
                    result.executable_entry = limit_entry
                    result.order_type = "limit"
                    result.entry_policy = "limit_retest_only"
                    result.auto_armed_limit = True
                    result.executable_rr = limit_rr
                    result.reasons.append(
                        f"Market R:R {market_rr:.2f} < min {result.minimum_executable_rr:.2f};"
                        f" armed causal limit {zone_id} at structural entry (R:R {limit_rr:.2f})"
                    )
                else:
                    result.reason_code = "EXECUTABLE_RR_TOO_LOW"
                    result.intent = ExecutionIntent.WAIT
                    result.reasons.append(
                        f"R:R {market_rr:.2f} < min {result.minimum_executable_rr:.2f};"
                        f" causal limit also insufficient (limit R:R {limit_rr:.2f})"
                    )
                    return result
            else:
                result.reason_code = "EXECUTABLE_RR_TOO_LOW"
                result.intent = ExecutionIntent.WAIT
                result.reasons.append(
                    f"R:R {result.executable_rr:.2f} < min {result.minimum_executable_rr:.2f}"
                )
                return result

        result.passed = True
        result.reason_code = "PASSED"
        result.intent = (
            ExecutionIntent.PLACE_LIMIT
            if (result.order_type == "limit" or result.auto_armed_limit)
            else ExecutionIntent.MARKET_NOW
        )
        return result


execution_revalidator = ExecutionRevalidator()
