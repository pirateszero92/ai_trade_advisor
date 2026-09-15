"""
Scenario & Pattern Matrix Engine
Combines Smart Money Concepts (SMC), Cumulative Volume Delta (CVD),
and Squeeze Momentum into 10 distinct institutional market scenarios.
Generates actionable 2-way contingency trading plans.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

ScenarioId = str


@dataclass
class ContingencyPlan:
    plan_a: str  # Primary setup
    plan_b: str  # Invalidation / Breakdown setup
    trigger_condition: str
    target_zone: str
    invalidation_level: float = 0.0


@dataclass
class ScenarioResult:
    scenario_id: ScenarioId
    name_th: str
    name_en: str
    archetype: str
    suggested_action: str
    setup_grade: str
    base_score: int
    entry_style: Literal["market", "limit", "wait"]
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit_1: float | None = None
    take_profit_2: float | None = None
    risk_reward: float = 0.0
    contingency_plan: ContingencyPlan = field(default_factory=lambda: ContingencyPlan("", "", "", ""))
    key_drivers: list[str] = field(default_factory=list)
    actionable: bool = False
    reaction_state: str | None = None
    reaction_evidence: dict[str, Any] = field(default_factory=dict)
    entry_status: str | None = None
    entry_block_reason: str | None = None
    reaction_id: str | None = None
    zone_id: str | None = None
    pressure_warning: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "name_th": self.name_th,
            "name_en": self.name_en,
            "archetype": self.archetype,
            "suggested_action": self.suggested_action,
            "setup_grade": self.setup_grade,
            "base_score": self.base_score,
            "entry_style": self.entry_style,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "risk_reward": self.risk_reward,
            "contingency_plan": {
                "plan_a": self.contingency_plan.plan_a,
                "plan_b": self.contingency_plan.plan_b,
                "trigger_condition": self.contingency_plan.trigger_condition,
                "target_zone": self.contingency_plan.target_zone,
                "invalidation_level": self.contingency_plan.invalidation_level,
            },
            "key_drivers": list(self.key_drivers),
            "actionable": self.actionable,
            "reaction_state": self.reaction_state,
            "reaction_evidence": dict(self.reaction_evidence),
            "entry_status": self.entry_status,
            "entry_block_reason": self.entry_block_reason,
            "reaction_id": self.reaction_id,
            "zone_id": self.zone_id,
            "pressure_warning": dict(self.pressure_warning),
        }


# FX pairs that need 4-decimal precision (exact suffix match, not substring)
_FX_SUFFIXES = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "XAUUSD", "XAGUSD"}


def _is_forex(sym: str) -> bool:
    """Return True only for genuine FX/spot-metal pairs (not USDT-quoted crypto)."""
    s = sym.upper().replace("/", "").replace("-", "").replace("_", "")
    return any(s == fx or s.endswith(fx) for fx in _FX_SUFFIXES)


def _fmt(val: float, sym: str = "") -> str:
    """Format price with appropriate precision based on magnitude and asset."""
    if val <= 0:
        return "$0.00"
    if val < 5.0 or _is_forex(sym):
        return f"${val:.4f}"
    if val >= 1000.0:
        return f"${val:,.2f}"
    return f"${val:.2f}"


class ScenarioMatrixEngine:
    """Classifies any market condition into one of 10 comprehensive institutional scenarios."""

    @staticmethod
    def classify_reaction(
        signal: Any, df: pd.DataFrame | None = None
    ) -> ScenarioResult | None:
        """Classify a closed-15M zone reaction independently of the primary scenario.

        The primary scenario hierarchy intentionally short-circuits on strong
        setup/watch candidates. A shallow probe disables those primary triggers
        while preserving price structure, active zones and order-flow evidence,
        allowing support/resistance reaction state to be emitted as a secondary,
        observation-only result.
        """
        if str(getattr(signal, "timeframe", "")).lower() != "15m":
            return None
        if df is None or df.empty:
            return None

        probe = copy(signal)
        probe.bos = False
        probe.choch = False
        probe.liquidity_swept = False
        probe.squeeze_status = "no_squeeze"
        probe.squeeze_momentum = 0.0
        probe.momentum_direction = ""
        probe.market_regime = {}

        # S2 examines only the selected primary OB before the reaction layer.
        # Preserve it in the active-zone collection, then clear the selector so
        # a watch-only S2 result cannot shadow the independent reaction result.
        active_zones = list(getattr(signal, "order_blocks", []) or [])
        primary_ob = getattr(signal, "order_block", None)
        if primary_ob is not None and primary_ob not in active_zones:
            active_zones.append(primary_ob)
        probe.order_blocks = active_zones
        probe.order_block = None

        result = ScenarioMatrixEngine.classify(probe, df=df)
        if result.archetype not in {"support_reaction", "resistance_reaction"}:
            return None
        # Defence in depth: this secondary layer can never authorize execution.
        was_actionable = result.actionable
        result.actionable = False
        if was_actionable:
            result.suggested_action = "wait_strategy_gate"
            result.entry_status = "CONFIRMED_WATCH"
            result.entry_block_reason = (
                "secondary reaction evidence cannot authorize execution"
            )
        result.entry_style = "wait"
        result.stop_loss = None
        result.take_profit_1 = None
        result.take_profit_2 = None
        result.risk_reward = 0.0
        return result

    @staticmethod
    def classify(signal: Any, df: pd.DataFrame | None = None) -> ScenarioResult:
        sym = str(getattr(signal, "symbol", ""))
        timeframe = str(getattr(signal, "timeframe", "")).lower()
        price = float(getattr(signal, "current_price", 0.0))
        if price <= 0.0 and df is not None and not df.empty:
            price = float(df["close"].iloc[-1])

        # 1. Extract Core Signals
        bias = str(getattr(signal, "bias", "neutral")).lower()
        bos = bool(getattr(signal, "bos", False))
        choch = bool(getattr(signal, "choch", False))
        in_discount = bool(getattr(signal, "in_discount", False))
        in_premium = bool(getattr(signal, "in_premium", False))
        eq = float(getattr(signal, "equilibrium", price))

        # Order Block & FVG
        ob = getattr(signal, "order_block", None)
        has_bull_ob = bool(ob and getattr(ob, "direction", "") == "bullish")
        has_bear_ob = bool(ob and getattr(ob, "direction", "") == "bearish")
        ob_top = float(getattr(ob, "top", 0.0)) if ob else 0.0
        ob_bottom = float(getattr(ob, "bottom", 0.0)) if ob else 0.0

        # Liquidity Sweeps
        swept = bool(getattr(signal, "liquidity_swept", False))
        sweep_dir = str(getattr(signal, "sweep_direction", "none")).lower()
        sweep_p = float(getattr(signal, "sweep_price", 0.0) or price)

        # Volume Delta / CVD
        delta_ratio = float(getattr(signal, "delta_ratio", 0.0))
        absorption = bool(getattr(signal, "delta_absorption", False))
        absorption_type = getattr(signal, "delta_absorption_type", None)
        is_bull_absorp = absorption and absorption_type == "bullish_absorption"
        is_bear_absorp = absorption and absorption_type == "bearish_absorption"
        vol_spike = bool(getattr(signal, "volume_spike", False))

        # Squeeze Momentum
        sqz_status = str(getattr(signal, "squeeze_status", "no_squeeze")).lower()
        sqz_mom = float(getattr(signal, "squeeze_momentum", 0.0))
        mom_dir = str(getattr(signal, "momentum_direction", "")).lower()

        # Market Regime
        regime_dict = getattr(signal, "market_regime", {}) or {}
        regime_dir = str(regime_dict.get("direction", "")).lower()

        # ATR / Volatility helper for dynamic stop buffers
        atr = price * 0.005
        if df is not None and len(df) >= 14:
            tr = pd.concat([
                df["high"] - df["low"],
                (df["high"] - df["close"].shift(1)).abs(),
                (df["low"] - df["close"].shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr_val = float(tr.tail(14).mean())
            if atr_val > 0:
                atr = atr_val

        # Candle Quality & Volume Liquidity Pre-Flight Filters
        body_ratio = 0.70
        vol_ratio = 1.0
        if df is not None and len(df) >= 20:
            c_open = float(df["open"].iloc[-1])
            c_close = float(df["close"].iloc[-1])
            c_high = float(df["high"].iloc[-1])
            c_low = float(df["low"].iloc[-1])
            c_range = max(c_high - c_low, 1e-6)
            body_ratio = abs(c_close - c_open) / c_range

            if "volume" in df.columns:
                vol_ma = float(df["volume"].tail(20).mean())
                curr_vol = float(df["volume"].iloc[-1])
                vol_ratio = curr_vol / vol_ma if vol_ma > 0 else 1.0

        last_open = price
        last_high = price
        last_low = price
        last_close = price
        if df is not None and not df.empty:
            last_open = float(df["open"].iloc[-1])
            last_high = float(df["high"].iloc[-1])
            last_low = float(df["low"].iloc[-1])
            last_close = float(df["close"].iloc[-1])

        def structural_target(side: str, entry: float) -> float | None:
            """Nearest opposing liquidity/structure; never manufacture an R multiple."""
            levels: list[float] = []
            if side == "long":
                levels.extend(float(x) for x in getattr(signal, "equal_highs", []) if float(x) > entry)
                levels.extend(
                    float(getattr(x, "price", 0.0))
                    for x in getattr(signal, "swing_highs", [])
                    if float(getattr(x, "price", 0.0)) > entry
                )
                levels.extend(
                    float(getattr(x, "bottom", 0.0))
                    for x in getattr(signal, "order_blocks", [])
                    if getattr(x, "direction", "") == "bearish" and float(getattr(x, "bottom", 0.0)) > entry
                )
                return min(levels) if levels else None
            levels.extend(float(x) for x in getattr(signal, "equal_lows", []) if 0 < float(x) < entry)
            levels.extend(
                float(getattr(x, "price", 0.0))
                for x in getattr(signal, "swing_lows", [])
                if 0 < float(getattr(x, "price", 0.0)) < entry
            )
            levels.extend(
                float(getattr(x, "top", 0.0))
                for x in getattr(signal, "order_blocks", [])
                if getattr(x, "direction", "") == "bullish" and 0 < float(getattr(x, "top", 0.0)) < entry
            )
            return max(levels) if levels else None

        def observation(
            scenario_id: str,
            name_th: str,
            name_en: str,
            archetype: str,
            reason: str,
        ) -> ScenarioResult:
            return ScenarioResult(
                scenario_id=scenario_id,
                name_th=name_th,
                name_en=name_en,
                archetype=archetype,
                suggested_action="wait_confirmation",
                setup_grade="WATCH",
                base_score=45,
                entry_style="wait",
                entry_price=price,
                actionable=False,
                contingency_plan=ContingencyPlan(
                    plan_a=f"รอ (WAIT): {reason}",
                    plan_b="ยกเลิกการเฝ้าดูเมื่อโครงสร้างหรือโซนถูก invalidate",
                    trigger_condition=reason,
                    target_zone="Nearest opposing liquidity after confirmation",
                    invalidation_level=price,
                ),
                key_drivers=[reason],
            )

        def reaction_entry(
            *,
            side: Literal["long", "short"],
            scenario_id: str,
            name_th: str,
            name_en: str,
            zone_bottom: float,
            zone_top: float,
            reaction_age_bars: int,
            evidence: dict[str, Any],
            trigger_reason: str,
        ) -> tuple[ScenarioResult | None, str | None]:
            """Promote a confirmed 15M reaction inside a short-lived entry window.

            A touch alone never reaches this helper.  The caller must first
            verify a closed rejection candle and aligned order flow.  The
            window prevents the scanner from waiting for a second scenario
            until price has already travelled to opposing liquidity.
            """
            entry_window_bars = 3
            if reaction_age_bars < 0 or reaction_age_bars >= entry_window_bars:
                return None, "entry window expired; do not chase"

            if side == "long":
                extension = max(0.0, price - zone_top)
                sl = round(zone_bottom - atr * 0.30, 4)
                target = structural_target("long", price)
                geometry_ok = sl < price and target is not None and target > price
                rr = (float(target) - price) / max(price - sl, 1e-6) if geometry_ok else 0.0
                action = "buy_market"
                invalidated = last_close < zone_bottom - atr * 0.15
            else:
                extension = max(0.0, zone_bottom - price)
                sl = round(zone_top + atr * 0.30, 4)
                target = structural_target("short", price)
                geometry_ok = sl > price and target is not None and 0 < target < price
                rr = (price - float(target)) / max(sl - price, 1e-6) if geometry_ok else 0.0
                action = "sell_market"
                invalidated = last_close > zone_top + atr * 0.15

            # Do not chase a reaction after it has expanded away from the zone,
            # and never manufacture a target to rescue R:R.
            if invalidated:
                return None, "reaction invalidated by the latest closed 15M candle"
            if extension > atr * 1.25:
                return (
                    None,
                    f"price extended {extension / max(atr, 1e-9):.2f} ATR from the reaction zone; do not chase",
                )
            if target is None:
                target_side = "above" if side == "long" else "below"
                return None, f"no opposing 15M liquidity target {target_side} price"
            if not geometry_ok:
                return None, "invalid entry/stop/target geometry"
            if rr < 1.5:
                return None, f"structural R:R {rr:.2f} < 1.50; do not chase"

            enriched_evidence = {
                **evidence,
                "reaction_age_bars": reaction_age_bars,
                "entry_window_bars": entry_window_bars,
                "entry_window_remaining_bars": entry_window_bars - reaction_age_bars - 1,
                "extension_atr": round(extension / max(atr, 1e-9), 4),
            }
            target_value = round(float(target), 4)
            return ScenarioResult(
                scenario_id=scenario_id,
                name_th=name_th,
                name_en=name_en,
                archetype=(
                    "support_reaction" if side == "long" else "resistance_reaction"
                ),
                suggested_action=action,
                setup_grade="GRADE_A",
                base_score=78,
                entry_style="market",
                entry_price=round(price, 4),
                stop_loss=sl,
                take_profit_1=target_value,
                take_profit_2=target_value,
                risk_reward=round(rr, 2),
                actionable=True,
                reaction_state="ENTRY_WINDOW_OPEN",
                reaction_evidence=enriched_evidence,
                entry_status="ENTRY_WINDOW_OPEN",
                reaction_id=str(evidence.get("reaction_id") or "") or None,
                zone_id=str(evidence.get("zone_id") or "") or None,
                contingency_plan=ContingencyPlan(
                    plan_a=(
                        f"พิจารณา {'LONG' if side == 'long' else 'SHORT'} ภายในหน้าต่างยืนยัน "
                        f"{entry_window_bars} แท่ง 15M ที่ {_fmt(price, sym)}; "
                        f"SL {_fmt(sl, sym)} TP {_fmt(target_value, sym)}"
                    ),
                    plan_b=(
                        "ยกเลิกทันทีเมื่อแท่ง 15M ปิด invalidate โซน "
                        "หรือราคายืดเกิน 1.25 ATR จากจุด reaction"
                    ),
                    trigger_condition=trigger_reason,
                    target_zone=f"Opposing 15M liquidity ({_fmt(target_value, sym)})",
                    invalidation_level=sl,
                ),
                key_drivers=[
                    trigger_reason,
                    f"15M reaction age {reaction_age_bars} bar(s)",
                    f"Structural R:R {rr:.2f}",
                ],
            ), None

        # =========================================================================
        # Scenario Classification Hierarchy (Priority Ordered)
        # =========================================================================

        # -------------------------------------------------------------------------
        # Priority 1: Momentum Impulse Breakouts (BOS / CHoCH + Squeeze Fire + Volume Pre-Flight)
        # -------------------------------------------------------------------------
        has_bull_squeeze_fire = sqz_status == "squeeze_fire" and sqz_mom > 0
        is_bull_momentum = has_bull_squeeze_fire and (bos or choch) and bias == "bullish"
        is_bull_preflight_ok = (body_ratio >= 0.40 or delta_ratio >= 0.15 or vol_spike) and (vol_ratio >= 1.10 or vol_spike or delta_ratio > 0.08)
        if is_bull_momentum and is_bull_preflight_ok and (bias == "bullish" or delta_ratio > 0.05 or sqz_mom > 0):
            # S1-BULL: Bullish Breakout
            aligned_breaks = [
                item for item in (
                    list(getattr(signal, "swing_structures", []))
                    + list(getattr(signal, "internal_structures", []))
                )
                if getattr(item, "direction", "") == "bullish"
            ]
            if not aligned_breaks:
                return observation(
                    "S1_BULL_BREAKOUT_WATCH",
                    "👀 Bullish Breakout — ไม่มี event level",
                    "Bullish Breakout Awaiting Registered Structure Event",
                    "breakout_watch",
                    "BOS/CHoCH ต้องอ้างอิง structure event ที่มีทิศทางและระดับชัดเจน",
                )
            break_level = float(aligned_breaks[-1].level)
            if price - break_level > atr * 1.25:
                return observation(
                    "S1_BULL_BREAKOUT_LATE",
                    "⏳ Bullish Breakout — ราคายืดเกินจุดไล่",
                    "Late Bullish Breakout Extension",
                    "breakout_watch",
                    "ราคาห่างระดับ break เกิน 1.25 ATR จึงไม่ไล่ราคา",
                )
            entry = price
            sl = round(min(last_low, break_level) - atr * 0.10, 4)
            target = structural_target("long", entry)
            if target is None:
                return observation(
                    "S1_BULL_BREAKOUT_WATCH",
                    "👀 Bullish Breakout — ไม่มีเป้าสภาพคล่อง",
                    "Bullish Breakout Without Structural Target",
                    "breakout_watch",
                    "ยังไม่พบ opposing liquidity เหนือราคา",
                )
            tp1 = round(target, 4)
            tp2 = tp1
            rr = (tp1 - entry) / max(entry - sl, 1e-6)
            if rr < 1.5:
                return observation(
                    "S1_BULL_BREAKOUT_WATCH",
                    "👀 Bullish Breakout — R:R ไม่พอ",
                    "Bullish Breakout With Insufficient R:R",
                    "breakout_watch",
                    f"เป้าสภาพคล่องจริงให้ R:R เพียง {rr:.2f}",
                )
            return ScenarioResult(
                scenario_id="S1_BULL_BREAKOUT",
                name_th="⚡ Bullish Momentum Breakout (ตามน้ำแท่งระเบิด)",
                name_en="Bullish Momentum Breakout with Verified Squeeze Fire",
                archetype="breakout",
                suggested_action="buy_market",
                setup_grade="GRADE_S",
                base_score=92,
                entry_style="market",
                entry_price=entry,
                stop_loss=sl,
                take_profit_1=tp1,
                take_profit_2=tp2,
                risk_reward=round(rr, 2),
                actionable=True,
                contingency_plan=ContingencyPlan(
                    plan_a=f"เปิด BUY ทันทีที่ราคาตลาด {_fmt(entry, sym)} SL ที่โคนแท่ง 15M {_fmt(sl, sym)} ล็อคเป้า TP ที่ {_fmt(tp1, sym)}",
                    plan_b=f"หากราคาดีดแตะ +1.0R เลื่อน SL มากันทุนทันที (Auto-BE) หากหมดแรงหลุด {_fmt(sl, sym)} คัทลอส",
                    trigger_condition=f"แท่งเทียนปิดทำ BOS/CHoCH (Body {body_ratio*100:.0f}%, Vol {vol_ratio:.1f}x) พร้อมสัญญาณ Squeeze Fire",
                    target_zone=f"Expansion High ({_fmt(tp1, sym)})",
                    invalidation_level=sl,
                ),
                key_drivers=["BOS/CHoCH Breakout", "Verified Bullish Squeeze Fire", f"Volume Expansion ({vol_ratio:.1f}x)", "Solid Candle Body"],
            )

        has_bear_squeeze_fire = sqz_status == "squeeze_fire" and sqz_mom < 0
        is_bear_momentum = has_bear_squeeze_fire and (bos or choch) and bias == "bearish"
        is_bear_preflight_ok = (body_ratio >= 0.40 or delta_ratio <= -0.15 or vol_spike) and (vol_ratio >= 1.10 or vol_spike or delta_ratio < -0.08)
        if is_bear_momentum and is_bear_preflight_ok and (bias == "bearish" or delta_ratio < -0.05 or sqz_mom < 0):
            # S1-BEAR: Bearish Breakdown
            aligned_breaks = [
                item for item in (
                    list(getattr(signal, "swing_structures", []))
                    + list(getattr(signal, "internal_structures", []))
                )
                if getattr(item, "direction", "") == "bearish"
            ]
            if not aligned_breaks:
                return observation(
                    "S1_BEAR_BREAKDOWN_WATCH",
                    "👀 Bearish Breakdown — ไม่มี event level",
                    "Bearish Breakdown Awaiting Registered Structure Event",
                    "breakout_watch",
                    "BOS/CHoCH ต้องอ้างอิง structure event ที่มีทิศทางและระดับชัดเจน",
                )
            break_level = float(aligned_breaks[-1].level)
            if break_level - price > atr * 1.25:
                return observation(
                    "S1_BEAR_BREAKDOWN_LATE",
                    "⏳ Bearish Breakdown — ราคายืดเกินจุดไล่",
                    "Late Bearish Breakdown Extension",
                    "breakout_watch",
                    "ราคาห่างระดับ break เกิน 1.25 ATR จึงไม่ไล่ราคา",
                )
            entry = price
            sl = round(max(last_high, break_level) + atr * 0.10, 4)
            target = structural_target("short", entry)
            if target is None:
                return observation(
                    "S1_BEAR_BREAKDOWN_WATCH",
                    "👀 Bearish Breakdown — ไม่มีเป้าสภาพคล่อง",
                    "Bearish Breakdown Without Structural Target",
                    "breakout_watch",
                    "ยังไม่พบ opposing liquidity ใต้ราคา",
                )
            tp1 = round(target, 4)
            tp2 = tp1
            rr = (entry - tp1) / max(sl - entry, 1e-6)
            if rr < 1.5:
                return observation(
                    "S1_BEAR_BREAKDOWN_WATCH",
                    "👀 Bearish Breakdown — R:R ไม่พอ",
                    "Bearish Breakdown With Insufficient R:R",
                    "breakout_watch",
                    f"เป้าสภาพคล่องจริงให้ R:R เพียง {rr:.2f}",
                )
            return ScenarioResult(
                scenario_id="S1_BEAR_BREAKDOWN",
                name_th="⚡ Bearish Momentum Breakdown (ตามน้ำทุบหลุดแนวรับ)",
                name_en="Bearish Momentum Breakdown with Verified Squeeze Fire",
                archetype="breakout",
                suggested_action="sell_market",
                setup_grade="GRADE_S",
                base_score=92,
                entry_style="market",
                entry_price=entry,
                stop_loss=sl,
                take_profit_1=tp1,
                take_profit_2=tp2,
                risk_reward=round(rr, 2),
                actionable=True,
                contingency_plan=ContingencyPlan(
                    plan_a=f"เปิด SHORT ทันทีที่ราคาตลาด {_fmt(entry, sym)} SL ที่ยอดแท่ง 15M {_fmt(sl, sym)} ล็อคเป้า TP ที่ {_fmt(tp1, sym)}",
                    plan_b=f"หากราคาลงแตะ +1.0R เลื่อน SL มากันทุนทันที (Auto-BE) หากดีดกลับหลุด {_fmt(sl, sym)} คัทลอส",
                    trigger_condition="แท่งเทียนปิดทำ Bearish BOS/CHoCH พร้อมสัญญาณ Squeeze Fire สีแดง",
                    target_zone=f"Expansion Low ({_fmt(tp1, sym)})",
                    invalidation_level=sl,
                ),
                key_drivers=["Bearish BOS/CHoCH", "Squeeze Fired Down", "Aggressive Seller Outflow"],
            )

        # -------------------------------------------------------------------------
        # Priority 2: Institutional Pullback & Order Block Retests
        # -------------------------------------------------------------------------
        # Use the same active OB collection as the reaction layer.  The old
        # implementation inspected only ``signal.order_block``; consequently
        # the reaction detector could see a valid demand/supply touch while S2
        # looked at a different primary block and rejected the setup.
        active_obs = list(getattr(signal, "order_blocks", []) or [])
        if ob is not None and ob not in active_obs:
            active_obs.append(ob)

        def touched_ob(direction: str) -> Any | None:
            candidates = []
            for candidate in active_obs:
                if str(getattr(candidate, "direction", "")).lower() != direction:
                    continue
                bottom, top = sorted((
                    float(getattr(candidate, "bottom", 0.0)),
                    float(getattr(candidate, "top", 0.0)),
                ))
                if bottom <= 0 or top <= 0:
                    continue
                price_inside = bottom - atr * 0.20 <= price <= top + atr * 0.20
                candle_intersects = (
                    last_low <= top + atr * 0.20
                    and last_high >= bottom - atr * 0.20
                )
                if price_inside or candle_intersects:
                    candidates.append(candidate)
            return min(
                candidates,
                key=lambda candidate: abs(float(getattr(candidate, "mid", price)) - price),
                default=None,
            )

        bull_retest_ob = touched_ob("bullish")
        bull_top = float(getattr(bull_retest_ob, "top", 0.0)) if bull_retest_ob else 0.0
        bull_bottom = float(getattr(bull_retest_ob, "bottom", 0.0)) if bull_retest_ob else 0.0
        bull_mid = float(getattr(bull_retest_ob, "mid", 0.0)) if bull_retest_ob else 0.0
        bull_ob_touched = bull_retest_ob is not None
        if bull_ob_touched and in_discount and bias != "bearish":
            bull_rejection = (
                last_low <= bull_top + atr * 0.20
                and last_close > max(bull_mid, last_open)
                and (is_bull_absorp or delta_ratio >= 0.08)
            )
            if not bull_rejection:
                return observation(
                    "S2_BULL_OB_WATCH",
                    "👀 Bullish OB Retest — รอแท่งปฏิเสธ",
                    "Bullish Order Block Retest Awaiting Confirmation",
                    "pullback_retest_watch",
                    "ราคาแตะ Bullish OB แต่ยังไม่มี rejection close และ order-flow ฝั่งซื้อ",
                )
            # S2-BULL: Bullish OB Retest (Dip Buy ใน Discount Zone)
            entry = round(bull_mid if bull_mid > 0 else (bull_top + bull_bottom) / 2, 4)
            if entry <= 0:
                entry = price
            sl = round(bull_bottom - atr * 0.3, 4)
            sl_dist = max(entry - sl, atr * 0.5)
            structural_tp = structural_target("long", entry)
            if structural_tp is None:
                return observation(
                    "S2_BULL_OB_WATCH",
                    "👀 Bullish OB Retest — ยังไม่มีเป้าสภาพคล่อง",
                    "Bullish Order Block Retest Without Structural Target",
                    "pullback_retest_watch",
                    "ยังไม่พบ opposing liquidity ที่ใช้คำนวณ achievable R:R ได้",
                )
            tp1 = round(structural_tp, 4)
            tp2 = tp1
            rr = round((tp1 - entry) / sl_dist, 2)
            if rr < 1.5:
                return observation(
                    "S2_BULL_OB_WATCH",
                    "👀 Bullish OB Retest — R:R ไม่พอ",
                    "Bullish Order Block Retest With Insufficient R:R",
                    "pullback_retest_watch",
                    f"เป้าสภาพคล่องจริงให้ R:R เพียง {rr:.2f}",
                )
            drivers = ["Bullish Order Block in Discount", "High R:R Asymmetry"]
            if is_bull_absorp:
                drivers.append("Smart Money Absorption Confirmed")
            if sqz_status == "squeeze_on":
                drivers.append("Volatility Compression Storing Energy")

            return ScenarioResult(
                scenario_id="S2_BULL_OB_RETEST",
                name_th="🎯 Bullish OB Retest (ย่อรับของถูกที่ Order Block แนวรับ)",
                name_en="Bullish Order Block Pullback in Discount Zone",
                archetype="pullback_retest",
                suggested_action="buy_limit_ob",
                setup_grade="GRADE_S" if is_bull_absorp else "GRADE_A",
                base_score=85 if is_bull_absorp else 78,
                entry_style="limit",
                entry_price=entry,
                stop_loss=sl,
                take_profit_1=tp1,
                take_profit_2=tp2,
                risk_reward=rr,
                actionable=True,
                contingency_plan=ContingencyPlan(
                    plan_a=f"ตั้ง Limit Buy ที่ขอบ/กึ่งกลาง Bullish OB {_fmt(entry, sym)} (SL แคบใต้กล่อง {_fmt(sl, sym)}, TP {_fmt(tp1, sym)})",
                    plan_b=f"⚠️ หากราคาเนื้อเทียนปิดหลุดใต้ {_fmt(sl, sym)} แสดงว่า OB แตก ให้ยกเลิกแผน Long และกลับหน้าเตรียม Short ตามน้ำ",
                    trigger_condition=f"ราคาย่อตัวแตะกล่อง Bullish OB [{_fmt(bull_bottom, sym)} - {_fmt(bull_top, sym)}] ในโซน Discount",
                    target_zone=f"Swing High / Premium Zone ({_fmt(tp1, sym)})",
                    invalidation_level=sl,
                ),
                key_drivers=drivers,
            )

        bear_retest_ob = touched_ob("bearish")
        bear_top = float(getattr(bear_retest_ob, "top", 0.0)) if bear_retest_ob else 0.0
        bear_bottom = float(getattr(bear_retest_ob, "bottom", 0.0)) if bear_retest_ob else 0.0
        bear_mid = float(getattr(bear_retest_ob, "mid", 0.0)) if bear_retest_ob else 0.0
        bear_ob_touched = bear_retest_ob is not None
        if bear_ob_touched and in_premium and bias != "bullish":
            bear_rejection = (
                last_high >= bear_bottom - atr * 0.20
                and last_close < min(bear_mid, last_open)
                and (is_bear_absorp or delta_ratio <= -0.08)
            )
            if not bear_rejection:
                return observation(
                    "S2_BEAR_OB_WATCH",
                    "👀 Bearish OB Retest — รอแท่งปฏิเสธ",
                    "Bearish Order Block Retest Awaiting Confirmation",
                    "pullback_retest_watch",
                    "ราคาแตะ Bearish OB แต่ยังไม่มี rejection close และ order-flow ฝั่งขาย",
                )
            # S2-BEAR: Bearish OB Retest (Sell on Rally ใน Premium Zone)
            entry = round(bear_mid if bear_mid > 0 else (bear_top + bear_bottom) / 2, 4)
            if entry <= 0:
                entry = price
            sl = round(bear_top + atr * 0.3, 4)
            sl_dist = max(sl - entry, atr * 0.5)
            structural_tp = structural_target("short", entry)
            if structural_tp is None:
                return observation(
                    "S2_BEAR_OB_WATCH",
                    "👀 Bearish OB Retest — ยังไม่มีเป้าสภาพคล่อง",
                    "Bearish Order Block Retest Without Structural Target",
                    "pullback_retest_watch",
                    "ยังไม่พบ opposing liquidity ที่ใช้คำนวณ achievable R:R ได้",
                )
            tp1 = round(structural_tp, 4)
            tp2 = tp1
            rr = round((entry - tp1) / sl_dist, 2)
            if rr < 1.5:
                return observation(
                    "S2_BEAR_OB_WATCH",
                    "👀 Bearish OB Retest — R:R ไม่พอ",
                    "Bearish Order Block Retest With Insufficient R:R",
                    "pullback_retest_watch",
                    f"เป้าสภาพคล่องจริงให้ R:R เพียง {rr:.2f}",
                )
            drivers = ["Bearish Order Block in Premium", "High R:R Asymmetry"]
            if is_bear_absorp:
                drivers.append("Smart Money Absorption Confirmed")
            if sqz_status == "squeeze_on":
                drivers.append("Volatility Compression Storing Energy")

            return ScenarioResult(
                scenario_id="S2_BEAR_OB_RETEST",
                name_th="🎯 Bearish OB Retest (ดัก Short ที่ Order Block แนวต้าน)",
                name_en="Bearish Order Block Pullback in Premium Zone",
                archetype="pullback_retest",
                suggested_action="sell_limit_ob",
                setup_grade="GRADE_S" if is_bear_absorp else "GRADE_A",
                base_score=85 if is_bear_absorp else 78,
                entry_style="limit",
                entry_price=entry,
                stop_loss=sl,
                take_profit_1=tp1,
                take_profit_2=tp2,
                risk_reward=rr,
                actionable=True,
                contingency_plan=ContingencyPlan(
                    plan_a=f"ตั้ง Limit Short ที่ขอบ/กึ่งกลาง Bearish OB {_fmt(entry, sym)} (SL แคบเหนือก้นกล่อง {_fmt(sl, sym)}, TP {_fmt(tp1, sym)})",
                    plan_b=f"⚠️ หากราคาเนื้อเทียนปิดทะลุเหนือ {_fmt(sl, sym)} แสดงว่าแนวต้านแตก ให้ยกเลิกแผน Short และกลับหน้าเตรียม Buy ตามน้ำ",
                    trigger_condition=f"ราคาดีดแตะกล่อง Bearish OB [{_fmt(bear_bottom, sym)} - {_fmt(bear_top, sym)}] ในโซน Premium",
                    target_zone=f"Swing Low / Discount Zone ({_fmt(tp1, sym)})",
                    invalidation_level=sl,
                ),
                key_drivers=drivers,
            )

        # -------------------------------------------------------------------------
        # Priority 3: Confirmed Liquidity Sweep Reversals (Strict Counter-Trend Safety)
        # -------------------------------------------------------------------------
        # Never trigger Top Sweep Short when momentum is surging violently bullish
        liquidity_tolerance = max(atr * 0.25, price * 0.001)
        registered_highs = [float(x) for x in getattr(signal, "equal_highs", [])]
        registered_highs.extend(float(getattr(x, "price", 0.0)) for x in getattr(signal, "swing_highs", []))
        registered_lows = [float(x) for x in getattr(signal, "equal_lows", [])]
        registered_lows.extend(float(getattr(x, "price", 0.0)) for x in getattr(signal, "swing_lows", []))
        registered_top_sweep = any(abs(sweep_p - level) <= liquidity_tolerance for level in registered_highs)
        registered_bottom_sweep = any(abs(sweep_p - level) <= liquidity_tolerance for level in registered_lows)

        allow_top_sweep = (
            swept and sweep_dir == "high" and
            registered_top_sweep and
            in_premium and
            not (sqz_status == "squeeze_fire" and sqz_mom > 0) and
            (is_bear_absorp or delta_ratio <= -0.08) and
            regime_dir != "bullish"
        )
        if allow_top_sweep:
            # S3-BEAR: Top Sweep Reversal (Short ดักยอด)
            entry = price
            sl = round(max(sweep_p, price) + atr * 0.5, 4)
            tp1 = round(ob_top if has_bull_ob and ob_top > 0 else eq, 4)
            tp2 = round(ob_bottom if has_bull_ob and ob_bottom > 0 else price - (sl - price) * 3.0, 4)
            sl_dist = max(sl - entry, 1e-6)
            rr = round((entry - tp1) / sl_dist, 2)
            if rr < 1.5:
                return observation(
                    "S3_BEAR_SWEEP_WATCH",
                    "👀 Bearish Sweep — R:R ไม่พอ",
                    "Bearish Sweep With Insufficient Structural R:R",
                    "liquidity_sweep_watch",
                    f"เป้าสภาพคล่องจริงให้ R:R เพียง {rr:.2f}",
                )
            return ScenarioResult(
                scenario_id="S3_BEAR_TOP_SWEEP",
                name_th="🪤 Bearish Top Sweep (ดัก Short ยอดหลอกกิน SL)",
                name_en="Bearish Liquidity Sweep at Premium Resistance",
                archetype="liquidity_sweep",
                suggested_action="sell_market",
                setup_grade="GRADE_S",
                base_score=88,
                entry_style="market",
                entry_price=entry,
                stop_loss=sl,
                take_profit_1=tp1,
                take_profit_2=tp2,
                risk_reward=rr,
                actionable=True,
                contingency_plan=ContingencyPlan(
                    plan_a=f"เปิด SHORT ทันทีหลัง Sweep ยอด {_fmt(sweep_p, sym)} เล็งเป้า TP1 ที่แนวรับ {_fmt(tp1, sym)} (SL {_fmt(sl, sym)})",
                    plan_b=f"หากราคากลืนแรงขายทะลุปิดแท่งยืนเหนือ {_fmt(sl, sym)} ให้คัทลอสทันที ยอมรับการ Breakout",
                    trigger_condition=f"ราคาแทงไส้เหนือ {_fmt(sweep_p, sym)} แต่ปิดแท่งกลับลงมาต่ำกว่า High เดิม",
                    target_zone=f"Bullish Demand / Discount Zone ({_fmt(tp1, sym)})",
                    invalidation_level=sl,
                ),
                key_drivers=["Bearish Liquidity Sweep", "Premium Zone Rejection", "Exhaustion Trap"],
            )

        # Never trigger Bottom Sweep Long when momentum is dumping violently bearish
        allow_bottom_sweep = (
            swept and sweep_dir == "low" and
            registered_bottom_sweep and
            in_discount and
            not (sqz_status == "squeeze_fire" and sqz_mom < 0) and
            (is_bull_absorp or delta_ratio >= 0.08) and
            regime_dir != "bearish"
        )
        if allow_bottom_sweep:
            # S3-BULL: Bottom Sweep Reversal (Long ดักก้นเหว)
            entry = price
            sl = round(min(sweep_p, price) - atr * 0.5, 4)
            tp1 = round(ob_bottom if has_bear_ob and ob_bottom > 0 else eq, 4)
            tp2 = round(ob_top if has_bear_ob and ob_top > 0 else price + (price - sl) * 3.0, 4)
            sl_dist = max(entry - sl, 1e-6)
            rr = round((tp1 - entry) / sl_dist, 2)
            if rr < 1.5:
                return observation(
                    "S3_BULL_SWEEP_WATCH",
                    "👀 Bullish Sweep — R:R ไม่พอ",
                    "Bullish Sweep With Insufficient Structural R:R",
                    "liquidity_sweep_watch",
                    f"เป้าสภาพคล่องจริงให้ R:R เพียง {rr:.2f}",
                )
            return ScenarioResult(
                scenario_id="S3_BULL_BOTTOM_SWEEP",
                name_th="🪤 Bullish Bottom Sweep (ดัก Long ก้นเหวรับแรงดีด)",
                name_en="Bullish Liquidity Sweep at Discount Support",
                archetype="liquidity_sweep",
                suggested_action="buy_market",
                setup_grade="GRADE_S",
                base_score=88,
                entry_style="market",
                entry_price=entry,
                stop_loss=sl,
                take_profit_1=tp1,
                take_profit_2=tp2,
                risk_reward=rr,
                actionable=True,
                contingency_plan=ContingencyPlan(
                    plan_a=f"เปิด LONG ทันทีหลัง Sweep ก้น {_fmt(sweep_p, sym)} เล็งเป้า TP1 ที่แนวต้าน {_fmt(tp1, sym)} (SL {_fmt(sl, sym)})",
                    plan_b=f"หากราคาทุบปิดแท่งหลุดต่ำกว่า {_fmt(sl, sym)} ให้คัทลอสทันที ยอมรับการ Breakdown",
                    trigger_condition=f"ราคาแทงไส้ใต้ {_fmt(sweep_p, sym)} แต่ดีดปิดแท่งกลับขึ้นมาสูงกว่า Low เดิม",
                    target_zone=f"Bearish Supply / Premium Zone ({_fmt(tp1, sym)})",
                    invalidation_level=sl,
                ),
                key_drivers=["Bullish Liquidity Sweep", "Discount Zone Absorption", "Spring Rebound"],
            )

        # -------------------------------------------------------------------------
        # Priority 4: Breaker Block Invalidation Flips
        # -------------------------------------------------------------------------
        if choch and bias == "bearish" and delta_ratio < -0.05:
            # S4-BEAR: Breaker Flip Short
            entry = price
            sl = round(price + atr * 0.8, 4)
            target = structural_target("short", entry)
            rr = (entry - target) / max(sl - entry, 1e-6) if target is not None else 0.0
            if target is None or rr < 1.5:
                return observation(
                    "S4_BEAR_BREAKER_WATCH",
                    "👀 Bearish Breaker — เป้าหมายไม่ผ่าน",
                    "Bearish Breaker Without Achievable Structural R:R",
                    "breaker_flip_watch",
                    "ยังไม่มี opposing liquidity ที่ให้ R:R อย่างน้อย 1.5",
                )
            tp1 = round(target, 4)
            return ScenarioResult(
                scenario_id="S4_BEAR_BREAKER_FLIP",
                name_th="🔄 Bearish Breaker Flip (แนวรับแตก กลับตัวเป็นขาลง)",
                name_en="Bearish Breaker Block Flip on Structure Breakdown",
                archetype="breaker_flip",
                suggested_action="sell_market",
                setup_grade="GRADE_A",
                base_score=80,
                entry_style="market",
                entry_price=entry,
                stop_loss=sl,
                take_profit_1=tp1,
                risk_reward=round(rr, 2),
                actionable=True,
                contingency_plan=ContingencyPlan(
                    plan_a=f"เปิด SHORT ตามน้ำหรือรอ Retest ใต้แนวรับที่เพิ่งแตก {_fmt(entry, sym)} (SL {_fmt(sl, sym)}, TP {_fmt(tp1, sym)})",
                    plan_b=f"หากราคากระชากกลับขึ้นมายืนเหนือ {_fmt(sl, sym)} (False Breakdown) ให้คัทลอสทันที",
                    trigger_condition="เกิด Bearish CHoCH เนื้อเทียนปิดหลุด Swing Low สำคัญ",
                    target_zone=f"Next Demand Zone ({_fmt(tp1, sym)})",
                    invalidation_level=sl,
                ),
                key_drivers=["Bearish CHoCH Confirmation", "Breaker Block Flip", "Seller Aggression"],
            )

        if choch and bias == "bullish" and delta_ratio > 0.05:
            # S4-BULL: Breaker Flip Long
            entry = price
            sl = round(price - atr * 0.8, 4)
            target = structural_target("long", entry)
            rr = (target - entry) / max(entry - sl, 1e-6) if target is not None else 0.0
            if target is None or rr < 1.5:
                return observation(
                    "S4_BULL_BREAKER_WATCH",
                    "👀 Bullish Breaker — เป้าหมายไม่ผ่าน",
                    "Bullish Breaker Without Achievable Structural R:R",
                    "breaker_flip_watch",
                    "ยังไม่มี opposing liquidity ที่ให้ R:R อย่างน้อย 1.5",
                )
            tp1 = round(target, 4)
            return ScenarioResult(
                scenario_id="S4_BULL_BREAKER_FLIP",
                name_th="🔄 Bullish Breaker Flip (แนวต้านแตก กลับตัวเป็นขาขึ้น)",
                name_en="Bullish Breaker Block Flip on Structure Breakout",
                archetype="breaker_flip",
                suggested_action="buy_market",
                setup_grade="GRADE_A",
                base_score=80,
                entry_style="market",
                entry_price=entry,
                stop_loss=sl,
                take_profit_1=tp1,
                risk_reward=round(rr, 2),
                actionable=True,
                contingency_plan=ContingencyPlan(
                    plan_a=f"เปิด BUY ตามน้ำหรือรอ Retest เหนือแนวต้านที่เพิ่งแตก {_fmt(entry, sym)} (SL {_fmt(sl, sym)}, TP {_fmt(tp1, sym)})",
                    plan_b=f"หากราคาทุบหลุดต่ำกว่า {_fmt(sl, sym)} (Fakeout Breakout) ให้คัทลอสทันที",
                    trigger_condition="เกิด Bullish CHoCH เนื้อเทียนปิดทะลุ Swing High สำคัญ",
                    target_zone=f"Next Supply Zone ({_fmt(tp1, sym)})",
                    invalidation_level=sl,
                ),
                key_drivers=["Bullish CHoCH Confirmation", "Breaker Block Flip", "Buyer Aggression"],
            )

        # -------------------------------------------------------------------------
        # Priority 5: 15M Closed-Candle Support Reaction State Machine
        # -------------------------------------------------------------------------
        # This layer classifies what the just-closed execution candle did at a
        # registered support. It never predicts the next candle and never opens a
        # trade by itself; S2/S3/S4 above remain the only executable confirmations.
        if timeframe == "15m" and df is not None and not df.empty:
            support_tolerance = max(atr * 0.15, price * 0.0005)
            support_zones: list[dict[str, Any]] = []

            def add_support(
                bottom: float,
                top: float,
                source: str,
                *,
                zone: Any | None = None,
            ) -> None:
                bottom, top = sorted((float(bottom), float(top)))
                if bottom <= 0 or top <= 0:
                    return
                if isinstance(zone, dict):
                    confirmed_index = int(zone.get("confirmed_index", 0) or 0)
                    zone_id = str(
                        zone.get("zone_id") or zone.get("level_id") or ""
                    )
                else:
                    confirmed_index = int(getattr(zone, "confirmed_index", 0) or 0)
                    zone_id = str(
                        getattr(zone, "zone_id", "")
                        or getattr(zone, "level_id", "")
                        or ""
                    )
                if not zone_id:
                    zone_id = f"level:support:{source}:{bottom:.8f}:{top:.8f}"
                if any(item["zone_id"] == zone_id for item in support_zones):
                    return
                support_zones.append({
                    "bottom": bottom,
                    "top": top,
                    "source": source,
                    "zone_id": zone_id,
                    "confirmed_index": confirmed_index,
                })

            # Active bullish order blocks are support areas. Include the selected
            # OB and the complete active-zone list because the selected OB may be
            # a supply zone while price is interacting with demand.
            if has_bull_ob:
                add_support(ob_bottom, ob_top, "bullish_order_block", zone=ob)
            for active_ob in getattr(signal, "order_blocks", []) or []:
                if str(getattr(active_ob, "direction", "")).lower() == "bullish":
                    add_support(
                        float(getattr(active_ob, "bottom", 0.0)),
                        float(getattr(active_ob, "top", 0.0)),
                        "bullish_order_block",
                        zone=active_ob,
                    )

            # Registered equal/swing lows and the SMC strong/weak low are line
            # supports. They deliberately use only this signal's 15M structure;
            # no higher-timeframe field participates in classification or score.
            equal_low_metadata = getattr(signal, "equal_low_levels", []) or []
            if equal_low_metadata:
                for level in equal_low_metadata:
                    add_support(
                        float(level.get("price", 0.0)),
                        float(level.get("price", 0.0)),
                        "equal_low",
                        zone=level,
                    )
            else:
                for level in getattr(signal, "equal_lows", []) or []:
                    add_support(float(level), float(level), "equal_low")
            for swing in getattr(signal, "swing_lows", []) or []:
                add_support(
                    float(getattr(swing, "price", 0.0)),
                    float(getattr(swing, "price", 0.0)),
                    "swing_low",
                    zone=swing,
                )
            strong_weak_low = getattr(signal, "strong_weak_low", None)
            if isinstance(strong_weak_low, dict):
                add_support(
                    float(strong_weak_low.get("price", 0.0)),
                    float(strong_weak_low.get("price", 0.0)),
                    str(strong_weak_low.get("label", "smc_low")).lower().replace(" ", "_"),
                    zone=strong_weak_low,
                )

            # Search the just-closed candle and the two preceding 15M candles.
            # This is a local execution-timeframe latch, not MTF confirmation.
            support_interactions: list[dict[str, Any]] = []
            for zone in support_zones:
                touched_ages: list[int] = []
                for age in range(min(3, len(df))):
                    candle_index = len(df) - 1 - age
                    # A zone discovered by this candle (or later) did not exist
                    # when the reaction occurred and must not explain it in
                    # hindsight. Legacy/manual line levels use index 0.
                    if int(zone.get("confirmed_index", 0)) >= candle_index:
                        continue
                    candle = df.iloc[-1 - age]
                    candle_low = float(candle["low"])
                    candle_high = float(candle["high"])
                    if (
                        candle_low <= zone["top"] + support_tolerance
                        and candle_high >= zone["bottom"] - support_tolerance
                    ):
                        touched_ages.append(age)
                if touched_ages:
                    touch_age = max(touched_ages)
                    window = df.iloc[-1 - touch_age:]
                    confirmation_age = 0
                    for candidate_age in range(touch_age + 1):
                        candidate = df.iloc[-1 - candidate_age]
                        candidate_open = float(candidate["open"])
                        candidate_high = float(candidate["high"])
                        candidate_low = float(candidate["low"])
                        candidate_close = float(candidate["close"])
                        candidate_range = max(candidate_high - candidate_low, 1e-6)
                        candidate_body = abs(candidate_close - candidate_open) / candidate_range
                        candidate_location = (candidate_close - candidate_low) / candidate_range
                        bullish_confirm = (
                            candidate_close > zone["top"] + support_tolerance
                            and candidate_close > candidate_open
                            and candidate_body >= 0.35
                            and candidate_location >= 0.60
                        )
                        bearish_confirm = (
                            candidate_close < zone["bottom"] - support_tolerance
                            and candidate_close < candidate_open
                            and candidate_body >= 0.35
                            and candidate_location <= 0.40
                        )
                        if bullish_confirm or bearish_confirm:
                            confirmation_age = candidate_age
                            break
                    confirmation = df.iloc[-1 - confirmation_age]
                    support_interactions.append({
                        **zone,
                        "reaction_age_bars": touch_age,
                        "touch_candle_index": len(df) - 1 - touch_age,
                        "reaction_candle_index": len(df) - 1 - confirmation_age,
                        "confirmation_age_bars": confirmation_age,
                        "window_low": float(window["low"].min()),
                        "window_high": float(window["high"].max()),
                        "candle_open": float(confirmation["open"]),
                        "candle_high": float(confirmation["high"]),
                        "candle_low": float(confirmation["low"]),
                        "candle_close": float(confirmation["close"]),
                    })
            support_zones = support_interactions

            if support_zones:
                # Preserve the oldest still-valid interaction inside the reaction
                # window. A newly confirmed nearby zone must not reset a pending
                # confirmation. Distance breaks ties; expired zones were filtered.
                def distance_to_close(zone: dict[str, Any]) -> float:
                    if zone["bottom"] <= last_close <= zone["top"]:
                        return 0.0
                    return min(abs(last_close - zone["bottom"]), abs(last_close - zone["top"]))

                support = min(
                    support_zones,
                    key=lambda zone: (
                        -zone["reaction_age_bars"],
                        int(zone.get("confirmed_index", 0)),
                        distance_to_close(zone),
                    ),
                )
                support_bottom = float(support["bottom"])
                support_top = float(support["top"])
                support_mid = (support_bottom + support_top) / 2.0
                reaction_age = int(support["reaction_age_bars"])
                confirmation_age = int(support.get("confirmation_age_bars", 0))
                reaction_open = float(support["candle_open"])
                reaction_high = float(support["candle_high"])
                reaction_low = float(support["candle_low"])
                reaction_close = float(support["candle_close"])
                reaction_id = (
                    f"{sym}:{timeframe}:support:{support['zone_id']}:"
                    f"{int(support['touch_candle_index'])}"
                )
                candle_range = max(reaction_high - reaction_low, 1e-6)
                reaction_body_ratio = abs(reaction_close - reaction_open) / candle_range
                close_location = (reaction_close - reaction_low) / candle_range
                bullish_body = reaction_close > reaction_open and reaction_body_ratio >= 0.35
                bearish_body = reaction_close < reaction_open and reaction_body_ratio >= 0.35
                bullish_flow = (
                    is_bull_absorp
                    or delta_ratio >= 0.08
                    or ((bos or choch) and bias == "bullish")
                    or (sqz_status == "squeeze_fire" and sqz_mom > 0)
                )
                bearish_flow = (
                    is_bear_absorp
                    or delta_ratio <= -0.08
                    or ((bos or choch) and bias == "bearish")
                    or (sqz_status == "squeeze_fire" and sqz_mom < 0)
                )
                closed_above = reaction_close > support_top + support_tolerance
                closed_below = reaction_close < support_bottom - support_tolerance
                swept_below = float(support["window_low"]) < support_bottom - support_tolerance
                current_holds_above = last_close > support_bottom - support_tolerance

                evidence = {
                    "timeframe": "15m",
                    "candle_policy": "closed_only",
                    "support_source": support["source"],
                    "zone_id": support["zone_id"],
                    "zone_confirmed_index": int(support.get("confirmed_index", 0)),
                    "reaction_id": reaction_id,
                    "reaction_candle_index": int(support["reaction_candle_index"]),
                    "touch_candle_index": int(support["touch_candle_index"]),
                    "window_low": round(float(support["window_low"]), 8),
                    "window_high": round(float(support["window_high"]), 8),
                    "support_bottom": round(support_bottom, 8),
                    "support_top": round(support_top, 8),
                    "reaction_age_bars": reaction_age,
                    "confirmation_age_bars": confirmation_age,
                    "candle_open": round(reaction_open, 8),
                    "candle_high": round(reaction_high, 8),
                    "candle_low": round(reaction_low, 8),
                    "candle_close": round(reaction_close, 8),
                    "body_ratio": round(reaction_body_ratio, 4),
                    "close_location": round(close_location, 4),
                    "delta_ratio": round(delta_ratio, 4),
                    "bullish_flow": bullish_flow,
                    "bearish_flow": bearish_flow,
                }

                def support_observation(
                    state: str,
                    scenario_id: str,
                    name_th: str,
                    name_en: str,
                    reason: str,
                    plan_a: str,
                    plan_b: str,
                    base_score: int,
                    *,
                    entry_status: str = "AWAITING_CONFIRMATION",
                    entry_block_reason: str | None = None,
                    pressure_warning: dict[str, Any] | None = None,
                ) -> ScenarioResult:
                    confirmed_no_entry = entry_status == "CONFIRMED_NO_ENTRY"
                    pressure_only = entry_status == "PRESSURE_WARNING"
                    return ScenarioResult(
                        scenario_id=scenario_id,
                        name_th=name_th,
                        name_en=name_en,
                        archetype="support_reaction",
                        suggested_action=(
                            "do_not_chase" if confirmed_no_entry
                            else "monitor_breakdown" if pressure_only
                            else "wait_confirmation"
                        ),
                        setup_grade=(
                            "CONFIRMED" if confirmed_no_entry
                            else "PRESSURE" if pressure_only
                            else "WATCH"
                        ),
                        base_score=base_score,
                        entry_style="wait",
                        entry_price=price,
                        actionable=False,
                        reaction_state=state,
                        reaction_evidence=evidence,
                        entry_status=entry_status,
                        entry_block_reason=entry_block_reason,
                        reaction_id=reaction_id,
                        zone_id=support["zone_id"],
                        pressure_warning=pressure_warning or {},
                        contingency_plan=ContingencyPlan(
                            plan_a=(
                                f"ยืนยันปฏิกิริยาแล้ว แต่ไม่เปิดสถานะ: {entry_block_reason}; ห้ามไล่ราคา"
                                if confirmed_no_entry
                                else plan_a
                            ),
                            plan_b=plan_b,
                            trigger_condition=reason,
                            target_zone=f"15M support {_fmt(support_bottom, sym)} - {_fmt(support_top, sym)}",
                            invalidation_level=support_mid,
                        ),
                        key_drivers=[
                            reason,
                            f"15M closed candle at {support['source']}",
                            f"Delta ratio {delta_ratio:+.2f}",
                        ],
                    )

                if (
                    swept_below and closed_above and bullish_body and bullish_flow
                    and close_location >= 0.60 and current_holds_above
                ):
                    promoted, block_reason = reaction_entry(
                        side="long",
                        scenario_id="S8_SUPPORT_FALSE_BREAK_ENTRY",
                        name_th="🟢 False Break Reclaim — เปิดหน้าต่าง Long 15M",
                        name_en="15M Support False-Break Entry Window",
                        zone_bottom=support_bottom,
                        zone_top=support_top,
                        reaction_age_bars=reaction_age,
                        evidence=evidence,
                        trigger_reason="False break reclaim พร้อมแรงซื้อและราคายังยืนเหนือแนวรับ",
                    )
                    if promoted is not None:
                        return promoted
                    return support_observation(
                        "FALSE_BREAK_RECLAIM",
                        "S8_SUPPORT_FALSE_BREAK_RECLAIM",
                        "🟢 False Break Reclaim — แทงหลุดแนวรับแล้วปิดกลับเหนือโซน",
                        "15M Support False-Break Reclaim Confirmed",
                        "แท่ง 15M กวาดใต้แนวรับ แต่ปิดกลับเหนือโซนพร้อมแรงซื้อ",
                        "รอแท่งถัดไปยืนเหนือโซนหรือ retest ไม่หลุดก่อนพิจารณา Long",
                        "ถ้าปิดกลับใต้แนวรับ ให้ยกเลิกมุมมองเด้งและเปลี่ยนเป็น Breakdown Watch",
                        68,
                        entry_status="CONFIRMED_NO_ENTRY",
                        entry_block_reason=block_reason,
                    )

                if (
                    closed_above and bullish_body and bullish_flow
                    and close_location >= 0.60 and current_holds_above
                ):
                    promoted, block_reason = reaction_entry(
                        side="long",
                        scenario_id="S8_SUPPORT_BOUNCE_ENTRY",
                        name_th="🟢 Support Bounce — เปิดหน้าต่าง Long 15M",
                        name_en="15M Support Bounce Entry Window",
                        zone_bottom=support_bottom,
                        zone_top=support_top,
                        reaction_age_bars=reaction_age,
                        evidence=evidence,
                        trigger_reason="แท่งปิดปฏิเสธแนวรับพร้อมแรงซื้อและ R:R เชิงโครงสร้างผ่าน",
                    )
                    if promoted is not None:
                        return promoted
                    return support_observation(
                        "BOUNCE_CONFIRMED",
                        "S8_SUPPORT_BOUNCE_CONFIRMED",
                        "🟢 Bounce Confirmed — แท่ง 15M ปฏิเสธแนวรับ",
                        "15M Support Bounce Confirmed",
                        "แท่ง 15M ปิดเหนือแนวรับด้วยเนื้อเทียนและ order flow ฝั่งซื้อ",
                        "เฝ้ารอ retest ที่ยืนเหนือแนวรับหรือ S2/S3 ยืนยันครบก่อนเข้า Long",
                        "ถ้าแท่งถัดไปปิดใต้แนวรับ การเด้งล้มเหลวและต้องกลับเป็น Breakdown Watch",
                        65,
                        entry_status="CONFIRMED_NO_ENTRY",
                        entry_block_reason=block_reason,
                    )

                if closed_below and bearish_body and bearish_flow and close_location <= 0.40:
                    return support_observation(
                        "BREAKDOWN_CONFIRMED",
                        "S8_SUPPORT_BREAKDOWN_CONFIRMED",
                        "🔴 Breakdown Confirmed — แท่ง 15M ปิดหลุดแนวรับ",
                        "15M Support Breakdown Confirmed",
                        "แท่ง 15M ปิดใต้แนวรับด้วยเนื้อเทียนและ order flow ฝั่งขาย",
                        "ห้ามรับมีด; รอ retest ใต้แนวรับและ S4/BOS ยืนยันก่อนพิจารณา Short",
                        "ถ้าราคาปิด reclaim กลับเหนือโซน ให้ยกเลิกมุมมอง Breakdown",
                        65,
                        entry_status="CONFIRMED_WATCH",
                    )

                pressure_factors = {
                    "bearish_body_ge_60": bearish_body and reaction_body_ratio >= 0.60,
                    "close_in_bottom_20": close_location <= 0.20,
                    "seller_delta_le_minus_20": delta_ratio <= -0.20,
                    "momentum_accelerating_down": mom_dir == "accelerating_down",
                    "volume_expansion": vol_spike,
                }
                pressure_score = (
                    25 * int(pressure_factors["bearish_body_ge_60"])
                    + 25 * int(pressure_factors["close_in_bottom_20"])
                    + 25 * int(pressure_factors["seller_delta_le_minus_20"])
                    + 15 * int(pressure_factors["momentum_accelerating_down"])
                    + 10 * int(pressure_factors["volume_expansion"])
                )
                if (
                    confirmation_age == 0
                    and not closed_below
                    and bearish_body
                    and bearish_flow
                    and pressure_score >= 70
                ):
                    pressure = {
                        "side": "bearish",
                        "level": "HIGH" if pressure_score >= 85 else "ELEVATED",
                        "score": pressure_score,
                        "factors": pressure_factors,
                        "confirmation_trigger": round(support_bottom, 8),
                        "invalidation_reclaim": round(support_mid, 8),
                        "execution_authorized": False,
                    }
                    return support_observation(
                        "SUPPORT_FAILURE_PRESSURE",
                        "S8_SUPPORT_FAILURE_PRESSURE",
                        "🔶 Bearish Pressure — แนวรับเสี่ยง Breakdown",
                        "15M Support Failure Pressure Warning",
                        "แรงขายกดแนวรับรุนแรง แต่แท่งยังไม่ปิดหลุดขอบล่าง",
                        f"เตือนล่วงหน้า: เฝ้ารอแท่ง 15M ปิดต่ำกว่า {_fmt(support_bottom, sym)} เพื่อยืนยัน Breakdown",
                        f"ยกเลิกแรงกดดันเมื่อราคาปิด reclaim เหนือ {_fmt(support_mid, sym)}",
                        62,
                        entry_status="PRESSURE_WARNING",
                        pressure_warning=pressure,
                    )

                missing: list[str] = []
                if not bullish_flow and not bearish_flow:
                    missing.append("order flow ยังไม่เลือกข้าง")
                if not bullish_body and not bearish_body:
                    missing.append("เนื้อเทียนปิดยังไม่ชัด")
                if support_bottom - support_tolerance <= last_close <= support_top + support_tolerance:
                    missing.append("ราคายังปิดค้างในโซน")
                reason = "แตะแนวรับแล้ว แต่ " + ", ".join(missing or ["เงื่อนไขการเด้ง/หลุดยังไม่ครบ"])
                return support_observation(
                    "TOUCH_WAIT",
                    "S8_SUPPORT_TOUCH_WAIT",
                    "🟡 Support Touch — รอแท่ง 15M เลือกทาง",
                    "15M Support Touch Awaiting Closed-Candle Confirmation",
                    reason,
                    "WAIT: รอปิดเหนือโซนพร้อมแรงซื้อเพื่อยืนยัน Bounce",
                    "WAIT: รอปิดใต้โซนพร้อมแรงขายเพื่อยืนยัน Breakdown",
                    50,
                )

        # -------------------------------------------------------------------------
        # Priority 5B: 15M Closed-Candle Resistance Reaction State Machine
        # -------------------------------------------------------------------------
        # Symmetric with support reactions: this reports only what the closed 15M
        # candle proved at registered resistance. It cannot authorize a Short or
        # Long without one of the executable scenarios above.
        if timeframe == "15m" and df is not None and not df.empty:
            resistance_tolerance = max(atr * 0.15, price * 0.0005)
            resistance_zones: list[dict[str, Any]] = []

            def add_resistance(
                bottom: float,
                top: float,
                source: str,
                *,
                zone: Any | None = None,
            ) -> None:
                bottom, top = sorted((float(bottom), float(top)))
                if bottom <= 0 or top <= 0:
                    return
                if isinstance(zone, dict):
                    confirmed_index = int(zone.get("confirmed_index", 0) or 0)
                    zone_id = str(
                        zone.get("zone_id") or zone.get("level_id") or ""
                    )
                else:
                    confirmed_index = int(getattr(zone, "confirmed_index", 0) or 0)
                    zone_id = str(
                        getattr(zone, "zone_id", "")
                        or getattr(zone, "level_id", "")
                        or ""
                    )
                if not zone_id:
                    zone_id = f"level:resistance:{source}:{bottom:.8f}:{top:.8f}"
                if any(item["zone_id"] == zone_id for item in resistance_zones):
                    return
                resistance_zones.append({
                    "bottom": bottom,
                    "top": top,
                    "source": source,
                    "zone_id": zone_id,
                    "confirmed_index": confirmed_index,
                })

            if has_bear_ob:
                add_resistance(ob_bottom, ob_top, "bearish_order_block", zone=ob)
            for active_ob in getattr(signal, "order_blocks", []) or []:
                if str(getattr(active_ob, "direction", "")).lower() == "bearish":
                    add_resistance(
                        float(getattr(active_ob, "bottom", 0.0)),
                        float(getattr(active_ob, "top", 0.0)),
                        "bearish_order_block",
                        zone=active_ob,
                    )

            equal_high_metadata = getattr(signal, "equal_high_levels", []) or []
            if equal_high_metadata:
                for level in equal_high_metadata:
                    add_resistance(
                        float(level.get("price", 0.0)),
                        float(level.get("price", 0.0)),
                        "equal_high",
                        zone=level,
                    )
            else:
                for level in getattr(signal, "equal_highs", []) or []:
                    add_resistance(float(level), float(level), "equal_high")
            for swing in getattr(signal, "swing_highs", []) or []:
                add_resistance(
                    float(getattr(swing, "price", 0.0)),
                    float(getattr(swing, "price", 0.0)),
                    "swing_high",
                    zone=swing,
                )
            strong_weak_high = getattr(signal, "strong_weak_high", None)
            if isinstance(strong_weak_high, dict):
                add_resistance(
                    float(strong_weak_high.get("price", 0.0)),
                    float(strong_weak_high.get("price", 0.0)),
                    str(strong_weak_high.get("label", "smc_high")).lower().replace(" ", "_"),
                    zone=strong_weak_high,
                )

            resistance_interactions: list[dict[str, Any]] = []
            for zone in resistance_zones:
                touched_ages: list[int] = []
                for age in range(min(3, len(df))):
                    candle_index = len(df) - 1 - age
                    if int(zone.get("confirmed_index", 0)) >= candle_index:
                        continue
                    candle = df.iloc[-1 - age]
                    candle_low = float(candle["low"])
                    candle_high = float(candle["high"])
                    if (
                        candle_low <= zone["top"] + resistance_tolerance
                        and candle_high >= zone["bottom"] - resistance_tolerance
                    ):
                        touched_ages.append(age)
                if touched_ages:
                    touch_age = max(touched_ages)
                    window = df.iloc[-1 - touch_age:]
                    confirmation_age = 0
                    for candidate_age in range(touch_age + 1):
                        candidate = df.iloc[-1 - candidate_age]
                        candidate_open = float(candidate["open"])
                        candidate_high = float(candidate["high"])
                        candidate_low = float(candidate["low"])
                        candidate_close = float(candidate["close"])
                        candidate_range = max(candidate_high - candidate_low, 1e-6)
                        candidate_body = abs(candidate_close - candidate_open) / candidate_range
                        candidate_location = (candidate_close - candidate_low) / candidate_range
                        bearish_confirm = (
                            candidate_close < zone["bottom"] - resistance_tolerance
                            and candidate_close < candidate_open
                            and candidate_body >= 0.35
                            and candidate_location <= 0.40
                        )
                        bullish_confirm = (
                            candidate_close > zone["top"] + resistance_tolerance
                            and candidate_close > candidate_open
                            and candidate_body >= 0.35
                            and candidate_location >= 0.60
                        )
                        if bearish_confirm or bullish_confirm:
                            confirmation_age = candidate_age
                            break
                    confirmation = df.iloc[-1 - confirmation_age]
                    resistance_interactions.append({
                        **zone,
                        "reaction_age_bars": touch_age,
                        "touch_candle_index": len(df) - 1 - touch_age,
                        "reaction_candle_index": len(df) - 1 - confirmation_age,
                        "confirmation_age_bars": confirmation_age,
                        "window_low": float(window["low"].min()),
                        "window_high": float(window["high"].max()),
                        "candle_open": float(confirmation["open"]),
                        "candle_high": float(confirmation["high"]),
                        "candle_low": float(confirmation["low"]),
                        "candle_close": float(confirmation["close"]),
                    })
            resistance_zones = resistance_interactions

            if resistance_zones:
                # Same latch policy as support: preserve the pending interaction
                # until it expires or invalidates, rather than chase newer zones.
                def distance_to_close(zone: dict[str, Any]) -> float:
                    if zone["bottom"] <= last_close <= zone["top"]:
                        return 0.0
                    return min(abs(last_close - zone["bottom"]), abs(last_close - zone["top"]))

                resistance = min(
                    resistance_zones,
                    key=lambda zone: (
                        -zone["reaction_age_bars"],
                        int(zone.get("confirmed_index", 0)),
                        distance_to_close(zone),
                    ),
                )
                resistance_bottom = float(resistance["bottom"])
                resistance_top = float(resistance["top"])
                resistance_mid = (resistance_bottom + resistance_top) / 2.0
                reaction_age = int(resistance["reaction_age_bars"])
                confirmation_age = int(resistance.get("confirmation_age_bars", 0))
                reaction_open = float(resistance["candle_open"])
                reaction_high = float(resistance["candle_high"])
                reaction_low = float(resistance["candle_low"])
                reaction_close = float(resistance["candle_close"])
                reaction_id = (
                    f"{sym}:{timeframe}:resistance:{resistance['zone_id']}:"
                    f"{int(resistance['touch_candle_index'])}"
                )
                candle_range = max(reaction_high - reaction_low, 1e-6)
                reaction_body_ratio = abs(reaction_close - reaction_open) / candle_range
                close_location = (reaction_close - reaction_low) / candle_range
                bullish_body = reaction_close > reaction_open and reaction_body_ratio >= 0.35
                bearish_body = reaction_close < reaction_open and reaction_body_ratio >= 0.35
                bullish_flow = (
                    is_bull_absorp
                    or delta_ratio >= 0.08
                    or ((bos or choch) and bias == "bullish")
                    or (sqz_status == "squeeze_fire" and sqz_mom > 0)
                )
                bearish_flow = (
                    is_bear_absorp
                    or delta_ratio <= -0.08
                    or ((bos or choch) and bias == "bearish")
                    or (sqz_status == "squeeze_fire" and sqz_mom < 0)
                )
                closed_above = reaction_close > resistance_top + resistance_tolerance
                closed_below = reaction_close < resistance_bottom - resistance_tolerance
                swept_above = float(resistance["window_high"]) > resistance_top + resistance_tolerance
                current_holds_below = last_close < resistance_top + resistance_tolerance

                evidence = {
                    "timeframe": "15m",
                    "candle_policy": "closed_only",
                    "resistance_source": resistance["source"],
                    "zone_id": resistance["zone_id"],
                    "zone_confirmed_index": int(resistance.get("confirmed_index", 0)),
                    "reaction_id": reaction_id,
                    "reaction_candle_index": int(resistance["reaction_candle_index"]),
                    "touch_candle_index": int(resistance["touch_candle_index"]),
                    "window_low": round(float(resistance["window_low"]), 8),
                    "window_high": round(float(resistance["window_high"]), 8),
                    "resistance_bottom": round(resistance_bottom, 8),
                    "resistance_top": round(resistance_top, 8),
                    "reaction_age_bars": reaction_age,
                    "confirmation_age_bars": confirmation_age,
                    "candle_open": round(reaction_open, 8),
                    "candle_high": round(reaction_high, 8),
                    "candle_low": round(reaction_low, 8),
                    "candle_close": round(reaction_close, 8),
                    "body_ratio": round(reaction_body_ratio, 4),
                    "close_location": round(close_location, 4),
                    "delta_ratio": round(delta_ratio, 4),
                    "bullish_flow": bullish_flow,
                    "bearish_flow": bearish_flow,
                }

                def resistance_observation(
                    state: str,
                    scenario_id: str,
                    name_th: str,
                    name_en: str,
                    reason: str,
                    plan_a: str,
                    plan_b: str,
                    base_score: int,
                    *,
                    entry_status: str = "AWAITING_CONFIRMATION",
                    entry_block_reason: str | None = None,
                    pressure_warning: dict[str, Any] | None = None,
                ) -> ScenarioResult:
                    confirmed_no_entry = entry_status == "CONFIRMED_NO_ENTRY"
                    pressure_only = entry_status == "PRESSURE_WARNING"
                    return ScenarioResult(
                        scenario_id=scenario_id,
                        name_th=name_th,
                        name_en=name_en,
                        archetype="resistance_reaction",
                        suggested_action=(
                            "do_not_chase" if confirmed_no_entry
                            else "monitor_breakout" if pressure_only
                            else "wait_confirmation"
                        ),
                        setup_grade=(
                            "CONFIRMED" if confirmed_no_entry
                            else "PRESSURE" if pressure_only
                            else "WATCH"
                        ),
                        base_score=base_score,
                        entry_style="wait",
                        entry_price=price,
                        actionable=False,
                        reaction_state=state,
                        reaction_evidence=evidence,
                        entry_status=entry_status,
                        entry_block_reason=entry_block_reason,
                        reaction_id=reaction_id,
                        zone_id=resistance["zone_id"],
                        pressure_warning=pressure_warning or {},
                        contingency_plan=ContingencyPlan(
                            plan_a=(
                                f"ยืนยันปฏิกิริยาแล้ว แต่ไม่เปิดสถานะ: {entry_block_reason}; ห้ามไล่ราคา"
                                if confirmed_no_entry
                                else plan_a
                            ),
                            plan_b=plan_b,
                            trigger_condition=reason,
                            target_zone=(
                                f"15M resistance {_fmt(resistance_bottom, sym)} - "
                                f"{_fmt(resistance_top, sym)}"
                            ),
                            invalidation_level=resistance_mid,
                        ),
                        key_drivers=[
                            reason,
                            f"15M closed candle at {resistance['source']}",
                            f"Delta ratio {delta_ratio:+.2f}",
                        ],
                    )

                if (
                    swept_above and closed_below and bearish_body and bearish_flow
                    and close_location <= 0.40 and current_holds_below
                ):
                    promoted, block_reason = reaction_entry(
                        side="short",
                        scenario_id="S9_RESISTANCE_FALSE_BREAK_ENTRY",
                        name_th="🔴 False Break Rejection — เปิดหน้าต่าง Short 15M",
                        name_en="15M Resistance False-Break Entry Window",
                        zone_bottom=resistance_bottom,
                        zone_top=resistance_top,
                        reaction_age_bars=reaction_age,
                        evidence=evidence,
                        trigger_reason="False break เหนือแนวต้าน ปิดกลับพร้อมแรงขายและราคายังอยู่ใต้โซน",
                    )
                    if promoted is not None:
                        return promoted
                    return resistance_observation(
                        "FALSE_BREAK_REJECTION",
                        "S9_RESISTANCE_FALSE_BREAK_REJECTION",
                        "🔴 False Break Rejection — แทงเหนือแนวต้านแล้วปิดกลับใต้โซน",
                        "15M Resistance False-Break Rejection Confirmed",
                        "แท่ง 15M กวาดเหนือแนวต้าน แต่ปิดกลับใต้โซนพร้อมแรงขาย",
                        "รอแท่งถัดไปยืนใต้โซนหรือ retest ไม่ผ่านก่อนพิจารณา Short",
                        "ถ้าปิด reclaim เหนือแนวต้าน ให้ยกเลิกมุมมอง rejection",
                        68,
                        entry_status="CONFIRMED_NO_ENTRY",
                        entry_block_reason=block_reason,
                    )

                if (
                    closed_below and bearish_body and bearish_flow
                    and close_location <= 0.40 and current_holds_below
                ):
                    promoted, block_reason = reaction_entry(
                        side="short",
                        scenario_id="S9_RESISTANCE_REJECTION_ENTRY",
                        name_th="🔴 Resistance Rejection — เปิดหน้าต่าง Short 15M",
                        name_en="15M Resistance Rejection Entry Window",
                        zone_bottom=resistance_bottom,
                        zone_top=resistance_top,
                        reaction_age_bars=reaction_age,
                        evidence=evidence,
                        trigger_reason="แท่งปิดปฏิเสธแนวต้านพร้อมแรงขายและ R:R เชิงโครงสร้างผ่าน",
                    )
                    if promoted is not None:
                        return promoted
                    return resistance_observation(
                        "REJECTION_CONFIRMED",
                        "S9_RESISTANCE_REJECTION_CONFIRMED",
                        "🔴 Rejection Confirmed — แท่ง 15M ปฏิเสธแนวต้าน",
                        "15M Resistance Rejection Confirmed",
                        "แท่ง 15M ปิดใต้แนวต้านด้วยเนื้อเทียนและ order flow ฝั่งขาย",
                        "เฝ้ารอ retest ที่ไม่ผ่านแนวต้านหรือ S2/S3 ยืนยันครบก่อนเข้า Short",
                        "ถ้าแท่งถัดไปปิดเหนือแนวต้าน การ rejection ล้มเหลวและต้องกลับเป็น Breakout Watch",
                        65,
                        entry_status="CONFIRMED_NO_ENTRY",
                        entry_block_reason=block_reason,
                    )

                if closed_above and bullish_body and bullish_flow and close_location >= 0.60:
                    return resistance_observation(
                        "BREAKOUT_CONFIRMED",
                        "S9_RESISTANCE_BREAKOUT_CONFIRMED",
                        "🟢 Breakout Confirmed — แท่ง 15M ปิดผ่านแนวต้าน",
                        "15M Resistance Breakout Confirmed",
                        "แท่ง 15M ปิดเหนือแนวต้านด้วยเนื้อเทียนและ order flow ฝั่งซื้อ",
                        "ห้ามไล่ราคา; รอ retest เหนือแนวต้านและ S1/S4 ยืนยันก่อนพิจารณา Long",
                        "ถ้าราคาปิดกลับใต้โซน ให้ยกเลิกมุมมอง Breakout",
                        65,
                        entry_status="CONFIRMED_WATCH",
                    )

                pressure_factors = {
                    "bullish_body_ge_60": bullish_body and reaction_body_ratio >= 0.60,
                    "close_in_top_20": close_location >= 0.80,
                    "buyer_delta_ge_20": delta_ratio >= 0.20,
                    "momentum_accelerating_up": mom_dir == "accelerating_up",
                    "volume_expansion": vol_spike,
                }
                pressure_score = (
                    25 * int(pressure_factors["bullish_body_ge_60"])
                    + 25 * int(pressure_factors["close_in_top_20"])
                    + 25 * int(pressure_factors["buyer_delta_ge_20"])
                    + 15 * int(pressure_factors["momentum_accelerating_up"])
                    + 10 * int(pressure_factors["volume_expansion"])
                )
                if (
                    confirmation_age == 0
                    and not closed_above
                    and bullish_body
                    and bullish_flow
                    and pressure_score >= 70
                ):
                    pressure = {
                        "side": "bullish",
                        "level": "HIGH" if pressure_score >= 85 else "ELEVATED",
                        "score": pressure_score,
                        "factors": pressure_factors,
                        "confirmation_trigger": round(resistance_top, 8),
                        "invalidation_reclaim": round(resistance_mid, 8),
                        "execution_authorized": False,
                    }
                    return resistance_observation(
                        "RESISTANCE_BREAKOUT_PRESSURE",
                        "S9_RESISTANCE_BREAKOUT_PRESSURE",
                        "🔷 Bullish Pressure — แนวต้านเสี่ยง Breakout",
                        "15M Resistance Breakout Pressure Warning",
                        "แรงซื้อกดแนวต้านรุนแรง แต่แท่งยังไม่ปิดผ่านขอบบน",
                        f"เตือนล่วงหน้า: เฝ้ารอแท่ง 15M ปิดเหนือ {_fmt(resistance_top, sym)} เพื่อยืนยัน Breakout",
                        f"ยกเลิกแรงกดดันเมื่อราคาปิดกลับต่ำกว่า {_fmt(resistance_mid, sym)}",
                        62,
                        entry_status="PRESSURE_WARNING",
                        pressure_warning=pressure,
                    )

                missing: list[str] = []
                if not bullish_flow and not bearish_flow:
                    missing.append("order flow ยังไม่เลือกข้าง")
                if not bullish_body and not bearish_body:
                    missing.append("เนื้อเทียนปิดยังไม่ชัด")
                if resistance_bottom - resistance_tolerance <= last_close <= resistance_top + resistance_tolerance:
                    missing.append("ราคายังปิดค้างในโซน")
                reason = "แตะแนวต้านแล้ว แต่ " + ", ".join(missing or ["เงื่อนไข rejection/breakout ยังไม่ครบ"])
                return resistance_observation(
                    "TOUCH_WAIT",
                    "S9_RESISTANCE_TOUCH_WAIT",
                    "🟡 Resistance Touch — รอแท่ง 15M เลือกทาง",
                    "15M Resistance Touch Awaiting Closed-Candle Confirmation",
                    reason,
                    "WAIT: รอปิดใต้โซนพร้อมแรงขายเพื่อยืนยัน Rejection",
                    "WAIT: รอปิดเหนือโซนพร้อมแรงซื้อเพื่อยืนยัน Breakout",
                    50,
                )

        # -------------------------------------------------------------------------
        # Priority 6: Divergence & Momentum Exhaustion
        # -------------------------------------------------------------------------
        if in_premium and mom_dir in ("decelerating_up", "accelerating_down") and delta_ratio < 0:
            return ScenarioResult(
                scenario_id="S6_DIVERGENCE_EXHAUSTION",
                name_th="⚠️ Premium Exhaustion (แรงซื้อหมดบนยอด ระวังการย่อตัว)",
                name_en="Bullish Exhaustion at Premium Zone",
                archetype="exhaustion",
                suggested_action="tighten_sl",
                setup_grade="WAIT",
                base_score=50,
                entry_style="wait",
                entry_price=price,
                contingency_plan=ContingencyPlan(
                    plan_a=f"ห้ามไล่ซื้อ Buy บนยอด! หากถือ Long อยู่ให้กระชับ SL มาที่ {_fmt(price - atr, sym)} เพื่อล็อคกำไร",
                    plan_b="รอราคาแทงไส้กวาด Sweep หรือย่อลงมาทดสอบ Discount Demand ค่อยหาจังหวะเปิดไม้ใหม่",
                    trigger_condition="ราคาอยู่บนยอด Premium แต่โมเมนตัม Squeeze และ Delta เริ่มอ่อนแรง",
                    target_zone=f"Discount Retracement ({_fmt(eq, sym)})",
                    invalidation_level=price - atr,
                ),
                key_drivers=["Premium Zone", "Decelerating Momentum", "Buyer Exhaustion"],
            )

        # Mirror: Discount Exhaustion — failed bearish breakdown, bulls starting to absorb
        if in_discount and mom_dir in ("decelerating_down", "accelerating_up") and delta_ratio > 0:
            return ScenarioResult(
                scenario_id="S6_DIVERGENCE_EXHAUSTION",
                name_th="⚠️ Discount Exhaustion (แรงขายหมดในก้นเหว — ระวังการดีดตัวกลับ)",
                name_en="Bearish Exhaustion at Discount Zone",
                archetype="exhaustion",
                suggested_action="tighten_sl",
                setup_grade="WAIT",
                base_score=50,
                entry_style="wait",
                entry_price=price,
                contingency_plan=ContingencyPlan(
                    plan_a=f"ห้ามไล่ Short ในก้นเหว! หากถือ Short อยู่ให้กระชับ SL มาที่ {_fmt(price + atr, sym)} เพื่อล็อคกำไร",
                    plan_b="รอราคาแทงไส้กวาด Sweep Low หรือดีดขึ้นมาทดสอบ Premium Supply ค่อยหาจังหวะเปิดไม้ใหม่",
                    trigger_condition="ราคาอยู่ใน Discount แต่โมเมนตัม Squeeze และ Delta เริ่มอ่อนแรงฝั่งขาย — Bull เริ่ม Absorb",
                    target_zone=f"Premium Retracement ({_fmt(eq, sym)})",
                    invalidation_level=price + atr,
                ),
                key_drivers=["Discount Zone", "Decelerating Bearish Momentum", "Seller Exhaustion"],
            )

        # -------------------------------------------------------------------------
        # Priority 7: Mid-Range Squeeze Compression (Equilibrium 50% - Two-Way Blueprint)
        # -------------------------------------------------------------------------
        if sqz_status != "squeeze_on" and str(regime_dict.get("regime", "")) != "compression":
            return observation(
                "NEUTRAL_NO_EDGE",
                "⏸ Neutral — ยังไม่มี Edge",
                "Neutral Market Without Qualified Edge",
                "neutral_no_edge",
                "ยังไม่มี causal trigger ที่ผ่านเกณฑ์บนแท่ง 15M ที่ปิดแล้ว",
            )
        plan_a_entry = round(eq - atr * 1.5, 4)
        plan_b_entry = round(eq + atr * 1.5, 4)
        return ScenarioResult(
            scenario_id="S5_MID_RANGE_COMPRESSION",
            name_th="⏳ Mid-Range Compression (ราคากลางกรอบ แผน 2 หน้า)",
            name_en="Mid-Range Equilibrium Compression (Two-Way Plan)",
            archetype="compression_wait",
            suggested_action="wait_two_way_plan",
            setup_grade="WAIT",
            base_score=60,
            entry_style="wait",
            entry_price=price,
            contingency_plan=ContingencyPlan(
                plan_a=f"รอ (WAIT): เฝ้าดูการปิดยืนยันเหนือกรอบ {_fmt(plan_b_entry, sym)}",
                plan_b=f"รอ (WAIT): เฝ้าดูการปิดยืนยันใต้กรอบ {_fmt(plan_a_entry, sym)}",
                trigger_condition="ราคาวิ่งสะสมพลังอยู่กึ่งกลางกรอบ Equilibrium 50% รอการเลือกข้าง",
                target_zone=f"Demand ({_fmt(plan_a_entry, sym)}) / Supply ({_fmt(plan_b_entry, sym)})",
                invalidation_level=eq,
            ),
            key_drivers=["Equilibrium 50%", "Squeeze Building Energy", "Two-Way Plan Active"],
        )
