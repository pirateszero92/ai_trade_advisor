"""Advisory-only AI review for deterministic single-timeframe Tri-Core setups."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.core.json_store import write_json
from app.engines.ai_engine import AIEngine, SINGLE_TIMEFRAME_DIRECTIVE
PROMPT = """You are an Institutional Senior Day Trader & Scalp Execution Officer (หัวหน้าเทรดเดอร์ Day Trade & Scalp ประจำกองทุน).
You evaluate every setup with the strict discipline of an intraday scalper. Your verdict determines whether to APPROVE (COHERENT) or VETO (CONFLICT) this trade.

DAY TRADE & SCALP EXECUTION DISCIPLINE:
1. ORDER FLOW & AGGRESSOR DELTA DISCIPLINE (ABSOLUTE RULE FOR SCALPERS):
   - For a SHORT scalp: You MUST see negative delta momentum OR proven bearish absorption at resistance (aggressive buyers absorbed by passive sell orders). NEVER short when taker buyers are actively buying (+Delta) unless bearish absorption is explicitly confirmed. If CVD delta is positive and growing without absorption, VETO the short scalp immediately!
   - For a LONG scalp: You MUST see positive delta momentum OR proven bullish absorption at support (aggressive sellers absorbed by passive buy orders). NEVER long when taker sellers are actively dumping (-Delta) unless bullish absorption is explicitly confirmed.
   - Absorption definitions:
     * "bullish_absorption": Aggressive sellers sell into passive limit buy orders at support. Delta is NEGATIVE, but price holds/reclaims. Standard absorption, NOT a contradiction.
     * "bearish_absorption": Aggressive buyers buy into passive limit sell orders at resistance. Delta is POSITIVE, but price fails to push higher. Standard absorption, NOT a contradiction.
2. "sweep_reversal" setup triggers when price sweeps an opposing liquidity extreme (prior swing low/high or inducement) and closes back across the level (reclaim) with CVD flow confirmation:
   - A LONG sweep_reversal sweeps a low (sell-side liquidity / SSL / swing low) and reclaims it. This is fully valid and coherent BOTH in a bullish trend (buying the pullback/dip after sweeping liquidity) and in a bearish/ranging market (reversing swept liquidity). Direction "long" with structure.bias "bullish" is standard trend-aligned liquidity sweep entry, NOT a contradiction.
   - A SHORT sweep_reversal sweeps a high (buy-side liquidity / BSL / swing high) and reclaims it downwards. Trend-aligned sweep reversals are prime high-probability institutional scalp setups.
3. TREND & INTRADAY CONTEXT:
   - On strong bullish trend days (regime == "trending" with "bullish" direction): Focus on Long pullbacks/retests. Strongly VETO counter-trend shorts unless there is an undisputable structural breakdown with negative flow.
   - In ranging regimes: Boundary scalps (Short at Supply, Long at Demand) are valid if entered near the boundary, NOT mid-range.
4. "displacement_retest" setup is a trend-continuation scalp where price returns to a fresh Order Block or FVG following institutional displacement.
5. In cvd.divergence_evidence: "sweep_extreme_price" / "reference_extreme_price" refers to the wick extreme of the swept candle, while "current_price" / "reclaim_close_price" refers to the closing level where the reclaim occurred. The natural difference is standard candle anatomy, NOT a data discrepancy.
6. S1-S9 scenario analytics, HMM regime, and SQZ are secondary contextual descriptors that do NOT override or contradict a valid setup.
7. Do not claim to have checked news because no news feed is supplied.
8. Explain clearly, decisively and authoritatively in concise Thai (ภาษาไทย).

