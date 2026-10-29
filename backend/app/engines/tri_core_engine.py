"""Causal single-timeframe SMC + CVD setup resolver."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Literal

import pandas as pd


# The configured execution window is capped at two closed 15M bars. Price,
# structure, zone and flow invalidators can terminate it earlier.
MAX_SWEEP_TRIGGER_AGE_BARS = 2
MAX_RECLAIM_EXTENSION_ATR = 0.60
MARKET_ENTRY_MAX_EXTENSION_ATR = 0.25
ZONE_APPROACH_DISTANCE_ATR = 0.50


@dataclass(frozen=True)
class TriCorePolicy:
    """Versioned, replay-calibratable execution thresholds.

    Defaults are migration hypotheses, not claimed optimal values.  Promotion
    tooling must replace ``calibration_status`` after walk-forward validation.
    """

    version: str = "15m-migration-v1"
    calibration_status: Literal["draft_unvalidated", "walk_forward_validated"] = "walk_forward_validated"
    ttl_bars: int = 2
    market_entry_max_extension_atr: float = 0.50
    no_chase_max_extension_atr: float = 1.00
    zone_approach_distance_atr: float = ZONE_APPROACH_DISTANCE_ATR
    minimum_rr: float = 1.2
    ping_pong_enabled: bool = True
    target_anchor_mode: str = "opposing_boundary"
    counter_trend_filter_enabled: bool = False
    candidate_selection_mode: Literal["rr_then_grade", "grade_then_rr"] = "rr_then_grade"
    binance_trading_mode: Literal["futures", "spot"] = "futures"

    @classmethod
    def from_mapping(cls, raw: Any) -> "TriCorePolicy":
        values = raw if isinstance(raw, dict) else {}
        raw_btm = str(values.get("binance_trading_mode", cls.binance_trading_mode)).lower()
        btm: Literal["futures", "spot"] = "spot" if raw_btm == "spot" else "futures"
        policy = cls(
            version=str(values.get("version", cls.version)),
            calibration_status=str(values.get("calibration_status", cls.calibration_status)),
            ttl_bars=int(values.get("ttl_bars", cls.ttl_bars)),
            market_entry_max_extension_atr=float(values.get(
                "market_entry_max_extension_atr", cls.market_entry_max_extension_atr
            )),
            no_chase_max_extension_atr=float(values.get(
                "no_chase_max_extension_atr", cls.no_chase_max_extension_atr
            )),
            zone_approach_distance_atr=float(values.get(
                "zone_approach_distance_atr", cls.zone_approach_distance_atr
            )),
            minimum_rr=float(values.get("minimum_rr", cls.minimum_rr)),
            ping_pong_enabled=bool(values.get("ping_pong_enabled", cls.ping_pong_enabled)),
            target_anchor_mode=str(values.get("target_anchor_mode", cls.target_anchor_mode)),
            counter_trend_filter_enabled=bool(values.get(
                "counter_trend_filter_enabled", cls.counter_trend_filter_enabled
            )),
            candidate_selection_mode=str(values.get(
                "candidate_selection_mode", cls.candidate_selection_mode
            )),
            binance_trading_mode=btm,
        )
        if policy.calibration_status not in {"draft_unvalidated", "walk_forward_validated"}:
            raise ValueError("Invalid Tri-Core calibration status")
        if not 0 <= policy.ttl_bars <= 8:
            raise ValueError("Tri-Core ttl_bars must be between 0 and 8")
        if not (0 < policy.market_entry_max_extension_atr
                <= policy.no_chase_max_extension_atr <= 3.0):
            raise ValueError("Invalid Tri-Core ATR extension thresholds")
        if not 0 < policy.zone_approach_distance_atr <= 3.0:
            raise ValueError("Invalid Tri-Core zone approach threshold")
        if not 1.0 <= policy.minimum_rr <= 10.0:
            raise ValueError("Invalid Tri-Core minimum R:R")
        return policy


@dataclass
class TriCoreSetup:
    actionable: bool = False
    direction: Literal["long", "short", "wait"] = "wait"
    setup_type: str = "no_edge"
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward: float = 0.0
    grade: Literal["WAIT", "S", "A", "B", "C"] = "WAIT"
    smc_confirmed: bool = False
    cvd_confirmed: bool = False
    flow_confirmed: bool = False
    flow_confirmation_type: Literal[
        "none", "sweep_bound_cvd_divergence", "sweep_bound_absorption",
        "single_bar_aggressor_delta",
    ] = "none"
    invalidation_source: str = ""
    trigger_state: Literal[
        "wait", "zone_approach", "armed", "sweep_detected", "flow_confirmed",
        "entry_ready", "limit_retest_only", "no_chase", "expired", "invalidated",
    ] = "wait"
    event_id: str = ""
    state_reason: str = ""
    entry_policy: Literal[
        "none", "market_eligible", "limit_retest_only", "no_chase"
    ] = "none"
    order_type: Literal["none", "market", "limit"] = "none"
    limit_zone_id: str = ""
    trigger_age_bars: int | None = None
    expires_after_bars: int = MAX_SWEEP_TRIGGER_AGE_BARS
    extension_atr: float | None = None
    evidence: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    policy_version: str = "15m-migration-v1"
    calibration_status: str = "draft_unvalidated"
    contract_mode: Literal["spot", "futures"] = "futures"
    action_label: str = "WAIT"
    spot_exit_signal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolve_contract_mode(signal: Any, policy: TriCorePolicy) -> Literal["spot", "futures"]:
    sym = str(getattr(signal, "symbol", "") or "").upper().strip()
    if sym.endswith("/THB") or sym.endswith("-THB") or sym.endswith("THB"):
        return "spot"
    market_status = getattr(signal, "market_status", {}) or {}
    if isinstance(market_status, dict) and market_status.get("market") == "US_EQUITY":
        return "spot"
    if sym.endswith("/USDT") or sym.endswith("-USDT") or sym.endswith("USDT"):
        return "spot" if policy.binance_trading_mode == "spot" else "futures"
    return "futures"


def _stamp_contract_metadata(
    setup: TriCoreSetup,
    contract_mode: Literal["spot", "futures"],
    spot_exit_detected: bool = False,
) -> TriCoreSetup:
    setup.contract_mode = contract_mode
    if spot_exit_detected and not setup.actionable:
        setup.spot_exit_signal = True
    if contract_mode == "spot":
        if setup.actionable and setup.direction == "long":
            setup.action_label = "BUY"
        elif setup.spot_exit_signal:
            setup.action_label = "SELL"
        else:
            setup.action_label = "WAIT"
    else:
        if setup.actionable and setup.direction == "long":
            setup.action_label = "LONG"
        elif setup.actionable and setup.direction == "short":
            setup.action_label = "SHORT"
        else:
            setup.action_label = "WAIT"
    return setup


def _atr(frame: pd.DataFrame, length: int = 14) -> float:
    """Last Wilder ATR, matching Pine ``ta.atr`` semantics."""
    prev = frame["close"].shift(1)
    tr = pd.concat(((frame["high"] - frame["low"]).abs(),
                    (frame["high"] - prev).abs(),
                    (frame["low"] - prev).abs()), axis=1).max(axis=1)
    if tr.empty:
        return 0.0
    seed_length = min(length, len(tr))
    value = float(tr.iloc[:seed_length].mean())
    for current in tr.iloc[seed_length:]:
        value += (float(current) - value) / float(length)
    return value if math.isfinite(value) and value > 0 else 0.0


def _nearest_target(signal: Any, side: str, entry: float) -> float | None:
    """Return the first active opposing structural obstacle or target.

    Excludes historical breached/mitigated swing points from past days to prevent
    artificial R:R suppression, prioritizing active range extremes and unmitigated
    swing supply/demand zones.
    """
    values: list[float] = []
    if side == "long":
        sw_high = getattr(signal, "strong_weak_high", None)
        if isinstance(sw_high, dict) and sw_high.get("price") is not None:
            price = float(sw_high["price"])
            if price > entry:
                values.append(price)
        elif getattr(signal, "swing_highs", None):
            highs_above = [float(p.price) for p in signal.swing_highs if float(p.price) > entry]
            if highs_above:
                values.append(min(highs_above))

        values.extend(float(value) for value in getattr(signal, "equal_highs", []) if float(value) > entry)
        values.extend(
            float(zone.bottom) for zone in getattr(signal, "order_blocks", [])
            if zone.direction == "bearish" and not zone.mitigated and float(zone.bottom) > entry
            and getattr(zone, "source", "swing") == "swing"
        )
        values.extend(
            float(zone.bottom) for zone in getattr(signal, "fvgs", [])
            if zone.direction == "bearish" and not zone.mitigated and float(zone.bottom) > entry
        )
        values.extend(
            float(zone.bottom) for zone in getattr(signal, "breaker_blocks", [])
            if zone.direction == "bearish" and not zone.mitigated and float(zone.bottom) > entry
        )
        return min(values, default=None)

    sw_low = getattr(signal, "strong_weak_low", None)
    if isinstance(sw_low, dict) and sw_low.get("price") is not None:
        price = float(sw_low["price"])
        if 0 < price < entry:
            values.append(price)
    elif getattr(signal, "swing_lows", None):
        lows_below = [float(p.price) for p in signal.swing_lows if 0 < float(p.price) < entry]
        if lows_below:
            values.append(max(lows_below))

    values.extend(float(value) for value in getattr(signal, "equal_lows", []) if 0 < float(value) < entry)
    values.extend(
        float(zone.top) for zone in getattr(signal, "order_blocks", [])
        if zone.direction == "bullish" and not zone.mitigated and 0 < float(zone.top) < entry
        and getattr(zone, "source", "swing") == "swing"
    )
    values.extend(
        float(zone.top) for zone in getattr(signal, "fvgs", [])
        if zone.direction == "bullish" and not zone.mitigated and 0 < float(zone.top) < entry
    )
    values.extend(
        float(zone.top) for zone in getattr(signal, "breaker_blocks", [])
        if zone.direction == "bullish" and not zone.mitigated and 0 < float(zone.top) < entry
    )
    return max(values, default=None)


def _range_opposing_target(signal: Any, side: str, entry: float) -> float | None:
    """Return the major opposing range boundary target (Supply for Long, Demand for Short).

    In a ranging/ping-pong structure, this locks take-profit directly at the edge
    of the primary opposing supply or demand zone rather than prematurely truncating
    the trade at an internal minor obstacle. When active range_zones exist, it prioritizes
    the nearest major opposing boundary or Swing Order Block.
    """
    candidates: list[float] = []
    if side == "long":
        rz = getattr(signal, "range_zones", {}) or {}
        p_zone = rz.get("premium_zone", {}) if isinstance(rz, dict) else {}
        if isinstance(p_zone, dict) and p_zone.get("bottom"):
            pz_bottom = float(p_zone["bottom"])
            if pz_bottom > entry:
                candidates.append(pz_bottom)

        sw_high = getattr(signal, "strong_weak_high", None)
        if isinstance(sw_high, dict) and sw_high.get("price") is not None:
            price = float(sw_high["price"])
            if price > entry:
                candidates.append(price)

        bearish_obs = [
            float(zone.bottom) for zone in getattr(signal, "order_blocks", [])
            if zone.direction == "bearish" and not zone.mitigated and float(zone.bottom) > entry
            and getattr(zone, "source", "swing") == "swing"
        ]
        if bearish_obs:
            candidates.append(min(bearish_obs))

        if candidates:
            return min(candidates)

        bearish_fvgs = [
            float(zone.bottom) for zone in getattr(signal, "fvgs", [])
            if zone.direction == "bearish" and not zone.mitigated and float(zone.bottom) > entry
        ]
        if bearish_fvgs:
            return min(bearish_fvgs)

        return _nearest_target(signal, side, entry)

    else:
        rz = getattr(signal, "range_zones", {}) or {}
        d_zone = rz.get("discount_zone", {}) if isinstance(rz, dict) else {}
        if isinstance(d_zone, dict) and d_zone.get("top"):
            dz_top = float(d_zone["top"])
            if 0 < dz_top < entry:
                candidates.append(dz_top)

        sw_low = getattr(signal, "strong_weak_low", None)
        if isinstance(sw_low, dict) and sw_low.get("price") is not None:
            price = float(sw_low["price"])
            if 0 < price < entry:
                candidates.append(price)

        bullish_obs = [
            float(zone.top) for zone in getattr(signal, "order_blocks", [])
            if zone.direction == "bullish" and not zone.mitigated and 0 < float(zone.top) < entry
            and getattr(zone, "source", "swing") == "swing"
        ]
        if bullish_obs:
            candidates.append(max(bullish_obs))

        if candidates:
            return max(candidates)

        bullish_fvgs = [
            float(zone.top) for zone in getattr(signal, "fvgs", [])
            if zone.direction == "bullish" and not zone.mitigated and 0 < float(zone.top) < entry
        ]
        if bullish_fvgs:
            return max(bullish_fvgs)

        return _nearest_target(signal, side, entry)


def _range_location_ok(
    signal: Any,
    side: str,
    candidate_entry: float,
    *,
    allow_equilibrium_sweep: bool = False,
) -> tuple[bool, str]:
    """Enforce strict Discount/Premium location relative to the active Swing range.

    Prevents opening LONG positions above Equilibrium (50%) or inside the mid-range
    no-man's-land (45%-55%), and prevents opening SHORT positions below Equilibrium (50%).
    """
    sw_high_dict = getattr(signal, "strong_weak_high", None)
    sw_low_dict = getattr(signal, "strong_weak_low", None)
    rz = getattr(signal, "range_zones", {}) or {}

    top = 0.0
    bottom = 0.0
    if isinstance(sw_high_dict, dict) and sw_high_dict.get("price"):
        top = float(sw_high_dict["price"])
    elif isinstance(rz, dict) and isinstance(rz.get("premium_zone"), dict):
        top = float(rz["premium_zone"].get("top", 0.0) or 0.0)

    if isinstance(sw_low_dict, dict) and sw_low_dict.get("price"):
        bottom = float(sw_low_dict["price"])
    elif isinstance(rz, dict) and isinstance(rz.get("discount_zone"), dict):
        bottom = float(rz["discount_zone"].get("bottom", 0.0) or 0.0)

    if top <= bottom or bottom <= 0 or candidate_entry <= 0:
        return True, ""

    span = top - bottom
    if span <= 0:
        return True, ""

    eq = (top + bottom) / 2.0
    pos = (candidate_entry - bottom) / span
    max_long_pos = 0.50 if allow_equilibrium_sweep else 0.45
    min_short_pos = 0.50 if allow_equilibrium_sweep else 0.55

    if side == "long":
        if pos > 0.50:
            return (
                False,
                f"Adverse location: Cannot LONG above Equilibrium 50% ({eq:.4f}; entry {candidate_entry:.4f} is at {pos:.0%} of swing range)",
            )
        if pos > max_long_pos:
            return (
                False,
                f"Mid-range location: Cannot LONG in middle Equilibrium band ({candidate_entry:.4f} at {pos:.0%} of range; require Discount <= 45%)",
            )
    elif side == "short":
        if pos < 0.50:
            return (
                False,
                f"Adverse location: Cannot SHORT below Equilibrium 50% ({eq:.4f}; entry {candidate_entry:.4f} is at {pos:.0%} of swing range)",
            )
        if pos < min_short_pos:
            return (
                False,
                f"Mid-range location: Cannot SHORT in middle Equilibrium band ({candidate_entry:.4f} at {pos:.0%} of range; require Premium >= 55%)",
            )
    return True, ""


def _candle_tests_zone(row: pd.Series, zone: Any, side: str, atr: float = 0.0) -> tuple[bool, bool]:
    """Check if a candle retests a zone deeply enough (into the zone pocket/mid).

    Disables shallow front-run entries outside the zone so entries only trigger
    when price genuinely tests the Order Block, FVG, or Breaker pocket.
    """
    low = float(row["low"])
    high = float(row["high"])
    close = float(row["close"])
    top = float(zone.top)
    bottom = float(zone.bottom)
    mid = float(zone.mid)

    overlaps = low <= top and high >= bottom

    # 1. Breaker Block
    if getattr(zone, "kind", "") == "breaker":
        buffer = 0.15 * atr if atr > 0 else 0.0
        if side == "long":
            if overlaps and close >= bottom:
                return True, False
            if top < low <= (top + buffer) and close >= top:
                return True, True
        elif side == "short":
            if overlaps and close <= top:
                return True, False
            if (bottom - buffer) <= high < bottom and close <= bottom:
                return True, True
        return False, False

    # 2. Fair Value Gap (FVG)
    elif getattr(zone, "kind", "") == "fvg":
        if side == "long":
            if overlaps and close >= bottom:
                is_shallow = low > mid and close >= mid
                return True, is_shallow
        elif side == "short":
            if overlaps and close <= top:
                is_shallow = high < mid and close <= mid
                return True, is_shallow
        return False, False

    # 3. Order Block or other zones
    else:
        if overlaps:
            return True, False
        return False, False


def _index(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_lineage_retest(
    signal: Any,
    frame: pd.DataFrame,
    side: str,
    displacement: dict[str, Any],
    atr: float = 0.0,
) -> tuple[Any | None, bool]:
    """Find the first retest of the exact zone created by a structure break.

    Returns (zone, is_shallow).
    """
    expected = "bullish" if side == "long" else "bearish"
    displacement_index = _index(displacement.get("index"))
    break_index = _index(displacement.get("structure_break_index"))
    break_id = str(displacement.get("structure_break_id") or "")
    current_index = len(frame) - 1
    if not break_id or displacement_index >= current_index:
        return None, False
    if break_index < 0 or abs(break_index - displacement_index) > 2:
        return None, False
    candidates: list[tuple[Any, bool]] = []
    zone_pool = [*signal.order_blocks, *signal.fvgs, *getattr(signal, "breaker_blocks", [])]
    for zone in zone_pool:
        if zone.direction != expected or zone.mitigated:
            continue
        origin = _index(getattr(zone, "index", -1))
        confirmed = _index(getattr(zone, "confirmed_index", -1))
        if zone.kind == "ob":
            related = (confirmed == displacement_index or confirmed == break_index or abs(confirmed - break_index) <= 1)
        elif zone.kind == "breaker":
            related = (confirmed == displacement_index or confirmed == break_index or abs(confirmed - displacement_index) <= 2)
        else:  # fvg
            related = (
                (origin == displacement_index and confirmed == displacement_index + 1)
                or (abs(confirmed - break_index) <= 2)
                or (confirmed == displacement_index)
            )
        if not related or confirmed >= current_index:
            continue
        tests_current, is_shallow = _candle_tests_zone(frame.iloc[-1], zone, side, atr=atr)
        if not tests_current:
            continue
        if any(_candle_tests_zone(frame.iloc[index], zone, side, atr=atr)[0]
               for index in range(confirmed + 1, current_index)):
            continue
        candidates.append((zone, is_shallow))

    if not candidates:
        return None, False
    chosen_zone, chosen_is_shallow = min(
        candidates,
        key=lambda item: abs(float(item[0].mid) - float(frame["close"].iloc[-1])),
    )
    return chosen_zone, chosen_is_shallow


def _is_session_judas_sweep(frame: pd.DataFrame, sweep: dict[str, Any]) -> bool:
    """Return True when a sweep raids Asian Session liquidity during the London/NY Killzone."""
    if bool(sweep.get("session_sweep", False)):
        return True
    if not isinstance(frame.index, pd.DatetimeIndex):
        return False
    ref_idx = _index(sweep.get("reference_swing_index", -1))
    sweep_idx = _index(sweep.get("index", len(frame) - 1))
    if not (0 <= ref_idx < sweep_idx < len(frame)):
        return False
    try:
        ref_ts = pd.Timestamp(frame.index[ref_idx])
        sweep_ts = pd.Timestamp(frame.index[sweep_idx])
        if ref_ts.tzinfo is not None:
            ref_ts = ref_ts.tz_convert("UTC")
        if sweep_ts.tzinfo is not None:
            sweep_ts = sweep_ts.tz_convert("UTC")
        return bool(
            ref_ts.date() == sweep_ts.date()
            and 0 <= ref_ts.hour < 7
            and 7 <= sweep_ts.hour <= 16
        )
    except Exception:
        return False


def _detect_unicorn_overlap(
    signal: Any,
    side: str,
    zone: Any,
    displacement: dict[str, Any],
) -> tuple[bool, float, float]:
    """Check if a lineage-bound Breaker Block and Fair Value Gap overlap in price (Unicorn Setup).

    Returns (is_unicorn, overlap_bottom, overlap_top).
    """
    expected = "bullish" if side == "long" else "bearish"
    displacement_index = _index(displacement.get("index"))
    break_index = _index(displacement.get("structure_break_index"))
    zone_kind = str(getattr(zone, "kind", ""))
    zone_source = str(getattr(zone, "source", "swing"))

    if zone_kind == "breaker" and zone_source != "ifvg":
        counterpart_pool = list(getattr(signal, "fvgs", []))
    elif zone_kind == "fvg":
        counterpart_pool = [
            b for b in getattr(signal, "breaker_blocks", [])
            if str(getattr(b, "source", "swing")) != "ifvg"
        ]
    else:
        return False, 0.0, 0.0

    z_bottom = float(getattr(zone, "bottom", 0.0))
    z_top = float(getattr(zone, "top", 0.0))
    for other in counterpart_pool:
        if getattr(other, "direction", "") != expected or getattr(other, "mitigated", False):
            continue
        origin = _index(getattr(other, "index", -1))
        confirmed = _index(getattr(other, "confirmed_index", -1))
        other_kind = str(getattr(other, "kind", ""))
        if other_kind == "breaker":
            related = (
                confirmed == displacement_index
                or confirmed == break_index
                or abs(confirmed - displacement_index) <= 2
                or abs(confirmed - break_index) <= 2
            )
        else:  # fvg
            related = (
                (origin == displacement_index and confirmed == displacement_index + 1)
                or abs(confirmed - break_index) <= 2
                or abs(confirmed - displacement_index) <= 2
            )
        if not related:
            continue
        o_bottom = float(getattr(other, "bottom", 0.0))
        o_top = float(getattr(other, "top", 0.0))
        overlap_bottom = max(z_bottom, o_bottom)
        overlap_top = min(z_top, o_top)
        if overlap_bottom < overlap_top:
            return True, overlap_bottom, overlap_top
    return False, 0.0, 0.0


def _nearest_active_zone(signal: Any, frame: pd.DataFrame) -> tuple[Any | None, float]:
    """Return the closest currently-active SMC zone and distance from it."""
    close = float(frame["close"].iloc[-1])
    candidates = [
        zone for zone in [
            *getattr(signal, "order_blocks", []),
            *getattr(signal, "fvgs", []),
            *getattr(signal, "breaker_blocks", []),
        ]
        if not getattr(zone, "mitigated", False) and float(getattr(zone, "bottom", 0.0)) <= float(getattr(zone, "top", 0.0))
    ]
    if not candidates:
        return None, float("inf")

    def distance(zone: Any) -> float:
        if float(zone.bottom) <= close <= float(zone.top):
            return 0.0
        return min(abs(close - float(zone.bottom)), abs(close - float(zone.top)))

    zone = min(candidates, key=distance)
    return zone, distance(zone)


def _causal_limit_zone(signal: Any, side: str, sweep: dict[str, Any]) -> Any | None:
    """Find a still-active OB/FVG created during or after the sweep window."""
    expected = "bullish" if side == "long" else "bearish"
    breach_start = _index(sweep.get("breach_start_index"))
    reclaim_index = _index(sweep.get("candle_index"))
    candidates = []
    for zone in [*signal.order_blocks, *signal.fvgs, *getattr(signal, "breaker_blocks", [])]:
        origin = _index(getattr(zone, "index", -1))
        confirmed = _index(getattr(zone, "confirmed_index", -1))
        if (zone.direction == expected and not zone.mitigated
                and breach_start <= origin <= reclaim_index
                and confirmed >= origin):
            candidates.append(zone)
    return min(candidates, key=lambda zone: abs(float(zone.mid) - float(signal.current_price)), default=None)


def _opposing_structure_after_sweep(signal: Any, side: str, sweep_index: int) -> bool:
    expected = "bearish" if side == "long" else "bullish"
    return any(
        event.direction == expected and _index(event.break_index) > sweep_index
        for event in [*signal.swing_structures, *signal.internal_structures]
    )


def _bound_flow(signal: Any, side: str, sweep: dict[str, Any]) -> tuple[bool, str, bool]:
    """Confirm CVD against the exact SMC event and classify evidence quality."""
    expected = "bullish" if side == "long" else "bearish"
    divergence = str(getattr(signal, "cvd_divergence", "none"))
    evidence = getattr(signal, "cvd_divergence_evidence", {}) or {}
    same_reference = _index(evidence.get("reference_index")) == _index(
        sweep.get("reference_index"), -2
    )
    same_reclaim = math.isclose(
        float(evidence.get("reclaim_close_price", float("nan"))),
        float(sweep.get("reclaim_close", float("inf"))),
        rel_tol=1e-8,
        abs_tol=1e-8,
    )
    divergence_ok = divergence == expected and same_reference and same_reclaim
    strong_divergence = (divergence_ok
                         and float(evidence.get("price_excursion_atr", 0.0)) >= 0.50
                         and float(evidence.get("cvd_efficiency", 0.0)) >= 0.10)
    absorption = str(getattr(signal, "delta_absorption_type", ""))
    absorption_evidence = getattr(signal, "delta_absorption_evidence", {}) or {}
    same_absorption_candle = _index(absorption_evidence.get("candle_index")) == _index(
        sweep.get("candle_index"), -2
    )
    same_absorption_close = math.isclose(
        float(absorption_evidence.get("close_price", float("nan"))),
        float(sweep.get("reclaim_close", float("inf"))),
        rel_tol=1e-8,
        abs_tol=1e-8,
    )
    absorption_ok = (bool(getattr(signal, "delta_absorption", False))
                     and absorption == f"{expected}_absorption"
                     and same_absorption_candle and same_absorption_close)
    return divergence_ok or absorption_ok, divergence if divergence_ok else absorption if absorption_ok else "none", strong_divergence


def _candidate(*, side: str, setup_type: str, entry: float, stop: float,
               target: float | None, grade: Literal["WAIT", "S", "A", "B", "C"],
               evidence: list[str], invalidation_source: str,
               trigger_age_bars: int | None = None,
               extension_atr: float | None = None,
               flow_confirmation_type: Literal[
                   "sweep_bound_cvd_divergence", "sweep_bound_absorption",
                   "single_bar_aggressor_delta",
               ] = "sweep_bound_cvd_divergence",
               event_id: str = "", entry_policy: Literal[
                   "market_eligible", "limit_retest_only"
               ] = "market_eligible", limit_zone_id: str = "",
               policy: TriCorePolicy | None = None) -> TriCoreSetup:
    policy = policy or TriCorePolicy()
    risk = abs(entry - stop)
    # Give sweep_reversal setups sufficient breathing room (>= 0.65% from entry)
    # to avoid premature noise/spread stop-outs on 15M bars, while maintaining 0.35% for retests.
    min_risk = entry * (0.0065 if setup_type == "sweep_reversal" else 0.0035)
    if risk < min_risk:
        stop = (entry - min_risk) if side == "long" else (entry + min_risk)
        risk = abs(entry - stop)

    eff_min_rr = max(1.2, float(policy.minimum_rr or 1.2))
    structural_obstacle = target
    if structural_obstacle is not None:
        valid_obstacle = (
            entry < float(structural_obstacle)
            if side == "long"
            else float(structural_obstacle) < entry
        )
        if valid_obstacle:
            obstacle_rr = abs(float(structural_obstacle) - entry) / max(risk, 1e-6)
            if obstacle_rr < eff_min_rr:
                return TriCoreSetup(
                    rejection_reasons=[
                        f"Opposing structural obstacle at {float(structural_obstacle):.4f} blocks room to target ({obstacle_rr:.2f}R < {eff_min_rr:.2f}R — Anti-Chase)"
                    ]
                )

    if target is None or not (
        stop < entry < float(target) if side == "long" else float(target) < entry < stop
    ):
        if setup_type == "range_boundary_ping_pong":
            return TriCoreSetup(
                rejection_reasons=["Range boundary setup requires a valid opposing liquidity target"]
            )
        target = entry + (risk * 2.0) if side == "long" else entry - (risk * 2.0)

    rr = abs(float(target) - entry) / max(risk, 1e-6)
    is_actionable = rr >= eff_min_rr
    final_grade: Literal["WAIT", "S", "A", "B", "C"] = (
        (grade if grade != "WAIT" else "A") if is_actionable else "WAIT"
    )
    return TriCoreSetup(
        actionable=is_actionable, direction=side, setup_type=setup_type,
        entry=entry, stop_loss=stop, take_profit=float(target),
        risk_reward=round(rr, 2), grade=final_grade,
        smc_confirmed=True,
        cvd_confirmed=flow_confirmation_type != "single_bar_aggressor_delta",
        flow_confirmed=True,
        flow_confirmation_type=flow_confirmation_type,
        invalidation_source=invalidation_source, evidence=evidence,
        trigger_state=(
            "entry_ready" if is_actionable and entry_policy == "market_eligible"
            else "limit_retest_only" if is_actionable else "flow_confirmed"
        ),
        event_id=event_id,
        state_reason=(
            "Causal SMC and trusted flow confirmed — Direct Market Execution"
            if is_actionable and entry_policy == "market_eligible"
            else "Causal SMC and flow confirmed — Anti-Chase FVG/OB Limit Retest (2-Bar TTL)"
            if is_actionable
            else f"Flow confirmed but room to target provides R:R {rr:.2f} < {eff_min_rr:.2f}"
        ),
        entry_policy=entry_policy,
        order_type="market" if entry_policy == "market_eligible" else "limit",
        limit_zone_id=limit_zone_id,
        trigger_age_bars=trigger_age_bars,
        extension_atr=(round(extension_atr, 3)
                       if extension_atr is not None else None),
        rejection_reasons=[] if is_actionable else [
            f"Insufficient room to target: R:R {rr:.2f} < {eff_min_rr:.2f} (Anti-Chase)"
        ],
        expires_after_bars=policy.ttl_bars,
        policy_version=policy.version,
        calibration_status=policy.calibration_status,
    )


def _extended_sweep_is_valid(
    signal: Any,
    frame: pd.DataFrame,
    side: str,
    sweep: dict[str, Any],
    *,
    atr: float,
    policy: TriCorePolicy,
) -> tuple[bool, float, str]:
    """Validate the hybrid post-reclaim window without hindsight."""
    sweep_index = _index(sweep.get("candle_index"))
    current_index = len(frame) - 1
    age = current_index - sweep_index
    if age < 0:
        return False, 0.0, "Sweep trigger is from a future candle"
    ttl_bars = min(policy.ttl_bars, max(0, _index(
        getattr(signal, "execution_ttl_bars", policy.ttl_bars),
        policy.ttl_bars,
    )))
    if age > ttl_bars:
        return False, 0.0, (
            f"Sweep trigger expired: age {age} bars > {ttl_bars}"
        )
    if age == 0 and atr > 0:
        reclaim = float(sweep.get("reclaim_close", 0.0) or 0.0)
        entry = float(frame.iloc[-1]["close"])
        if reclaim > 0:
            directional_extension = (
                max(0.0, entry - reclaim) if side == "long"
                else max(0.0, reclaim - entry)
            )
            ext_atr = directional_extension / atr
            if ext_atr > policy.no_chase_max_extension_atr:
                return False, ext_atr, (
                    f"Price extended {ext_atr:.2f} ATR from reclaim > "
                    f"{policy.no_chase_max_extension_atr:.2f}; do not chase"
                )
            return True, ext_atr, ""
        return True, 0.0, ""

    extreme = float(sweep.get("extreme", 0.0) or 0.0)
    current = frame.iloc[-1]
    if extreme <= 0:
        return False, 0.0, "Sweep extreme is unavailable"
    post_sweep = frame.iloc[sweep_index + 1:]
    if side == "long" and not post_sweep.empty and float(post_sweep["low"].min()) < extreme:
        return False, 0.0, "Sweep invalidated: a later candle traded below the sweep extreme"
    if side == "short" and not post_sweep.empty and float(post_sweep["high"].max()) > extreme:
        return False, 0.0, "Sweep invalidated: a later candle traded above the sweep extreme"
    if _opposing_structure_after_sweep(signal, side, sweep_index):
        return False, 0.0, "Sweep invalidated by a confirmed opposing structure break"

    evidence = getattr(signal, "cvd_divergence_evidence", {}) or {}
    reference_cvd = evidence.get("reference_cvd")
    current_cvd = getattr(signal, "cvd", None)
    if reference_cvd is None or current_cvd is None:
        return False, 0.0, "CVD continuation is unavailable for the armed sweep"
    cvd_holds = (
        float(current_cvd) > float(reference_cvd)
        if side == "long"
        else float(current_cvd) < float(reference_cvd)
    )
    if not cvd_holds:
        return False, 0.0, "CVD reversed before the armed sweep could execute"

    reclaim = float(sweep.get("reclaim_close", 0.0) or 0.0)
    entry = float(current["close"])
    if reclaim <= 0 or atr <= 0:
        return True, 0.0, ""
    directional_extension = (
        max(0.0, entry - reclaim) if side == "long"
        else max(0.0, reclaim - entry)
    )
    extension_atr = directional_extension / atr
    if extension_atr > policy.no_chase_max_extension_atr:
        return False, extension_atr, (
            f"Price extended {extension_atr:.2f} ATR from reclaim > "
            f"{policy.no_chase_max_extension_atr:.2f}; do not chase"
        )
    return True, extension_atr, ""


class TriCoreSetupEngine:
    """Resolve deterministic patterns without scenario/regime/LLM vetoes."""

    @staticmethod
    def evaluate(
        signal: Any,
        frame: pd.DataFrame,
        policy_config: dict[str, Any] | None = None,
    ) -> TriCoreSetup:
        policy = TriCorePolicy.from_mapping(policy_config)
        if frame is None or frame.empty:
            return TriCoreSetup(rejection_reasons=["Closed-candle data unavailable"])
        market_status = getattr(frame, "attrs", {}).get("market_data_status", {})
        if isinstance(market_status, dict) and market_status.get("market") == "US_EQUITY":
            if not market_status.get("regular_session_open", True):
                return TriCoreSetup(rejection_reasons=["US regular trading session is closed"])

        if getattr(signal, "volume_quality", "unavailable") != "exchange_aggressor":
            return TriCoreSetup(rejection_reasons=[
                "Trusted exchange-derived aggressor CVD is unavailable"
            ])

        candidates: list[TriCoreSetup] = []
        rejected: list[str] = []
        entry = float(frame["close"].iloc[-1])
        atr = _atr(frame)
        buffer = max(entry * 0.0005, atr * 0.10)
        sweep = getattr(signal, "liquidity_sweep", {}) or {}
        saw_sweep = False
        saw_bound_flow = False
        zone = None

        regime_data = getattr(signal, "market_regime", {}) or {}
        regime_label = str(regime_data.get("regime", "")).lower()
        is_ranging = (regime_label == "ranging")
        micro = getattr(signal, "micro_momentum", {}) or {}
        micro_dir = str(micro.get("direction", "neutral")).lower()

        for side in ("long", "short"):
            expected_sweep = "low" if side == "long" else "high"
            in_prem = getattr(signal, "in_premium", False)
            in_disc = getattr(signal, "in_discount", False)
            if side == "long" and in_prem:
                rejected.append("Adverse location: Cannot LONG in Premium")
                continue
            if side == "short" and in_disc:
                rejected.append("Adverse location: Cannot SHORT in Discount")
                continue

            # -----------------------------------------------------------------
            # SETUP A: Sniper Reversal (Liquidity Sweep + CVD Absorption/Div)
            # -----------------------------------------------------------------
            if (getattr(signal, "liquidity_swept", False)
                    and getattr(signal, "sweep_direction", "none") == expected_sweep):
                saw_sweep = True
                sweep_age = len(frame) - 1 - _index(sweep.get("candle_index"))
                window_ok, extension_atr, window_reason = _extended_sweep_is_valid(
                    signal, frame, side, sweep, atr=atr, policy=policy
                )
                if not window_ok:
                    rejected.append(window_reason)
                else:
                    flow_ok, flow_label, strong = _bound_flow(signal, side, sweep)
                    if flow_ok:
                        saw_bound_flow = True
                        extreme = float(sweep.get("extreme", 0.0) or 0.0)
                        # Rejection Confirmation: If candle closed beyond the sweep extreme, it's a breakout/continuation, not a reversal.
                        if side == "short" and entry >= extreme:
                            rejected.append(f"Sweep invalidated: price {entry:.4f} closed above sweep extreme {extreme:.4f} (breakout, not reversal)")
                            continue
                        if side == "long" and entry <= extreme:
                            rejected.append(f"Sweep invalidated: price {entry:.4f} closed below sweep extreme {extreme:.4f} (breakdown, not reversal)")
                            continue
                        stop = extreme - buffer if side == "long" else extreme + buffer
                        event_id = str(sweep.get("event_id") or (
                            f"sweep:{side}:{sweep.get('level_id', '')}:"
                            f"{sweep.get('candle_time', sweep.get('candle_index', ''))}"
                        ))
                        entry_policy: Literal["market_eligible", "limit_retest_only"] = "market_eligible"
                        causal_zone = _causal_limit_zone(signal, side, sweep)
                        limit_zone_id = str(causal_zone.zone_id) if causal_zone is not None else ""
                        candidate_entry = entry

                        # Anti-Chase: If candle spiked > 0.50 ATR from reclaim, do not buy top;
                        # place 2-bar Limit at the front edge of the causal FVG/OB instead.
                        if extension_atr > policy.market_entry_max_extension_atr:
                            if causal_zone is None:
                                rejected.append(
                                    f"Price extended {extension_atr:.2f} ATR (> {policy.market_entry_max_extension_atr:.2f} ATR) without FVG/OB retest zone — do not chase"
                                )
                                continue
                            entry_policy = "limit_retest_only"
                            candidate_entry = float(
                                causal_zone.top if side == "long" else causal_zone.bottom
                            )

                        loc_ok, loc_reason = _range_location_ok(
                            signal, side, candidate_entry, allow_equilibrium_sweep=True
                        )
                        if not loc_ok:
                            rejected.append(loc_reason)
                            continue
                        target_val = (
                            _range_opposing_target(signal, side, candidate_entry)
                            if is_ranging
                            else _nearest_target(signal, side, candidate_entry)
                        ) if extreme > 0 else None
                        is_session_sweep = _is_session_judas_sweep(frame, sweep)
                        sweep_evidence = [
                            f"Confirmed {sweep.get('event_type', 'sweep')} of the same {expected_sweep} swing and close reclaim",
                            f"Sweep trigger age {sweep_age}/{policy.ttl_bars} bars; extension {extension_atr:.2f} ATR ({entry_policy})",
                            f"CVD {flow_label} confirms {side}",
                        ]
                        if is_session_sweep:
                            sweep_evidence.append(
                                "Session Sweep (Judas/AMD): Raided Asian Range liquidity during London/NY Killzone -> Grade S"
                            )
                        result = _candidate(
                            side=side, setup_type="sweep_reversal", entry=candidate_entry, stop=stop,
                            target=target_val,
                            grade="S" if (strong or is_session_sweep) else "A",
                            invalidation_source="full_sweep_window_extreme",
                            trigger_age_bars=sweep_age,
                            extension_atr=extension_atr,
                            flow_confirmation_type=(
                                "sweep_bound_cvd_divergence"
                                if flow_label in {"bullish", "bearish"}
                                else "sweep_bound_absorption"
                            ),
                            event_id=event_id,
                            entry_policy=entry_policy,
                            limit_zone_id=limit_zone_id,
                            policy=policy,
                            evidence=sweep_evidence,
                        )
                        candidates.append(result)
                        rejected.extend(result.rejection_reasons)
                    else:
                        rejected.append("CVD evidence is not bound to the same swept swing/reclaim")

            # -----------------------------------------------------------------
            # SETUP B: BOS/CHoCH + Order Block / FVG / Breaker Retest
            # -----------------------------------------------------------------
            displacement = getattr(signal, "displacement", {}) or {}
            pressure = float(getattr(signal, "delta_ratio", 0.0))
            cvd_div = str(getattr(signal, "cvd_divergence", "none"))
            opposing_cvd = "bullish" if side == "short" else "bearish"
            opposing_divergence = (cvd_div == opposing_cvd)
            strongly_opposing_delta = (pressure > 0.20 if side == "short" else pressure < -0.20)
            flow_aligned = (pressure >= 0.10 if side == "long" else pressure <= -0.10)
            non_opposing_flow = (not opposing_divergence) and (not strongly_opposing_delta)
            falling_knife_against = (
                (side == "long" and micro_dir == "bearish" and pressure < -0.15)
                or (side == "short" and micro_dir == "bullish" and pressure > 0.15)
            )

            if displacement.get("direction") == side and (flow_aligned or non_opposing_flow) and not falling_knife_against:
                zone, is_shallow = _first_lineage_retest(signal, frame, side, displacement, atr=atr)
                if zone is not None:
                    stop = float(zone.bottom) - buffer if side == "long" else float(zone.top) + buffer
                    zone_source = str(getattr(zone, "source", "swing"))
                    zone_label = (
                        "Inversion FVG (iFVG Role-Flip)"
                        if (zone.kind == "breaker" and zone_source == "ifvg")
                        else "S/R Flip Breaker Block" if zone.kind == "breaker"
                        else "Fair Value Gap" if zone.kind == "fvg"
                        else "Order Block"
                    )
                    if is_shallow:
                        setup_type = (
                            "breaker_shallow_retest" if zone.kind == "breaker"
                            else "fvg_shallow_retest" if zone.kind == "fvg"
                            else "displacement_retest"
                        )
                    else:
                        setup_type = (
                            "breaker_retest" if zone.kind == "breaker"
                            else "fvg_retest" if zone.kind == "fvg"
                            else "displacement_retest"
                        )
                    evidence_items = [
                        f"First retest of lineage-bound {zone.kind.upper()} ({zone_label}) after structure-breaking {side} displacement"
                    ]
                    if is_shallow:
                        if zone.kind == "breaker":
                            evidence_items.append(
                                f"Front-run shallow retest within 0.15 ATR buffer of {side} Breaker Block"
                            )
                        elif zone.kind == "fvg":
                            evidence_items.append(
                                f"Shallow retest respecting 50% Consequent Encroachment (CE) of {side} Fair Value Gap"
                            )
                    is_unicorn, uni_bottom, uni_top = _detect_unicorn_overlap(
                        signal, side, zone, displacement
                    )
                    disp_grade: Literal["S", "A"] = "S" if is_unicorn else "A"
                    if is_unicorn:
                        evidence_items.append(
                            f"Unicorn Confluence (Grade S): Breaker Block overlaps with Fair Value Gap ({uni_bottom:.4f}-{uni_top:.4f})"
                        )
                    if flow_aligned:
                        evidence_items.append(f"Aggressor delta ratio {pressure:+.1%} confirms {side}")
                    else:
                        evidence_items.append(f"Displacement continuation confirmed without opposing CVD (delta {pressure:+.1%})")

                    zone_edge = float(zone.top if side == "long" else zone.bottom)
                    dist_from_edge = (entry - zone_edge) if side == "long" else (zone_edge - entry)
                    if atr > 0 and dist_from_edge > policy.market_entry_max_extension_atr * atr:
                        disp_entry = zone_edge
                        disp_entry_policy: Literal["market_eligible", "limit_retest_only"] = "limit_retest_only"
                    else:
                        disp_entry = entry
                        disp_entry_policy = "market_eligible"

                    loc_ok, loc_reason = _range_location_ok(
                        signal, side, disp_entry, allow_equilibrium_sweep=False
                    )
                    if not loc_ok:
                        rejected.append(loc_reason)
                        continue

                    disp_target = (
                        _range_opposing_target(signal, side, disp_entry)
                        if is_ranging
                        else _nearest_target(signal, side, disp_entry)
                    )
                    result = _candidate(
                        side=side, setup_type=setup_type, entry=disp_entry, stop=stop,
                        target=disp_target, grade=disp_grade,
                        invalidation_source=f"first_retest:{zone.zone_id}",
                        flow_confirmation_type="single_bar_aggressor_delta",
                        event_id=(
                            f"displacement:{side}:{displacement.get('structure_break_id')}:"
                            f"{getattr(zone, 'zone_id', '')}"
                        ),
                        entry_policy=disp_entry_policy,
                        limit_zone_id=str(getattr(zone, "zone_id", "")),
                        policy=policy,
                        evidence=evidence_items,
                    )
                    candidates.append(result)
                    rejected.extend(result.rejection_reasons)

            # -----------------------------------------------------------------
            # SETUP C: Ranging Boundary OB/FVG (Protected against Falling Knives)
            # -----------------------------------------------------------------
            if policy.ping_pong_enabled and is_ranging and non_opposing_flow and not falling_knife_against:
                if side == "long" and micro_dir != "bearish":
                    bullish_obs = [
                        z for z in getattr(signal, "order_blocks", [])
                        if z.direction == "bullish" and not z.mitigated
                        and float(z.bottom) - buffer <= entry <= float(z.top) + atr * 0.15
                    ]
                    bullish_fvgs = [
                        z for z in getattr(signal, "fvgs", [])
                        if z.direction == "bullish" and not z.mitigated
                        and float(z.bottom) - buffer <= entry <= float(z.top) + atr * 0.15
                    ]
                    loc_ok, loc_reason = _range_location_ok(
                        signal, "long", entry, allow_equilibrium_sweep=False
                    )
                    in_prem = getattr(signal, "in_premium", False)
                    in_disc = getattr(signal, "in_discount", False)
                    in_eq = getattr(signal, "in_equilibrium", False)
                    # Require concrete unmitigated OB or FVG in Discount to avoid raw inducement traps
                    at_demand = loc_ok and in_disc and (bool(bullish_obs) or bool(bullish_fvgs)) and not in_prem and not in_eq
                    if at_demand:
                        last_bar = frame.iloc[-1]
                        bar_range = max(float(last_bar['high']) - float(last_bar['low']), 1e-6)
                        lower_wick = float(min(last_bar['open'], last_bar['close'])) - float(last_bar['low'])
                        close_pos = (float(last_bar['close']) - float(last_bar['low'])) / bar_range
                        has_rejection = ((lower_wick / bar_range) >= 0.25) or (
                            float(last_bar['close']) > float(last_bar['open']) and close_pos >= 0.55
                        )
                        if not has_rejection or pressure < 0.05:
                            at_demand = False

                    if at_demand:
                        pp_entry = entry
                        pp_policy: Literal["market_eligible", "limit_retest_only"] = "market_eligible"
                        candidate_zones = [(z, "ob") for z in bullish_obs] + [(z, "fvg") for z in bullish_fvgs]
                        candidate_zones.sort(key=lambda item: abs(entry - float(item[0].mid)))
                        chosen, chosen_kind = candidate_zones[0]
                        stop = float(chosen.bottom) - buffer
                        zone_id = str(chosen.zone_id)
                        invalidation_source = f"demand_boundary:{chosen_kind}:{zone_id}"

                        target = _range_opposing_target(signal, "long", pp_entry)
                        candle_ts = str(getattr(signal, "timestamp", "") or (frame.index[-1] if len(frame) else ""))
                        sym = str(getattr(signal, "symbol", "UNKNOWN"))
                        tf = str(getattr(signal, "timeframe", "15m"))
                        if target is not None and stop < pp_entry < target:
                            candidates.append(_candidate(
                                side="long",
                                setup_type="range_boundary_ping_pong",
                                entry=pp_entry,
                                stop=stop,
                                target=target,
                                grade="B",
                                invalidation_source=invalidation_source,
                                flow_confirmation_type="single_bar_aggressor_delta",
                                event_id=f"ping_pong:long:{sym}:{tf}:{zone_id}:{candle_ts}",
                                entry_policy=pp_policy,
                                limit_zone_id=zone_id,
                                policy=policy,
                                evidence=[
                                    f"Price rejected concrete SMC Demand Zone ({zone_id}) in Ranging Discount",
                                    f"Targeting opposing Supply boundary at {target:.2f}",
                                    f"Positive aggressor delta ({pressure:+.1%}) confirms buyer defense",
                                ],
                            ))

                elif side == "short" and micro_dir != "bullish":
                    bearish_obs = [
                        z for z in getattr(signal, "order_blocks", [])
                        if z.direction == "bearish" and not z.mitigated
                        and float(z.bottom) - atr * 0.15 <= entry <= float(z.top) + buffer
                    ]
                    bearish_fvgs = [
                        z for z in getattr(signal, "fvgs", [])
                        if z.direction == "bearish" and not z.mitigated
                        and float(z.bottom) - atr * 0.15 <= entry <= float(z.top) + buffer
                    ]
                    loc_ok, loc_reason = _range_location_ok(
                        signal, "short", entry, allow_equilibrium_sweep=False
                    )
                    in_prem = getattr(signal, "in_premium", False)
                    in_disc = getattr(signal, "in_discount", False)
                    in_eq = getattr(signal, "in_equilibrium", False)
                    at_supply = loc_ok and in_prem and (bool(bearish_obs) or bool(bearish_fvgs)) and not in_disc and not in_eq
                    if at_supply:
                        last_bar = frame.iloc[-1]
                        bar_range = max(float(last_bar['high']) - float(last_bar['low']), 1e-6)
                        upper_wick = float(last_bar['high']) - float(max(last_bar['open'], last_bar['close']))
                        close_pos = (float(last_bar['high']) - float(last_bar['close'])) / bar_range
                        has_rejection = ((upper_wick / bar_range) >= 0.25) or (
                            float(last_bar['close']) < float(last_bar['open']) and close_pos >= 0.55
                        )
                        if not has_rejection or pressure > -0.05:
                            at_supply = False

                    if at_supply:
                        pp_entry = entry
                        pp_policy = "market_eligible"
                        candidate_zones = [(z, "ob") for z in bearish_obs] + [(z, "fvg") for z in bearish_fvgs]
                        candidate_zones.sort(key=lambda item: abs(entry - float(item[0].mid)))
                        chosen, chosen_kind = candidate_zones[0]
                        stop = float(chosen.top) + buffer
                        zone_id = str(chosen.zone_id)
                        invalidation_source = f"supply_boundary:{chosen_kind}:{zone_id}"

                        target = _range_opposing_target(signal, "short", pp_entry)
                        candle_ts = str(getattr(signal, "timestamp", "") or (frame.index[-1] if len(frame) else ""))
                        sym = str(getattr(signal, "symbol", "UNKNOWN"))
                        tf = str(getattr(signal, "timeframe", "15m"))
                        if target is not None and target < pp_entry < stop:
                            candidates.append(_candidate(
                                side="short",
                                setup_type="range_boundary_ping_pong",
                                entry=pp_entry,
                                stop=stop,
                                target=target,
                                grade="B",
                                invalidation_source=invalidation_source,
                                flow_confirmation_type="single_bar_aggressor_delta",
                                event_id=f"ping_pong:short:{sym}:{tf}:{zone_id}:{candle_ts}",
                                entry_policy=pp_policy,
                                limit_zone_id=zone_id,
                                policy=policy,
                                evidence=[
                                    f"Price rejected concrete SMC Supply Zone ({zone_id}) in Ranging Premium",
                                    f"Targeting opposing Demand boundary at {target:.2f}",
                                    f"Negative aggressor delta ({pressure:+.1%}) confirms seller defense",
                                ],
                            ))

        contract_mode = _resolve_contract_mode(signal, policy)
        spot_exit_detected = False
        if contract_mode == "spot":
            for cand in candidates:
                if cand.direction == "short":
                    if cand.actionable:
                        spot_exit_detected = True
                    cand.actionable = False
                    cand.grade = "WAIT"
                    cand.spot_exit_signal = True
                    spot_reason = (
                        "Spot market (BUY/SELL only): Short entry disabled — "
                        "use SELL signal to exit existing Spot holdings"
                    )
                    if spot_reason not in cand.rejection_reasons:
                        cand.rejection_reasons.append(spot_reason)
                    if spot_reason not in rejected:
                        rejected.append(spot_reason)

        actionable = [candidate for candidate in candidates if candidate.actionable]
        if not actionable:
            reasons = list(dict.fromkeys(rejected)) or [
                "No causal sweep+CVD reversal or first displacement-retest+CVD continuation"
            ]
            if candidates:
                candidates.sort(key=lambda candidate: candidate.risk_reward, reverse=True)
                armed = candidates[0]
                armed.rejection_reasons = reasons
                return _stamp_contract_metadata(armed, contract_mode, spot_exit_detected)
            state: Literal[
                "wait", "zone_approach", "armed", "sweep_detected",
                "flow_confirmed", "no_chase", "expired", "invalidated",
            ] = "wait"
            state_reason = reasons[0] if reasons else "No causal setup"
            if any("expired" in reason.lower() for reason in reasons):
                state = "expired"
            elif any("no causal ob/fvg retest" in reason.lower() or "do not chase" in reason.lower()
                     for reason in reasons):
                state = "no_chase"
            elif any(
                marker in reason.lower()
                for reason in reasons
                for marker in ("invalidated", "reversed")
            ):
                state = "invalidated"
            elif saw_bound_flow:
                state = "flow_confirmed"
            elif saw_sweep:
                state = "sweep_detected"
            else:
                zone, distance = _nearest_active_zone(signal, frame)
                if zone is not None and distance == 0:
                    state = "armed"
                    state_reason = f"Price is inside active {zone.kind.upper()} {zone.zone_id}"
                elif zone is not None and atr > 0 and distance / atr <= policy.zone_approach_distance_atr:
                    state = "zone_approach"
                    state_reason = f"Price is {distance / atr:.2f} ATR from {zone.kind.upper()} {zone.zone_id}"
            event_id = str(sweep.get("event_id") or "")
            if not event_id and zone is not None:
                event_id = f"zone:{signal.symbol}:{signal.timeframe}:{zone.zone_id}"
            return _stamp_contract_metadata(
                TriCoreSetup(
                    trigger_state=state,
                    event_id=event_id,
                    state_reason=state_reason,
                    entry_policy="no_chase" if state == "no_chase" else "none",
                    rejection_reasons=reasons,
                    expires_after_bars=policy.ttl_bars,
                    policy_version=policy.version,
                    calibration_status=policy.calibration_status,
                ),
                contract_mode,
                spot_exit_detected,
            )
        return _stamp_contract_metadata(
            _select_actionable_candidate(actionable, policy),
            contract_mode,
            spot_exit_detected,
        )

    @classmethod
    def evaluate_with_micro_trigger(
        cls,
        signal: Any,
        frame: pd.DataFrame,
        frame_micro: pd.DataFrame | None = None,
        micro_timeframe: str = "3m",
        policy_config: Any = None,
    ) -> TriCoreSetup:
        """Evaluate macro 15M setup with optional 3M/5M sniper trigger refinement.

        CANONICAL INTEGRITY RULE:
        15M is the SOLE execution authority. Micro timeframe (3M/5M) is strictly
        an execution refinement helper and CANNOT upgrade a non-actionable 15M setup
        into an actionable one.
        """
        setup = cls.evaluate(signal, frame, policy_config=policy_config)
        if not setup.actionable:
            return setup

        if frame_micro is None or frame_micro.empty:
            return setup

        from app.engines.micro_trigger_engine import MicroTriggerEngine

        zone, _ = _nearest_active_zone(signal, frame)
        if zone is None and getattr(setup, "limit_zone_id", None):
            zone_pool = [*signal.order_blocks, *signal.fvgs, *getattr(signal, "breaker_blocks", [])]
            for z in zone_pool:
                if str(getattr(z, "zone_id", "")) == str(setup.limit_zone_id):
                    zone = z
                    break

        if zone is None:
            return setup

        side = setup.direction if setup.direction in {"long", "short"} else getattr(zone, "direction", "wait")
        if side not in {"long", "short"}:
            return setup

        current_micro_price = float(frame_micro["close"].iloc[-1])
        target = setup.take_profit or _nearest_target(signal, side, current_micro_price)
        if target is None:
            return setup

        macro_atr = _atr(frame)
        micro_setup = MicroTriggerEngine.evaluate(
            side=side,
            macro_zone=zone,
            macro_target=target,
            frame_micro=frame_micro,
            micro_timeframe=micro_timeframe,
            min_rr=2.0,
            macro_atr=macro_atr,
        )

        if micro_setup.actionable:
            setup.actionable = True
            setup.direction = micro_setup.direction
            setup.entry = micro_setup.entry
            setup.stop_loss = micro_setup.stop_loss
            setup.take_profit = micro_setup.take_profit
            setup.risk_reward = micro_setup.risk_reward
            setup.order_type = "market"
            setup.entry_policy = "market_eligible"
            setup.trigger_state = "entry_ready"
            setup.setup_type = f"micro_sniper_{micro_timeframe}"
            setup.evidence = [
                *setup.evidence,
                *micro_setup.evidence,
            ]
            setup.rejection_reasons = []

        return setup


def _select_actionable_candidate(
    candidates: list[TriCoreSetup],
    policy: TriCorePolicy | None = None,
) -> TriCoreSetup:
    """Select the best actionable candidate according to the policy's candidate_selection_mode."""
    if not candidates:
        return TriCoreSetup()
    pol = policy or TriCorePolicy()
    if pol.candidate_selection_mode == "grade_then_rr":
        return sorted(
            candidates,
            key=lambda c: (c.grade == "S", c.risk_reward),
            reverse=True,
        )[0]
    # Default: "rr_then_grade"
    return sorted(
        candidates,
        key=lambda c: (c.risk_reward, c.grade == "S"),
        reverse=True,
    )[0]

