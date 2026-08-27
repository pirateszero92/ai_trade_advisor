"""Market-state classification and conservative adaptive trade policy.

This module does not add a fourth trading indicator or contribute points to
confluence.  It classifies the environment from OHLC path/volatility statistics
and the already-approved SMC, Volume Delta and Squeeze outputs, then chooses a
risk/gating policy for that environment.
"""

from __future__ import annotations

import math
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from app.core.strategy_config_store import (
    STRATEGY_CONFIG_LOCK,
    read_strategy_config,
    update_strategy_section,
)


STRATEGY_FILE = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"
_CACHE: dict[str, Any] | None = None
_CACHE_MTIME_NS: int | None = None

REGIME_LABELS = {
    "trending": "Trending",
    "ranging": "Ranging",
    "volatile": "High Volatility",
    "compression": "Compression",
    "unknown": "Unknown / Insufficient Data",
}

DEFAULT_REGIME_POLICY: dict[str, Any] = {
    "version": 1,
    "classification": {
        "minimum_bars": 60,
        "atr_length": 14,
        "efficiency_lookback": 30,
        "volatility_lookback": 80,
        "trend_efficiency_min": 0.38,
        "volatile_atr_ratio": 1.65,
        "volatile_percentile": 80.0,
    },
    "policies": {
        "trending": {
            "entry_allowed": True,
            "min_confluence": 65.0,
            "min_rr": 2.0,
            "risk_multiplier": 1.0,
            "require_direction_alignment": True,
            "require_liquidity_sweep": False,
            "require_volume_confirmation": False,
            "require_squeeze_fire": False,
        },
        "ranging": {
            "entry_allowed": True,
            "min_confluence": 75.0,
            "min_rr": 2.0,
            "risk_multiplier": 0.65,
            "require_direction_alignment": False,
            "require_liquidity_sweep": True,
            "require_volume_confirmation": False,
            "require_squeeze_fire": False,
        },
        "volatile": {
            "entry_allowed": True,
            "min_confluence": 82.0,
            "min_rr": 2.5,
            "risk_multiplier": 0.40,
            "require_direction_alignment": True,
            "require_liquidity_sweep": False,
            "require_volume_confirmation": True,
            "require_squeeze_fire": False,
        },
        "compression": {
            "entry_allowed": False,
            "min_confluence": 85.0,
            "min_rr": 2.5,
            "risk_multiplier": 0.0,
            "require_direction_alignment": True,
            "require_liquidity_sweep": False,
            "require_volume_confirmation": False,
            "require_squeeze_fire": True,
        },
        "unknown": {
            "entry_allowed": False,
            "min_confluence": 100.0,
            "min_rr": 3.0,
            "risk_multiplier": 0.0,
            "require_direction_alignment": True,
            "require_liquidity_sweep": False,
            "require_volume_confirmation": False,
            "require_squeeze_fire": False,
        },
    },
}


