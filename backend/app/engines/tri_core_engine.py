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
    calibration_status: Literal["draft_unvalidated", "walk_forward_validated"] = "draft_unvalidated"
    ttl_bars: int = MAX_SWEEP_TRIGGER_AGE_BARS
    market_entry_max_extension_atr: float = MARKET_ENTRY_MAX_EXTENSION_ATR
    no_chase_max_extension_atr: float = MAX_RECLAIM_EXTENSION_ATR
    zone_approach_distance_atr: float = ZONE_APPROACH_DISTANCE_ATR
    minimum_rr: float = 2.0
    ping_pong_enabled: bool = False
    target_anchor_mode: str = "opposing_boundary"
    counter_trend_filter_enabled: bool = True

    @classmethod
    def from_mapping(cls, raw: Any) -> "TriCorePolicy":
        values = raw if isinstance(raw, dict) else {}
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
    grade: Literal["WAIT", "A", "S"] = "WAIT"
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    the trade at an internal minor obstacle.
    """
    if side == "long":
        # 1. Look for Major Bearish Order Block bottom above entry
        bearish_obs = [
            float(zone.bottom) for zone in getattr(signal, "order_blocks", [])
            if zone.direction == "bearish" and not zone.mitigated and float(zone.bottom) > entry
            and getattr(zone, "source", "swing") == "swing"
        ]
        if bearish_obs:
            return min(bearish_obs)

        # 2. Look for Bearish FVG bottom above entry
        bearish_fvgs = [
            float(zone.bottom) for zone in getattr(signal, "fvgs", [])
            if zone.direction == "bearish" and not zone.mitigated and float(zone.bottom) > entry
        ]
        if bearish_fvgs:
            return min(bearish_fvgs)

        # 3. Look for Range Zones premium zone bottom
        rz = getattr(signal, "range_zones", {}) or {}
        p_zone = rz.get("premium_zone", {})
        if isinstance(p_zone, dict) and p_zone.get("bottom"):
            pz_bottom = float(p_zone["bottom"])
            if pz_bottom > entry:
                return pz_bottom

        return _nearest_target(signal, side, entry)

    else:
        # 1. Look for Major Bullish Order Block top below entry
        bullish_obs = [
            float(zone.top) for zone in getattr(signal, "order_blocks", [])
            if zone.direction == "bullish" and not zone.mitigated and 0 < float(zone.top) < entry
            and getattr(zone, "source", "swing") == "swing"
        ]
        if bullish_obs:
            return max(bullish_obs)

        # 2. Look for Bullish FVG top below entry
        bullish_fvgs = [
            float(zone.top) for zone in getattr(signal, "fvgs", [])
            if zone.direction == "bullish" and not zone.mitigated and 0 < float(zone.top) < entry
        ]
        if bullish_fvgs:
            return max(bullish_fvgs)

        # 3. Look for Range Zones discount zone top
        rz = getattr(signal, "range_zones", {}) or {}
        d_zone = rz.get("discount_zone", {})
        if isinstance(d_zone, dict) and d_zone.get("top"):
            dz_top = float(d_zone["top"])
            if 0 < dz_top < entry:
                return dz_top

        return _nearest_target(signal, side, entry)


def _zone_overlaps(row: pd.Series, zone: Any) -> bool:
    return float(row["low"]) <= float(zone.top) and float(row["high"]) >= float(zone.bottom)


def _candle_tests_zone(row: pd.Series, zone: Any, side: str, atr: float = 0.0) -> tuple[bool, bool]:
    """Check if a candle retests a zone, returning (is_test, is_shallow).

    Supports:
    1. Standard penetration retest for OB, Breaker, and FVG.
    2. Consequent Encroachment (50% CE) shallow retest for FVG.
    3. Front-run buffer (within 0.15 ATR) shallow retest for Breaker Blocks.
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
                # If low remained above mid, price respected the upper 50% CE shallowly
                is_shallow = low > mid and close >= mid
                return True, is_shallow
        elif side == "short":
            if overlaps and close <= top:
                # If high remained below mid, price respected the lower 50% CE shallowly
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


