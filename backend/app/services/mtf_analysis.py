"""Phase 5 hierarchical 4H Bias -> 1H Setup -> 15m Trigger analysis.

Timeframes are evaluated as ordered gates.  Scores are never averaged across
roles: an execution trigger cannot overrule an opposite or unavailable parent
structure, and AI remains downstream of the final deterministic gate.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from app.core.strategy_config_store import read_strategy_config
from app.engines.indicator_core import load_indicator_core_config
from app.engines.market_data import MarketDataEngine
from app.engines.regime_engine import load_regime_policy_config
from app.engines.smc_engine import SMCEngine, SMCSignal
from app.engines.strategy_engine import StrategyEngine, StrategyResult
from app.engines.timeframe_profiles import (
    PROFILE_ROLES,
    load_timeframe_profiles,
    resolve_profile_indicator_config,
    validate_timeframe_profiles,
)
from app.services.analysis_snapshot import _frame_digest, _next_refresh_at


STRATEGY_FILE = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"
StageStatus = Literal["ready", "watch", "blocked"]
TradeDirection = Literal["long", "short", "wait"]


def _config_digest(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20]


def mtf_snapshot_id(
    *,
    frames: dict[str, pd.DataFrame],
    symbol: str,
    market_type: str,
    exchange: str,
    entry_mode: str,
    config_snapshot: dict[str, Any],
) -> str:
    """Fingerprint the exact three windows plus decision configuration."""
    key = (
        symbol.upper(), market_type.lower(), exchange.lower(), entry_mode,
        _config_digest(config_snapshot),
    )
    digest = hashlib.sha256()
    digest.update("|".join(key).encode("utf-8"))
    for role in PROFILE_ROLES:
        digest.update(role.encode("utf-8"))
        digest.update(_frame_digest(frames[role]))
    return digest.hexdigest()[:24]


def _trade_direction(bias: str) -> TradeDirection:
    if bias == "bullish":
        return "long"
    if bias == "bearish":
        return "short"
    return "wait"


def _bias_for(direction: str) -> str:
    return "bullish" if direction == "long" else "bearish" if direction == "short" else "neutral"


def _matching_zone(signal: SMCSignal, direction: str) -> bool:
    expected = _bias_for(direction)
    return bool(signal.order_block and signal.order_block.direction == expected)


def _directional_triggers(signal: SMCSignal, direction: str) -> list[str]:
    """Return only events that are directionally valid for this candidate.

    BOS and CHoCH are accepted when the trigger-timeframe bias aligns with or
    is neutral to the trade direction.  An explicit opposite-bias structure
    (e.g. bearish BOS on a long setup) is filtered out by the upstream gate;
    this function should not duplicate that check by requiring a strict match.
    Liquidity sweeps remain directional (low sweep for longs, high for shorts).
    """
    expected_bias = _bias_for(direction)
    opposite_bias = "bearish" if expected_bias == "bullish" else "bullish"
    events: list[str] = []
    # Accept BOS/CHoCH when bias matches OR is neutral (not when it opposes the direction)
    if signal.bias != opposite_bias:
        if signal.bos:
            events.append("bos")
        if signal.choch:
            events.append("choch")
    if signal.liquidity_swept and (
        (direction == "long" and signal.sweep_direction == "low")
        or (direction == "short" and signal.sweep_direction == "high")
    ):
        events.append("liquidity_sweep")
    if signal.squeeze_status == "squeeze_fire" and (
        (direction == "long" and signal.momentum_direction == "accelerating_up" and signal.squeeze_momentum > 0)
        or (direction == "short" and signal.momentum_direction == "accelerating_down" and signal.squeeze_momentum < 0)
    ):
        events.append("squeeze_fire")
    return events



@dataclass
class TimeframeStage:
    role: str
    timeframe: str
    status: StageStatus
    direction: TradeDirection
    signal: SMCSignal = field(repr=False)
    checks: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    trigger_evidence: list[str] = field(default_factory=list)
    last_closed_candle: str = ""

    def to_dict(self) -> dict[str, Any]:
        labels = {
            "bias": "Market Bias",
            "setup": "SMC Setup",
            "trigger": "Entry Trigger",
        }
        return {
            "role": self.role,
            "label": labels.get(self.role, self.role.title()),
            "timeframe": self.timeframe,
            "status": self.status,
            "direction": self.direction,
            "bias": self.signal.bias,
            "bias_source": self.signal.structure_bias_source,
            "confluence": self.signal.confluence_score,
            "checks": list(self.checks),
            "reasons": list(self.reasons),
            "trigger_evidence": list(self.trigger_evidence),
            "last_closed_candle": self.last_closed_candle,
            "indicator_decision": deepcopy(self.signal.indicator_decision),
            "market_regime": deepcopy(self.signal.market_regime),
            "order_block": self.signal.to_dict().get("order_block"),
            "fvg": self.signal.to_dict().get("fvg"),
            "liquidity_swept": self.signal.liquidity_swept,
            "delta_absorption": self.signal.delta_absorption,
            "delta_status": self.signal.delta_status,
            "bos": self.signal.bos,
            "choch": self.signal.choch,
        }


@dataclass
class MTFAnalysis:
    symbol: str
    status: StageStatus
    direction: TradeDirection
    actionable: bool
    stages: dict[str, TimeframeStage]
    strategy: StrategyResult
    profile: dict[str, Any]
    config_snapshot: dict[str, Any] = field(default_factory=dict, repr=False)
    snapshot_id: str = ""
    generated_at: datetime | None = None
    valid_until: datetime | None = None
    frames: dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)

    @property
    def trigger_signal(self) -> SMCSignal:
        return self.stages["trigger"].signal

    def decision_dict(self) -> dict[str, Any]:
        """Deterministic portion safe for evidence hashing and replay."""
        matrix = {
            stage.timeframe: stage.to_dict()
            for stage in (self.stages[role] for role in PROFILE_ROLES)
        }
        ready_count = sum(stage.status == "ready" for stage in self.stages.values())
        if self.actionable:
            grade = "SUPREME GRADE A+"
        elif ready_count >= 2:
            grade = "GRADE A · WATCH TRIGGER"
        elif ready_count == 1:
            grade = "GRADE B · WATCH SETUP"
        else:
            grade = "WAIT · MTF BLOCKED"
        direction_label = self.direction.upper()
        if self.actionable:
            summary = f"{direction_label} ผ่าน 4H Bias, 1H Setup และ 15m Trigger ครบทุกชั้น"
        elif self.status == "watch":
            summary = f"{direction_label} อยู่ในสถานะ WATCH; รอชั้นถัดไปหรือ Execution Gate ยืนยัน"
        else:
            summary = "MTF hierarchy ยังไม่มีทิศทางที่สอดคล้องครบทุกชั้น"
        trigger = self.trigger_signal
        return {
            "version": int(self.profile["version"]),
            "enabled": bool(self.profile["enabled"]),
            "snapshot_id": self.snapshot_id,
            "status": self.status,
            "direction": self.direction,
            "aligned_bias": _bias_for(self.direction).upper(),
            "actionable": self.actionable,
            "alignment_count": ready_count,
            "total_timeframes": len(PROFILE_ROLES),
            "grade_badge": grade,
            "summary_th": summary,
            "role_order": list(PROFILE_ROLES),
            "matrix": matrix,
            "stages": {role: self.stages[role].to_dict() for role in PROFILE_ROLES},
            "strategy": self.strategy.to_dict(),
            "execution": {
                "timeframe": self.stages["trigger"].timeframe,
                "entry": trigger.entry,
                "stop_loss": trigger.stop_loss,
                "take_profit": trigger.take_profit,
                "risk_reward": trigger.risk_reward,
                "current_price": trigger.current_price,
                "entry_type": trigger.entry_type,
            },
            "absorption_found": any(
                stage.signal.delta_absorption for stage in self.stages.values()
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.decision_dict()
        payload["generated_at"] = self.generated_at.isoformat() if self.generated_at else None
        payload["valid_until"] = self.valid_until.isoformat() if self.valid_until else None
        return payload

    def metadata(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "source": "shared_mtf_closed_candle_snapshot",
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "candle_policy": "closed_only",
            "roles": {
                role: {
                    "timeframe": stage.timeframe,
                    "last_closed_candle": stage.last_closed_candle,
                    "lookback": int(self.profile["roles"][role]["lookback"]),
                    "candles": len(self.frames.get(role, [])),
                }
                for role, stage in self.stages.items()
            },
        }


def _stage_base_checks(
    *, signal: SMCSignal, profile: dict[str, Any], direction: str
) -> tuple[list[str], list[str]]:
    checks: list[str] = []
    reasons: list[str] = []
    gate = profile["gate"]
    indicator = signal.indicator_decision or {}
    if gate["require_indicator_readiness"] and not indicator.get("ready", False):
        reasons.append("Indicator data is not ready for this timeframe role")
    else:
        checks.append("Indicator data readiness passed")
    if signal.confluence_score < float(gate["min_confluence"]):
        reasons.append(
            f"Confluence {signal.confluence_score} < profile minimum {gate['min_confluence']}"
        )
    else:
        checks.append(
            f"Confluence {signal.confluence_score}/{gate['min_confluence']} passed"
        )
    if gate["require_order_block"] and not _matching_zone(signal, direction):
        reasons.append(f"Direction-aligned {_bias_for(direction)} Order Block is required")
    elif _matching_zone(signal, direction):
        checks.append("Direction-aligned Order Block found")
    return checks, reasons


def analyze_mtf_frames(
    *,
    frames: dict[str, pd.DataFrame],
    symbol: str,
    entry_mode: str,
    config_snapshot: dict[str, Any],
    snapshot_id: str = "",
) -> MTFAnalysis:
    """Evaluate already captured frames without external I/O (replay-safe)."""
    profiles = validate_timeframe_profiles(config_snapshot.get("timeframe_profiles"))
    if not profiles["enabled"]:
        raise ValueError("Phase 5 timeframe profiles are disabled")
    missing = set(PROFILE_ROLES) - set(frames)
    if missing or any(frames.get(role) is None or frames[role].empty for role in PROFILE_ROLES):
        raise ValueError(f"Missing MTF market windows: {sorted(missing)}")

    base_indicator = config_snapshot.get("indicator_core") or load_indicator_core_config()
    regime_config = config_snapshot.get("regime_policy") or load_regime_policy_config()
    signals: dict[str, SMCSignal] = {}
    parent_bias = "neutral"
    for role in PROFILE_ROLES:
        role_profile = profiles["roles"][role]
        engine = SMCEngine(**role_profile["smc"])
        indicator_config = resolve_profile_indicator_config(role_profile, base_indicator)
        signal = engine.analyze(
            frames[role].copy(),
            symbol,
            role_profile["timeframe"],
            htf_bias=parent_bias,
            entry_mode=entry_mode,
            indicator_config=indicator_config,
            regime_config=regime_config,
        )
        signals[role] = signal
        if role == "bias":
            parent_bias = signal.bias

    stages: dict[str, TimeframeStage] = {}
    bias_signal = signals["bias"]
    bias_profile = profiles["roles"]["bias"]
    direction = _trade_direction(bias_signal.bias)
    bias_checks, bias_reasons = _stage_base_checks(
        signal=bias_signal, profile=bias_profile, direction=direction
    )
    if direction == "wait":
        bias_reasons.append("4H market structure is neutral")
    else:
        source_label = (
            "fresh BOS/CHoCH"
            if bias_signal.structure_bias_source == "fresh_structure_break"
            else "confirmed HH/HL or LH/LL structure"
        )
        bias_checks.append(
            f"4H market bias authorizes {direction.upper()} only ({source_label})"
        )
    stages["bias"] = TimeframeStage(
        role="bias",
        timeframe=bias_profile["timeframe"],
        status="ready" if not bias_reasons else "blocked",
        direction=direction,
        signal=bias_signal,
        checks=bias_checks,
        reasons=bias_reasons,
        last_closed_candle=pd.Timestamp(frames["bias"].index[-1]).isoformat(),
    )

    setup_signal = signals["setup"]
    setup_profile = profiles["roles"]["setup"]
    setup_checks: list[str] = []
    setup_reasons: list[str] = []
    setup_blocked = stages["bias"].status != "ready"
    if setup_blocked:
        setup_reasons.append("4H Bias gate is not ready")
    else:
        expected_bias = _bias_for(direction)
        opposite_bias = "bearish" if expected_bias == "bullish" else "bullish"
        if setup_signal.bias == opposite_bias:
            setup_reasons.append(
                f"1H structure is {opposite_bias} against 4H {expected_bias} bias"
            )
            setup_blocked = True
        elif setup_signal.bias == "neutral" and not setup_profile["gate"]["allow_neutral_structure"]:
            setup_reasons.append("1H neutral structure is not allowed by the setup profile")
            setup_blocked = True
        else:
            setup_checks.append("1H structure does not oppose 4H Bias")
        checks, reasons = _stage_base_checks(
            signal=setup_signal, profile=setup_profile, direction=direction
        )
        setup_checks.extend(checks)
        setup_reasons.extend(reasons)
    setup_status: StageStatus = (
        "blocked" if setup_blocked else "ready" if not setup_reasons else "watch"
    )
    stages["setup"] = TimeframeStage(
        role="setup",
        timeframe=setup_profile["timeframe"],
        status=setup_status,
        direction=direction if not setup_blocked else "wait",
        signal=setup_signal,
        checks=setup_checks,
        reasons=setup_reasons,
        last_closed_candle=pd.Timestamp(frames["setup"].index[-1]).isoformat(),
    )

    trigger_signal = signals["trigger"]
    trigger_profile = profiles["roles"]["trigger"]
    trigger_checks: list[str] = []
    trigger_reasons: list[str] = []
    trigger_events: list[str] = []
    trigger_blocked = setup_status == "blocked"
    if setup_status != "ready":
        trigger_reasons.append("1H Setup gate is not ready")
        trigger_blocked = setup_status == "blocked"
    else:
        expected_bias = _bias_for(direction)
        opposite_bias = "bearish" if expected_bias == "bullish" else "bullish"
        if trigger_signal.bias == opposite_bias:
            trigger_reasons.append(
                f"15m structure is {opposite_bias} against the authorized {expected_bias} setup"
            )
            trigger_blocked = True
        elif trigger_signal.bias == "neutral" and not trigger_profile["gate"]["allow_neutral_structure"]:
            trigger_reasons.append("15m neutral structure is not allowed by the trigger profile")
            trigger_blocked = True
        else:
            trigger_checks.append("15m structure does not oppose the 1H Setup")
        checks, reasons = _stage_base_checks(
            signal=trigger_signal, profile=trigger_profile, direction=direction
        )
        trigger_checks.extend(checks)
        trigger_reasons.extend(reasons)
        trigger_events = _directional_triggers(trigger_signal, direction)
        required_events = trigger_profile["gate"]["require_any_trigger"]
        if required_events and not set(required_events).intersection(trigger_events):
            trigger_reasons.append(
                "Waiting for direction-aligned 15m BOS, CHoCH, liquidity sweep or squeeze release"
            )
        elif required_events:
            trigger_checks.append(f"15m trigger fired: {', '.join(trigger_events)}")
    trigger_status: StageStatus = (
        "blocked" if trigger_blocked else "ready" if not trigger_reasons else "watch"
    )
    stages["trigger"] = TimeframeStage(
        role="trigger",
        timeframe=trigger_profile["timeframe"],
        status=trigger_status,
        direction=direction if not trigger_blocked else "wait",
        signal=trigger_signal,
        checks=trigger_checks,
        reasons=trigger_reasons,
        trigger_evidence=trigger_events,
        last_closed_candle=pd.Timestamp(frames["trigger"].index[-1]).isoformat(),
    )

    strategy = StrategyEngine(strategy_config=config_snapshot).evaluate(trigger_signal)
    if strategy.effective_policy:
        trigger_signal.market_regime["effective_policy"] = strategy.effective_policy
    all_roles_ready = all(stage.status == "ready" for stage in stages.values())
    if not all_roles_ready:
        strategy.approved = False
        strategy.direction = "wait"
        strategy.setup_direction = direction
        for role in PROFILE_ROLES:
            stage = stages[role]
            if stage.status != "ready":
                for reason in stage.reasons:
                    tagged = f"MTF {stage.timeframe} {stage.role}: {reason}"
                    if tagged not in strategy.rejection_reasons:
                        strategy.rejection_reasons.append(tagged)

    actionable = all_roles_ready and strategy.approved
    if actionable:
        overall_status: StageStatus = "ready"
    elif any(stage.status == "blocked" for stage in stages.values()):
        overall_status = "blocked"
    else:
        overall_status = "watch"
    return MTFAnalysis(
        symbol=symbol,
        status=overall_status,
        direction=direction if direction in {"long", "short"} else "wait",
        actionable=actionable,
        stages=stages,
        strategy=strategy,
        profile=profiles,
        config_snapshot=deepcopy(config_snapshot),
        snapshot_id=snapshot_id,
        frames={role: frames[role].copy() for role in PROFILE_ROLES},
    )


class MTFAnalysisService:
    """Fetch, evaluate and cache one canonical three-role decision per 15m close."""

    def __init__(self) -> None:
        self._market = MarketDataEngine()
        self._cache: dict[tuple[str, ...], MTFAnalysis] = {}
        self._locks: dict[tuple[str, ...], asyncio.Lock] = {}

    def clear(self) -> None:
        self._cache.clear()

    @staticmethod
    def _config_snapshot() -> dict[str, Any]:
        config = read_strategy_config(STRATEGY_FILE)
        config["indicator_core"] = load_indicator_core_config()
        config["regime_policy"] = load_regime_policy_config()
        config["timeframe_profiles"] = load_timeframe_profiles()
        return config

    async def get(
        self,
        *,
        symbol: str,
        market_type: str,
        exchange: str,
        entry_mode: str,
    ) -> MTFAnalysis:
        config = self._config_snapshot()
        profiles = config["timeframe_profiles"]
        config_hash = _config_digest(config)
        key = (symbol.upper(), market_type.lower(), exchange.lower(), entry_mode, config_hash)
        now = datetime.now(timezone.utc)
        cached = self._cache.get(key)
        if cached and cached.valid_until and now < cached.valid_until:
            return cached

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = datetime.now(timezone.utc)
            cached = self._cache.get(key)
            if cached and cached.valid_until and now < cached.valid_until:
                return cached
            calls = [
                self._market.get_ohlcv(
                    symbol,
                    profiles["roles"][role]["timeframe"],
                    market_type,
                    exchange,
                    limit=int(profiles["roles"][role]["lookback"]),
                    closed_only=True,
                )
                for role in PROFILE_ROLES
            ]
            fetched = await asyncio.gather(*calls)
            frames = dict(zip(PROFILE_ROLES, fetched))
            for role, frame in frames.items():
                if frame.empty:
                    raise ValueError(f"No closed {role} candles available")

            snapshot_id = mtf_snapshot_id(
                frames=frames,
                symbol=symbol,
                market_type=market_type,
                exchange=exchange,
                entry_mode=entry_mode,
                config_snapshot=config,
            )
            analysis = analyze_mtf_frames(
                frames=frames,
                symbol=symbol,
                entry_mode=entry_mode,
                config_snapshot=config,
                snapshot_id=snapshot_id,
            )
            trigger_profile = profiles["roles"]["trigger"]
            analysis.generated_at = now
            analysis.valid_until = _next_refresh_at(
                frames["trigger"].index[-1], trigger_profile["timeframe"]
            )
            self._cache[key] = analysis
            if len(self._cache) > 500:
                self._cache.pop(next(iter(self._cache)), None)
            return analysis


mtf_analyses = MTFAnalysisService()