def _number(
    value: Any,
    *,
    name: str,
    minimum: float,
    maximum: float,
    integer: bool = False,
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return int(numeric) if integer else numeric


def validate_regime_policy_config(raw: Any) -> dict[str, Any]:
    """Return a canonical, fail-closed regime policy configuration."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("regime_policy must be an object")
    unknown = set(raw) - {"version", "classification", "policies"}
    if unknown:
        raise ValueError(f"Unknown regime_policy fields: {sorted(unknown)}")
    if raw.get("version", 1) != 1:
        raise ValueError("regime_policy.version must be 1")

    defaults = DEFAULT_REGIME_POLICY
    classification_raw = raw.get("classification", {})
    if not isinstance(classification_raw, dict):
        raise ValueError("regime_policy.classification must be an object")
    class_defaults = defaults["classification"]
    unknown_classification = set(classification_raw) - set(class_defaults)
    if unknown_classification:
        raise ValueError(
            f"Unknown classification fields: {sorted(unknown_classification)}"
        )
    merged_classification = {**class_defaults, **classification_raw}
    classification = {
        "minimum_bars": _number(
            merged_classification["minimum_bars"],
            name="minimum_bars", minimum=30, maximum=1500, integer=True,
        ),
        "atr_length": _number(
            merged_classification["atr_length"],
            name="atr_length", minimum=5, maximum=100, integer=True,
        ),
        "efficiency_lookback": _number(
            merged_classification["efficiency_lookback"],
            name="efficiency_lookback", minimum=10, maximum=500, integer=True,
        ),
        "volatility_lookback": _number(
            merged_classification["volatility_lookback"],
            name="volatility_lookback", minimum=20, maximum=1000, integer=True,
        ),
        "trend_efficiency_min": _number(
            merged_classification["trend_efficiency_min"],
            name="trend_efficiency_min", minimum=0.05, maximum=0.95,
        ),
        "volatile_atr_ratio": _number(
            merged_classification["volatile_atr_ratio"],
            name="volatile_atr_ratio", minimum=1.0, maximum=10.0,
        ),
        "volatile_percentile": _number(
            merged_classification["volatile_percentile"],
            name="volatile_percentile", minimum=50.0, maximum=100.0,
        ),
    }
    if classification["minimum_bars"] <= classification["atr_length"]:
        raise ValueError("minimum_bars must be greater than atr_length")
    if classification["minimum_bars"] <= classification["efficiency_lookback"]:
        raise ValueError("minimum_bars must be greater than efficiency_lookback")

    policies_raw = raw.get("policies", {})
    if not isinstance(policies_raw, dict):
        raise ValueError("regime_policy.policies must be an object")
    unknown_regimes = set(policies_raw) - set(REGIME_LABELS)
    if unknown_regimes:
        raise ValueError(f"Unknown market regimes: {sorted(unknown_regimes)}")
    policies: dict[str, dict[str, Any]] = {}
    allowed_fields = set(next(iter(defaults["policies"].values())))
    for regime in REGIME_LABELS:
        supplied = policies_raw.get(regime, {})
        if not isinstance(supplied, dict):
            raise ValueError(f"{regime} policy must be an object")
        unknown_fields = set(supplied) - allowed_fields
        if unknown_fields:
            raise ValueError(f"Unknown {regime} policy fields: {sorted(unknown_fields)}")
        merged = {**defaults["policies"][regime], **supplied}
        for flag in (
            "entry_allowed", "require_direction_alignment",
            "require_liquidity_sweep", "require_volume_confirmation",
            "require_squeeze_fire",
        ):
            if not isinstance(merged[flag], bool):
                raise ValueError(f"{regime}.{flag} must be boolean")
        risk_multiplier = _number(
            merged["risk_multiplier"], name=f"{regime}.risk_multiplier",
            minimum=0.0, maximum=1.0,
        )
        if not merged["entry_allowed"] and risk_multiplier != 0:
            raise ValueError(f"{regime}.risk_multiplier must be 0 when entry is blocked")
        if merged["entry_allowed"] and risk_multiplier <= 0:
            raise ValueError(f"{regime}.risk_multiplier must be positive when entry is allowed")
        policies[regime] = {
            "entry_allowed": merged["entry_allowed"],
            "min_confluence": _number(
                merged["min_confluence"], name=f"{regime}.min_confluence",
                minimum=0.0, maximum=100.0,
            ),
            "min_rr": _number(
                merged["min_rr"], name=f"{regime}.min_rr",
                minimum=1.0, maximum=20.0,
            ),
            "risk_multiplier": risk_multiplier,
            "require_direction_alignment": merged["require_direction_alignment"],
            "require_liquidity_sweep": merged["require_liquidity_sweep"],
            "require_volume_confirmation": merged["require_volume_confirmation"],
            "require_squeeze_fire": merged["require_squeeze_fire"],
        }
    return {"version": 1, "classification": classification, "policies": policies}


def load_regime_policy_config() -> dict[str, Any]:
    global _CACHE, _CACHE_MTIME_NS
    with STRATEGY_CONFIG_LOCK:
        try:
            mtime = STRATEGY_FILE.stat().st_mtime_ns if STRATEGY_FILE.exists() else None
        except OSError:
            mtime = None
        if _CACHE is not None and mtime == _CACHE_MTIME_NS:
            return deepcopy(_CACHE)
        try:
            strategy = read_strategy_config(STRATEGY_FILE)
            loaded = validate_regime_policy_config(strategy.get("regime_policy"))
        except Exception as exc:
            logger.warning("Invalid regime_policy configuration; using defaults: {}", exc)
            loaded = validate_regime_policy_config(DEFAULT_REGIME_POLICY)
        _CACHE = loaded
        _CACHE_MTIME_NS = mtime
        return deepcopy(loaded)


def save_regime_policy_config(raw: Any) -> dict[str, Any]:
    global _CACHE, _CACHE_MTIME_NS
    config = validate_regime_policy_config(raw)
    with STRATEGY_CONFIG_LOCK:
        update_strategy_section(STRATEGY_FILE, "regime_policy", config)
        _CACHE = config
        _CACHE_MTIME_NS = STRATEGY_FILE.stat().st_mtime_ns
    return deepcopy(config)


class MarketRegimeEngine:
    """Classify market state and attach its configured decision policy."""

    def classify(
        self,
        df: pd.DataFrame,
        signal: Any,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        active = validate_regime_policy_config(config or load_regime_policy_config())
        params = active["classification"]
        minimum_bars = int(params["minimum_bars"])
        if df is None or len(df) < minimum_bars:
            return self._result(
                active, "unknown", "neutral", 0.0, False, {},
                [f"Need at least {minimum_bars} bars; received {0 if df is None else len(df)}"],
            )

        required = {"high", "low", "close"}
        if not required.issubset({str(column).lower() for column in df.columns}):
            return self._result(
                active, "unknown", "neutral", 0.0, False, {},
                ["High, low and close data are required"],
            )

        frame = df.copy()
        frame.columns = [str(column).lower() for column in frame.columns]
        values = frame[["high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
        if not np.isfinite(values.to_numpy()).all() or (values["close"] <= 0).any():
            return self._result(
                active, "unknown", "neutral", 0.0, False, {},
                ["Market-state input contains invalid prices"],
            )

        high, low, close = values["high"], values["low"], values["close"]
        previous_close = close.shift(1)
        true_range = pd.concat(
            [(high - low).abs(), (high - previous_close).abs(), (low - previous_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = true_range.rolling(int(params["atr_length"]), min_periods=int(params["atr_length"])).mean()
        normalized_atr = (atr / close).replace([np.inf, -np.inf], np.nan).dropna()
        if normalized_atr.empty:
            return self._result(
                active, "unknown", "neutral", 0.0, False, {},
                ["Unable to calculate stable volatility statistics"],
            )

        volatility_window = normalized_atr.tail(int(params["volatility_lookback"]))
        current_atr_pct = float(normalized_atr.iloc[-1] * 100.0)
        baseline = float(volatility_window.median())
        current_normalized_atr = float(normalized_atr.iloc[-1])
        atr_ratio = current_normalized_atr / baseline if baseline > 0 else 1.0
        volatility_percentile = float(
            (volatility_window <= current_normalized_atr).mean() * 100.0
        )

        efficiency_lookback = int(params["efficiency_lookback"])
        path = close.tail(efficiency_lookback + 1)
        net_change = float(path.iloc[-1] - path.iloc[0])
        travelled = float(path.diff().abs().sum())
        efficiency = abs(net_change) / travelled if travelled > 0 else 0.0
        path_direction = "bullish" if net_change > 0 else "bearish" if net_change < 0 else "neutral"

        votes = [path_direction]
        for value in (getattr(signal, "bias", "neutral"), getattr(signal, "htf_bias", "neutral")):
            if value in ("bullish", "bearish"):
                votes.append(value)
        if getattr(signal, "squeeze_data_valid", False):
            momentum = float(getattr(signal, "squeeze_momentum", 0.0))
            if momentum > 0:
                votes.append("bullish")
            elif momentum < 0:
                votes.append("bearish")
        bullish_votes = votes.count("bullish")
        bearish_votes = votes.count("bearish")
        direction = "bullish" if bullish_votes > bearish_votes else "bearish" if bearish_votes > bullish_votes else path_direction
        directional_votes = max(bullish_votes, bearish_votes)
        alignment = directional_votes / max(1, bullish_votes + bearish_votes)

        evidence = [
            f"Path efficiency {efficiency:.2f}",
            f"ATR {current_atr_pct:.2f}% ({atr_ratio:.2f}x baseline)",
            f"Volatility percentile {volatility_percentile:.0f}",
            f"Directional evidence {direction} ({directional_votes}/{max(1, bullish_votes + bearish_votes)} votes)",
        ]

        squeeze_on = (
            getattr(signal, "squeeze_data_valid", False)
            and getattr(signal, "squeeze_status", "no_squeeze") == "squeeze_on"
        )
        is_volatile = (
            atr_ratio >= float(params["volatile_atr_ratio"])
            or volatility_percentile >= float(params["volatile_percentile"])
        )
        is_trending = (
            efficiency >= float(params["trend_efficiency_min"])
            and directional_votes >= 2
            and alignment >= 0.60
        )

        if squeeze_on and not is_volatile:
            regime = "compression"
            confidence = min(99.0, 65.0 + (1.0 - min(atr_ratio, 1.0)) * 25.0)
            evidence.append("Existing Squeeze layer reports active compression")
        elif is_volatile:
            regime = "volatile"
            ratio_score = min(1.0, atr_ratio / float(params["volatile_atr_ratio"]))
            percentile_score = volatility_percentile / 100.0
            confidence = min(99.0, max(ratio_score, percentile_score) * 100.0)
            evidence.append("Current volatility exceeds the configured expansion boundary")
        elif is_trending:
            regime = "trending"
            confidence = min(99.0, (efficiency * 0.65 + alignment * 0.35) * 100.0)
            evidence.append("Price-path efficiency and directional evidence are aligned")
        else:
            regime = "ranging"
            confidence = min(95.0, max(50.0, (1.0 - efficiency) * 80.0 + (1.0 - alignment) * 20.0))
            evidence.append("Directional efficiency is below the trend boundary")

        metrics = {
            "atr_pct": round(current_atr_pct, 4),
            "atr_ratio": round(atr_ratio, 4),
            "volatility_percentile": round(volatility_percentile, 1),
            "path_efficiency": round(efficiency, 4),
            "directional_alignment": round(alignment, 4),
        }
        return self._result(active, regime, direction, confidence, True, metrics, evidence)

    @staticmethod
    def _result(
        config: dict[str, Any],
        regime: str,
        direction: str,
        confidence: float,
        ready: bool,
        metrics: dict[str, Any],
        evidence: list[str],
    ) -> dict[str, Any]:
        policy = deepcopy(config["policies"][regime])
        return {
            "version": int(config["version"]),
            "regime": regime,
            "label": REGIME_LABELS[regime],
            "direction": direction,
            "confidence": round(max(0.0, min(confidence, 100.0))),
            "ready": ready,
            "metrics": metrics,
            "evidence": evidence,
            "policy": policy,
        }