def _nearest_active_zone(signal: Any, frame: pd.DataFrame) -> tuple[Any | None, float]:
    """Return the closest currently-active SMC zone and distance from it."""
    close = float(frame["close"].iloc[-1])
    candidates = [
        zone for zone in [*signal.order_blocks, *signal.fvgs, *getattr(signal, "breaker_blocks", [])]
        if not zone.mitigated and float(zone.bottom) <= float(zone.top)
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
               target: float | None, grade: Literal["A", "S"],
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
    valid_geometry = target is not None and (
        stop < entry < target if side == "long" else target < entry < stop
    )
    if not valid_geometry:
        return TriCoreSetup(rejection_reasons=["No valid nearest opposing liquidity target"])
    rr = abs(float(target) - entry) / abs(entry - stop)
    return TriCoreSetup(
        actionable=rr >= policy.minimum_rr, direction=side, setup_type=setup_type,
        entry=entry, stop_loss=stop, take_profit=float(target),
        risk_reward=round(rr, 2), grade=grade if rr >= policy.minimum_rr else "WAIT",
        smc_confirmed=True,
        cvd_confirmed=flow_confirmation_type != "single_bar_aggressor_delta",
        flow_confirmed=True,
        flow_confirmation_type=flow_confirmation_type,
        invalidation_source=invalidation_source, evidence=evidence,
        trigger_state=(
            "entry_ready" if rr >= policy.minimum_rr and entry_policy == "market_eligible"
            else "limit_retest_only" if rr >= policy.minimum_rr else "flow_confirmed"
        ),
        event_id=event_id,
        state_reason=("Causal SMC and trusted flow confirmed"
                      if rr >= policy.minimum_rr else "Flow confirmed but structural R:R is insufficient"),
        entry_policy=entry_policy,
        order_type="market" if entry_policy == "market_eligible" else "limit",
        limit_zone_id=limit_zone_id,
        trigger_age_bars=trigger_age_bars,
        extension_atr=(round(extension_atr, 3)
                       if extension_atr is not None else None),
        rejection_reasons=[] if rr >= policy.minimum_rr else [
            f"Nearest opposing obstacle provides R:R {rr:.2f} < {policy.minimum_rr:.2f}"
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
    if age == 0:
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
    # Both values must come from the same compute_volume_delta pass. Absolute
    # cumulative CVD values from a provider frame may use a different origin.
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
        return False, 0.0, "Reclaim/ATR data is unavailable"
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
        trend_direction = str(regime_data.get("direction", "")).lower()
        htf_bias = str(getattr(signal, "htf_bias", "neutral") or "neutral").lower()
        is_ranging = (regime_label == "ranging") or (policy.ping_pong_enabled and regime_label != "trending")

        for side in ("long", "short"):
            if policy.counter_trend_filter_enabled:
                if regime_label == "trending":
                    if trend_direction == "bullish" and side == "short":
                        rejected.append("Counter-trend SHORT rejected: Market regime is trending bullish")
                        continue
                    if trend_direction == "bearish" and side == "long":
                        rejected.append("Counter-trend LONG rejected: Market regime is trending bearish")
                        continue
                if regime_label != "ranging":
                    if htf_bias == "bullish" and side == "short":
                        rejected.append("Counter-trend SHORT rejected: HTF bias is bullish")
                        continue
                    if htf_bias == "bearish" and side == "long":
                        rejected.append("Counter-trend LONG rejected: HTF bias is bearish")
                        continue

            expected_sweep = "low" if side == "long" else "high"
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
                        stop = extreme - buffer if side == "long" else extreme + buffer
                        event_id = str(sweep.get("event_id") or (
                            f"sweep:{side}:{sweep.get('level_id', '')}:"
                            f"{sweep.get('candle_time', sweep.get('candle_index', ''))}"
                        ))
                        entry_policy: Literal["market_eligible", "limit_retest_only"] = "market_eligible"
                        limit_zone_id = ""
                        candidate_entry = entry
                        if extension_atr > policy.market_entry_max_extension_atr:
                            limit_zone = _causal_limit_zone(signal, side, sweep)
                            if limit_zone is None:
                                rejected.append(
                                    f"Price extended {extension_atr:.2f} ATR; no causal OB/FVG retest exists"
                                )
                                continue
                            entry_policy = "limit_retest_only"
                            limit_zone_id = str(limit_zone.zone_id)
                            candidate_entry = float(limit_zone.mid)
                        target_val = (
                            _range_opposing_target(signal, side, candidate_entry)
                            if is_ranging
                            else _nearest_target(signal, side, candidate_entry)
                        ) if extreme > 0 else None
                        result = _candidate(
                            side=side, setup_type="sweep_reversal", entry=candidate_entry, stop=stop,
                            target=target_val,
                            grade="S" if strong else "A",
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
                            evidence=[f"Confirmed {sweep.get('event_type', 'sweep')} of the same {expected_sweep} swing and close reclaim",
                                      f"Sweep trigger age {sweep_age}/{policy.ttl_bars} bars; extension {extension_atr:.2f} ATR",
                                      f"CVD {flow_label} confirms {side}"],
                        )
                        candidates.append(result)
                        rejected.extend(result.rejection_reasons)
                    else:
                        rejected.append("CVD evidence is not bound to the same swept swing/reclaim")

            displacement = getattr(signal, "displacement", {}) or {}
            pressure = float(getattr(signal, "delta_ratio", 0.0))
            cvd_div = str(getattr(signal, "cvd_divergence", "none"))
            opposing_cvd = "bullish" if side == "short" else "bearish"
            opposing_divergence = (cvd_div == opposing_cvd)
            strongly_opposing_delta = (pressure > 0.20 if side == "short" else pressure < -0.20)
            flow_aligned = (pressure >= 0.10 if side == "long" else pressure <= -0.10)
            non_opposing_flow = (not opposing_divergence) and (not strongly_opposing_delta)

            if displacement.get("direction") == side and (flow_aligned or non_opposing_flow):
                zone, is_shallow = _first_lineage_retest(signal, frame, side, displacement, atr=atr)
                if zone is not None:
                    stop = float(zone.bottom) - buffer if side == "long" else float(zone.top) + buffer
                    zone_label = (
                        "S/R Flip Breaker Block" if zone.kind == "breaker"
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
                    if flow_aligned:
                        evidence_items.append(f"Aggressor delta ratio {pressure:+.1%} confirms {side}")
                    else:
                        evidence_items.append(f"Displacement continuation confirmed without opposing CVD (delta {pressure:+.1%})")

                    disp_target = (
                        _range_opposing_target(signal, side, entry)
                        if is_ranging
                        else _nearest_target(signal, side, entry)
                    )
                    result = _candidate(
                        side=side, setup_type=setup_type, entry=entry, stop=stop,
                        target=disp_target, grade="A",
                        invalidation_source=f"first_retest:{zone.zone_id}",
                        flow_confirmation_type="single_bar_aggressor_delta",
                        event_id=(
                            f"displacement:{side}:{displacement.get('structure_break_id')}:"
                            f"{getattr(zone, 'zone_id', '')}"
                        ),
                        entry_policy="market_eligible",
                        policy=policy,
                        evidence=evidence_items,
                    )
                    candidates.append(result)
                    rejected.extend(result.rejection_reasons)

            # --- Range Boundary Ping-Pong Resolver ---
            if is_ranging and non_opposing_flow:
                if side == "long":
                    bullish_obs = [
                        z for z in getattr(signal, "order_blocks", [])
                        if z.direction == "bullish" and not z.mitigated
                        and float(z.bottom) - buffer <= entry <= float(z.top) + atr * 0.75
                    ]
                    bullish_fvgs = [
                        z for z in getattr(signal, "fvgs", [])
                        if z.direction == "bullish" and not z.mitigated
                        and float(z.bottom) - buffer <= entry <= float(z.top) + atr * 0.75
                    ]
                    at_demand = getattr(signal, "in_discount", False) or bool(bullish_obs) or bool(bullish_fvgs)
                    if at_demand:
                        if bullish_obs:
                            stop = min(float(z.bottom) for z in bullish_obs) - buffer
                            zone_id = str(bullish_obs[0].zone_id)
                        elif bullish_fvgs:
                            stop = min(float(z.bottom) for z in bullish_fvgs) - buffer
                            zone_id = str(bullish_fvgs[0].zone_id)
                        else:
                            rz = getattr(signal, "range_zones", {}) or {}
                            d_zone = rz.get("discount_zone", {})
                            d_bottom = float(d_zone.get("bottom", 0.0) or 0.0)
                            stop = (d_bottom - buffer) if d_bottom > 0 else (entry - atr * 1.5)
                            zone_id = "discount_boundary"

                        target = _range_opposing_target(signal, "long", entry)
                        if target is not None and stop < entry < target:
                            candidates.append(_candidate(
                                side="long",
                                setup_type="range_boundary_ping_pong",
                                entry=entry,
                                stop=stop,
                                target=target,
                                grade="A",
                                invalidation_source=f"demand_boundary:{zone_id}",
                                flow_confirmation_type="single_bar_aggressor_delta",
                                event_id=f"ping_pong:long:{getattr(signal, 'symbol', 'UNKNOWN')}:{len(frame)}",
                                entry_policy="market_eligible",
                                policy=policy,
                                evidence=[
                                    "Price tested SMC Demand Zone in Ranging Market",
                                    f"Targeting opposing Major Supply boundary at {target:.2f}",
                                    f"Flow non-opposing (delta ratio {pressure:+.1%})",
                                ],
                            ))

                elif side == "short":
                    bearish_obs = [
                        z for z in getattr(signal, "order_blocks", [])
                        if z.direction == "bearish" and not z.mitigated
                        and float(z.bottom) - atr * 0.75 <= entry <= float(z.top) + buffer
                    ]
                    bearish_fvgs = [
                        z for z in getattr(signal, "fvgs", [])
                        if z.direction == "bearish" and not z.mitigated
                        and float(z.bottom) - atr * 0.75 <= entry <= float(z.top) + buffer
                    ]
                    at_supply = getattr(signal, "in_premium", False) or bool(bearish_obs) or bool(bearish_fvgs)
                    if at_supply:
                        if bearish_obs:
                            stop = max(float(z.top) for z in bearish_obs) + buffer
                            zone_id = str(bearish_obs[0].zone_id)
                        elif bearish_fvgs:
                            stop = max(float(z.top) for z in bearish_fvgs) + buffer
                            zone_id = str(bearish_fvgs[0].zone_id)
                        else:
                            rz = getattr(signal, "range_zones", {}) or {}
                            p_zone = rz.get("premium_zone", {})
                            p_top = float(p_zone.get("top", 0.0) or 0.0)
                            stop = (p_top + buffer) if p_top > 0 else (entry + atr * 1.5)
                            zone_id = "premium_boundary"

                        target = _range_opposing_target(signal, "short", entry)
                        if target is not None and target < entry < stop:
                            candidates.append(_candidate(
                                side="short",
                                setup_type="range_boundary_ping_pong",
                                entry=entry,
                                stop=stop,
                                target=target,
                                grade="A",
                                invalidation_source=f"supply_boundary:{zone_id}",
                                flow_confirmation_type="single_bar_aggressor_delta",
                                event_id=f"ping_pong:short:{getattr(signal, 'symbol', 'UNKNOWN')}:{len(frame)}",
                                entry_policy="market_eligible",
                                policy=policy,
                                evidence=[
                                    "Price tested SMC Supply Zone in Ranging Market",
                                    f"Targeting opposing Major Demand boundary at {target:.2f}",
                                    f"Flow non-opposing (delta ratio {pressure:+.1%})",
                                ],
                            ))

        actionable = [candidate for candidate in candidates if candidate.actionable]
        if not actionable:
            reasons = list(dict.fromkeys(rejected)) or [
                "No causal sweep+CVD reversal or first displacement-retest+CVD continuation"
            ]
            if candidates:
                candidates.sort(key=lambda candidate: candidate.risk_reward, reverse=True)
                armed = candidates[0]
                armed.rejection_reasons = reasons
                return armed
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
            return TriCoreSetup(
                trigger_state=state,
                event_id=event_id,
                state_reason=state_reason,
                entry_policy="no_chase" if state == "no_chase" else "none",
                rejection_reasons=reasons,
                expires_after_bars=policy.ttl_bars,
                policy_version=policy.version,
                calibration_status=policy.calibration_status,
            )
        actionable.sort(key=lambda candidate: (candidate.grade == "S", candidate.risk_reward), reverse=True)
        return actionable[0]

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