Return ONLY one JSON object, no markdown, using exactly these keys:
verdict (COHERENT/CONFLICT/UNAVAILABLE),
execution_action (ENTER_NOW/LIMIT_RETEST/VETO_BLOCKED),
scalper_bias (BULLISH_SCALP/BEARISH_SCALP/NO_TRADE),
reason (string in Thai ภาษาไทย),
conflicts (array of strings in Thai),
management_note (string or null in Thai),
evidence (array of strings in Thai).
"""


class Proposal(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=False)
    verdict: Literal["COHERENT", "CONFLICT", "UNAVAILABLE"]
    execution_action: Literal["ENTER_NOW", "LIMIT_RETEST", "VETO_BLOCKED"] = "ENTER_NOW"
    scalper_bias: Literal["BULLISH_SCALP", "BEARISH_SCALP", "NO_TRADE"] = "NO_TRADE"
    reason: str = Field(min_length=1, max_length=1600)
    conflicts: list[str] = Field(default_factory=list, max_length=6)
    management_note: str | None = Field(default=None, max_length=600)
    evidence: list[str] = Field(default_factory=list, max_length=8)


def build_context(analysis):
    signal, frame = analysis.signal, analysis.frame
    close = float(frame["close"].iloc[-1])
    dec = 4 if close < 5.0 else 2
    levels = set()
    zones = []
    swings = [round(float(p.price), dec) for p in [*signal.swing_highs, *signal.swing_lows]
              if p.confirmed_timestamp is not None and 0 <= p.confirmed_index < len(frame)]
    for point in [*signal.swing_highs, *signal.swing_lows]:
        if point.confirmed_timestamp is not None and 0 <= point.confirmed_index < len(frame):
            levels.add(float(point.price))
    for zone in [*signal.order_blocks, *signal.fvgs]:
        if getattr(zone, "mitigated", False):
            continue
        confirmed = getattr(zone, "confirmed_index", None)
        if confirmed is None or confirmed < 0 or confirmed >= len(frame):
            continue
        for price in (zone.bottom, zone.top):
            if math.isfinite(price) and price > 0:
                levels.add(float(price))
        zones.append({
            "type": getattr(zone, "type", "zone"),
            "bottom": round(float(zone.bottom), dec),
            "top": round(float(zone.top), dec),
        })
    # Window extrema are observable now; no future/pivot-confirmation assumption.
    levels.update([float(frame["low"].tail(20).min()), float(frame["high"].tail(20).max())])
    below = sorted(p for p in levels if 0 < p < close)
    above = sorted(p for p in levels if p > close)
    candles = []
    for timestamp, row in frame.tail(15).iterrows():
        t_str = timestamp.strftime("%m-%d %H:%M") if hasattr(timestamp, "strftime") else str(timestamp)
        candles.append([
            t_str,
            round(float(row["open"]), dec),
            round(float(row["high"]), dec),
            round(float(row["low"]), dec),
            round(float(row["close"]), dec),
            round(float(row["volume"]), 1),
        ])
    return {
        "snapshot_id": analysis.snapshot_id,
        "symbol": signal.symbol,
        "timeframe": analysis.timeframe,
        "current_price": round(close, dec),
        "candles_format": "[time, open, high, low, close, volume]",
        "candles": candles,
        "active_zones": zones[-6:],
        "confirmed_swings": swings[-8:],
        "structure": {
            "bias": signal.bias,
            "bos": signal.bos,
            "choch": signal.choch,
            "event_age": signal.structure_event_age,
            "liquidity_swept": signal.liquidity_swept,
            "zone_position": signal.zone_position,
            "sweep_direction": signal.sweep_direction,
        },
        "cvd": {
            "delta": round(float(signal.volume_delta or 0), 1),
            "ratio": round(float(signal.delta_ratio or 0), 3),
            "absorption": signal.delta_absorption,
            "quality": signal.volume_quality,
            "source": signal.flow_source,
            "granularity": signal.flow_granularity,
            "divergence": signal.cvd_divergence,
            "divergence_evidence": signal.cvd_divergence_evidence,
            "series_tail": signal.cvd_tail,
        },
        "core_score": signal.indicator_decision.get("core_score"),
        "squeeze": {
            "status": signal.squeeze_status,
            "momentum": round(float(signal.squeeze_momentum or 0), 3),
            "bonus": signal.indicator_decision.get("squeeze_bonus", 0),
        },
        "legacy_scenario_analytics": {
            "id": signal.scenario.get("scenario_id"),
            "actionable": signal.scenario.get("actionable", False),
        },
        "deterministic_setup": signal.tri_core_setup,
        "setup_context": {
            "setup_type": signal.tri_core_setup.get("setup_type", "no_edge"),
            "trade_nature": (
                "Liquidity Sweep Reversal (reclaims swept liquidity level)"
                if signal.tri_core_setup.get("setup_type") == "sweep_reversal"
                else "Trend-Following Continuation Retest"
            ),
        },
    }


def validate_advisory(proposal, context, analysis):
    signal = analysis.signal
    if signal.tri_core_setup.get("actionable") is not True:
        raise ValueError("AI advisory requires a deterministic Tri-Core setup")
    if not signal.indicator_decision.get("ready") or signal.volume_quality != "exchange_aggressor":
        raise ValueError("SMC/CVD data is unavailable")
    is_approved = proposal.verdict == "COHERENT" and proposal.execution_action != "VETO_BLOCKED"
    execution_action = "VETO_BLOCKED" if proposal.verdict == "CONFLICT" else proposal.execution_action
    return {
        "advisory_only": True,
        "ai_scalper_approved": is_approved,
        "execution_action": execution_action,
        "scalper_bias": proposal.scalper_bias,
        "deterministic_direction": signal.tri_core_setup["direction"],
    }


class ScannerAI:
    def __init__(self):
        self._slots = asyncio.Semaphore(4)

    async def review(self, analysis, mode):
        started = time.monotonic()
        cfg = get_settings()
        engine = AIEngine()
        provider = engine.active_provider
        context = build_context(analysis)
        review = {"status": "error", "mode": mode, "input_snapshot_id": analysis.snapshot_id,
                  "provider": provider, "verdict": "UNAVAILABLE", "input_type": "structured_ohlcv_smc_cvd",
                  "legacy_strategy": analysis.strategy.to_dict(), "prompt_version": "scanner-ai-v1"}
        raw = ""
        try:
            async with self._slots:
                if datetime.now(timezone.utc) >= analysis.valid_until:
                    raise ValueError("Snapshot expired while queued")
                review["model"] = str(getattr(cfg, {"local": "local_llm_model", "gemini": "gemini_model",
                                                   "openrouter": "openrouter_model"}[provider]))
                raw = await asyncio.wait_for(engine._dispatch(provider, [
                    {"role": "system", "content": PROMPT + SINGLE_TIMEFRAME_DIRECTIVE},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False, allow_nan=False)},
                ]), timeout=cfg.scanner_ai_timeout_seconds)
            if engine._contains_cross_timeframe_reference(raw, analysis.timeframe):
                raise ValueError("Response crossed execution timeframe boundary")
            proposal = Proposal.model_validate_json(raw.strip())
            review.update(proposal.model_dump())
            review["status"] = "reviewed"
            review.update(validate_advisory(proposal, context, analysis))
        except Exception as exc:
            logger.warning("Scanner AI unavailable for {}: {}", analysis.symbol, type(exc).__name__)
            review["error"] = "AI advisory unavailable or invalid"
        review["latency_ms"] = round((time.monotonic()-started)*1000)
        review["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        # Audit includes the exact input and legacy result for later paper comparison.
        audit_id = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()[:24]
        try:
            await asyncio.to_thread(write_json, Path(__file__).parents[2]/"data"/"ai_reviews"/(audit_id+".json"),
                                    {"input": context, "review": review, "raw_response": raw[:16000]})
        except Exception:
            review["audit_error"] = True
        result = analysis.clone()
        result.signal.ai_review = deepcopy(review)
        return result
scanner_ai = ScannerAI()
