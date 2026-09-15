"""Configurable three-layer indicator scoring and explainability core.

The registry intentionally contains only the project's three approved signal
families. New indicators can be introduced later by registering another
scorer; weights and enablement do not require changes to the SMC pipeline.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from app.core.strategy_config_store import (
    STRATEGY_CONFIG_LOCK,
    read_strategy_config,
    update_strategy_section,
)


STRATEGY_FILE = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"
_CONFIG_LOCK = STRATEGY_CONFIG_LOCK
_CONFIG_CACHE: dict[str, Any] | None = None
_CONFIG_MTIME_NS: int | None = None

DEFAULT_INDICATOR_CORE: dict[str, Any] = {
    "version": 1,
    "minimum_data_coverage": 70.0,
    "indicators": {
        "smc_structure": {
            "enabled": True,
            "required": True,
            "weight": 40.0,
            "params": {},
        },
        "volume_delta": {
            "enabled": True,
            "required": True,
            "weight": 30.0,
            "params": {
                "absorption_lookback": 10,
                "bullish_absorption_quantile": 0.30,
                "bearish_absorption_quantile": 0.70,
                "volume_spike_multiplier": 1.50,
                "pressure_threshold": 0.20,
            },
        },
        "squeeze_momentum": {
            "enabled": True,
            "required": False,
            "weight": 30.0,
            "params": {
                "bb_length": 20,
                "bb_mult": 2.0,
                "kc_length": 20,
                "kc_mult": 1.5,
            },
        },
    },
}

INDICATOR_METADATA: dict[str, dict[str, str]] = {
    "smc_structure": {
        "label": "SMC Structure",
        "short_label": "SMC",
        "description": "Market structure, location, liquidity and institutional zones",
    },
    "volume_delta": {
        "label": "Volume Delta & CVD",
        "short_label": "CVD",
        "description": "Directional volume pressure, absorption and volume expansion",
    },
    "squeeze_momentum": {
        "label": "Squeeze Momentum",
        "short_label": "SQZ",
        "description": "Compression release and direction-aligned momentum timing",
    },
}


@dataclass
class LayerScore:
    indicator_id: str
    available: bool
    raw_score: float
    max_score: float
    status: str
    evidence: list[str] = field(default_factory=list)


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
    if not minimum <= numeric <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return int(numeric) if integer else numeric


def _validate_params(indicator_id: str, params: Any) -> dict[str, int | float]:
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError(f"{indicator_id}.params must be an object")
    defaults = DEFAULT_INDICATOR_CORE["indicators"][indicator_id]["params"]
    unknown = set(params) - set(defaults)
    if unknown:
        raise ValueError(f"Unknown {indicator_id} parameters: {sorted(unknown)}")
    merged = {**defaults, **params}
    if indicator_id == "volume_delta":
        return {
            "absorption_lookback": _number(
                merged["absorption_lookback"], name="absorption_lookback",
                minimum=3, maximum=100, integer=True,
            ),
            "bullish_absorption_quantile": _number(
                merged["bullish_absorption_quantile"], name="bullish_absorption_quantile",
                minimum=0.05, maximum=0.49,
            ),
            "bearish_absorption_quantile": _number(
                merged["bearish_absorption_quantile"], name="bearish_absorption_quantile",
                minimum=0.51, maximum=0.95,
            ),
            "volume_spike_multiplier": _number(
                merged["volume_spike_multiplier"], name="volume_spike_multiplier",
                minimum=1.0, maximum=10.0,
            ),
            "pressure_threshold": _number(
                merged["pressure_threshold"], name="pressure_threshold",
                minimum=0.01, maximum=1.0,
            ),
        }
    if indicator_id == "squeeze_momentum":
        return {
            "bb_length": _number(
                merged["bb_length"], name="bb_length", minimum=5, maximum=200, integer=True,
            ),
            "bb_mult": _number(
                merged["bb_mult"], name="bb_mult", minimum=0.1, maximum=10.0,
            ),
            "kc_length": _number(
                merged["kc_length"], name="kc_length", minimum=5, maximum=200, integer=True,
            ),
            "kc_mult": _number(
                merged["kc_mult"], name="kc_mult", minimum=0.1, maximum=10.0,
            ),
        }
    return {}


def validate_indicator_core_config(raw: Any) -> dict[str, Any]:
    """Return a canonical, validated indicator-core configuration."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("indicator_core must be an object")
    unknown_top_level = set(raw) - {"version", "minimum_data_coverage", "indicators"}
    if unknown_top_level:
        raise ValueError(f"Unknown indicator_core fields: {sorted(unknown_top_level)}")
    if raw.get("version", 1) != 1:
        raise ValueError("indicator_core.version must be 1")

    requested = raw.get("indicators", {})
    if not isinstance(requested, dict):
        raise ValueError("indicator_core.indicators must be an object")
    unknown_indicators = set(requested) - set(INDICATOR_METADATA)
    if unknown_indicators:
        raise ValueError(f"Unregistered indicators: {sorted(unknown_indicators)}")

    canonical: dict[str, Any] = {
        "version": 1,
        "minimum_data_coverage": _number(
            raw.get("minimum_data_coverage", DEFAULT_INDICATOR_CORE["minimum_data_coverage"]),
            name="minimum_data_coverage", minimum=0, maximum=100,
        ),
        "indicators": {},
    }
    enabled_count = 0
    for indicator_id in INDICATOR_METADATA:
        defaults = DEFAULT_INDICATOR_CORE["indicators"][indicator_id]
        supplied = requested.get(indicator_id, {})
        if not isinstance(supplied, dict):
            raise ValueError(f"{indicator_id} configuration must be an object")
        unknown_fields = set(supplied) - {"enabled", "required", "weight", "params"}
        if unknown_fields:
            raise ValueError(f"Unknown {indicator_id} fields: {sorted(unknown_fields)}")
        enabled = supplied.get("enabled", defaults["enabled"])
        required = supplied.get("required", defaults["required"])
        if not isinstance(enabled, bool) or not isinstance(required, bool):
            raise ValueError(f"{indicator_id}.enabled and required must be boolean")
        if required and not enabled:
            raise ValueError(f"{indicator_id} cannot be required while disabled")
        weight = _number(
            supplied.get("weight", defaults["weight"]),
            name=f"{indicator_id}.weight",
            minimum=0.1 if enabled else 0.0,
            maximum=1000.0,
        )
        if enabled:
            enabled_count += 1
        canonical["indicators"][indicator_id] = {
            "enabled": enabled,
            "required": required,
            "weight": weight,
            "params": _validate_params(indicator_id, supplied.get("params", defaults["params"])),
        }
    if enabled_count == 0:
        raise ValueError("At least one registered indicator must remain enabled")
    return canonical


def load_indicator_core_config() -> dict[str, Any]:
    """Load config with an mtime-aware cache for high-frequency analysis."""
    global _CONFIG_CACHE, _CONFIG_MTIME_NS

    with _CONFIG_LOCK:
        try:
            current_mtime = (
                STRATEGY_FILE.stat().st_mtime_ns if STRATEGY_FILE.exists() else None
            )
        except OSError:
            current_mtime = None
        if _CONFIG_CACHE is not None and current_mtime == _CONFIG_MTIME_NS:
            return deepcopy(_CONFIG_CACHE)

        try:
            if STRATEGY_FILE.exists():
                strategy = read_strategy_config(STRATEGY_FILE)
                loaded = validate_indicator_core_config(strategy.get("indicator_core"))
            else:
                loaded = validate_indicator_core_config(DEFAULT_INDICATOR_CORE)
        except Exception as exc:
            # Invalid on-disk configuration must fail back to the conservative
            # built-in three-layer profile instead of disabling signal controls.
            logger.warning(
                "Invalid indicator_core configuration; using defaults: {}", exc
            )
            loaded = validate_indicator_core_config(DEFAULT_INDICATOR_CORE)

        _CONFIG_CACHE = loaded
        _CONFIG_MTIME_NS = current_mtime
        return deepcopy(loaded)


def public_indicator_core_config() -> dict[str, Any]:
    config = load_indicator_core_config()
    for indicator_id, layer in config["indicators"].items():
        layer.update(INDICATOR_METADATA[indicator_id])
    return config


def save_indicator_core_config(raw: Any) -> dict[str, Any]:
    """Atomically update only the indicator_core section of strategy.yaml."""
    global _CONFIG_CACHE, _CONFIG_MTIME_NS

    config = validate_indicator_core_config(raw)
    with _CONFIG_LOCK:
        update_strategy_section(STRATEGY_FILE, "indicator_core", config)
        _CONFIG_CACHE = config
        _CONFIG_MTIME_NS = STRATEGY_FILE.stat().st_mtime_ns
    return public_indicator_core_config()


def _aligned(direction: str, long_value: bool, short_value: bool) -> bool:
    return (direction == "long" and long_value) or (direction == "short" and short_value)


def _score_smc(signal: Any, _params: dict[str, Any]) -> LayerScore:
    score = 0.0
    evidence: list[str] = []
    available = bool(getattr(signal, "current_price", 0.0) > 0)
    direction = getattr(signal, "direction", "wait")
    if not available:
        return LayerScore("smc_structure", False, 0, 40, "unavailable", ["No valid OHLC structure"])
    if _aligned(direction, signal.bias == "bullish", signal.bias == "bearish"):
        score += 8
        evidence.append("Execution-timeframe structure bias aligns with direction")
    # Higher timeframes are presentation-only and never affect scoring.
    order_block = getattr(signal, "order_block", None)
    if order_block and _aligned(
        direction, order_block.direction == "bullish", order_block.direction == "bearish"
    ):
        score += 10
        evidence.append("Direction-aligned Order Block")
    fvg = getattr(signal, "fvg", None)
    if fvg and _aligned(direction, fvg.direction == "bullish", fvg.direction == "bearish"):
        score += 6
        evidence.append("Direction-aligned FVG")
    if getattr(signal, "liquidity_swept", False) and _aligned(
        direction, signal.sweep_direction == "low", signal.sweep_direction == "high"
    ):
        score += 8
        evidence.append("Direction-aligned liquidity sweep")
    if _aligned(direction, getattr(signal, "in_discount", False), getattr(signal, "in_premium", False)):
        score += 5
        evidence.append("Entry is in the correct premium/discount zone")
    if getattr(signal, "bos", False) or getattr(signal, "choch", False):
        structure_events = list(getattr(signal, "swing_structures", [])) + list(
            getattr(signal, "internal_structures", [])
        )
        latest_event = max(
            structure_events,
            key=lambda item: int(getattr(item, "break_index", -1)),
            default=None,
        )
        expected = "bullish" if direction == "long" else "bearish" if direction == "short" else ""
        if latest_event is not None and getattr(latest_event, "direction", "") == expected:
            score += 3
            evidence.append("Direction-aligned BOS/CHoCH structure confirmation")
        elif latest_event is not None:
            evidence.append("Latest BOS/CHoCH opposes trade direction and received no score")
    status = "strong" if score >= 28 else "supporting" if score >= 16 else "weak"
    return LayerScore("smc_structure", True, score, 40, status, evidence)


def _score_volume(signal: Any, params: dict[str, Any]) -> LayerScore:
    if not getattr(signal, "volume_data_valid", False):
        return LayerScore("volume_delta", False, 0, 30, "unavailable", ["Reliable volume is unavailable"])
    quality = getattr(signal, "volume_quality", "unavailable")
    if quality != "exchange_aggressor":
        return LayerScore(
            "volume_delta", False, 0, 30, "unavailable",
            ["True exchange aggressor CVD is unavailable; candle-volume estimates are presentation-only"],
        )
    score = 0.0
    evidence: list[str] = []
    direction = getattr(signal, "direction", "wait")
    delta = float(getattr(signal, "volume_delta", 0.0))
    ratio = float(getattr(signal, "delta_ratio", 0.0))
    threshold = float(params["pressure_threshold"])
    if _aligned(direction, delta > 0, delta < 0):
        score += 5
        evidence.append("Aggressor volume delta supports trade direction")
    divergence = getattr(signal, "cvd_divergence", "none")
    divergence_aligned = _aligned(
        direction, divergence == "bullish", divergence == "bearish"
    )
    divergence_evidence = getattr(signal, "cvd_divergence_evidence", {}) or {}
    if divergence_aligned:
        score += 15
        evidence.append("CVD divergence supports trade direction")
        if (float(divergence_evidence.get("price_excursion_atr", 0.0)) >= 0.50
                and float(divergence_evidence.get("cvd_efficiency", 0.0)) >= 0.10):
            score += 5
            evidence.append("CVD divergence passes high-significance thresholds")
    absorption_type = getattr(signal, "delta_absorption_type", None)
    absorption_aligned = _aligned(
        direction,
        absorption_type == "bullish_absorption",
        absorption_type == "bearish_absorption",
    )
    if not divergence_aligned and getattr(signal, "delta_absorption", False) and absorption_aligned:
        score += 15
        evidence.append("Failed aggressive flow (absorption) is direction-aligned")
    elif not divergence_aligned and _aligned(direction, ratio >= threshold, ratio <= -threshold):
        score += 5
        evidence.append(f"Directional pressure exceeds {threshold:.0%}")
    if getattr(signal, "volume_spike", False):
        score += 5
        evidence.append("Volume expansion confirmed")
    score = min(score, 30.0)
    status = "strong" if score >= 22 else "supporting" if score >= 10 else "weak"
    return LayerScore("volume_delta", True, score, 30, status, evidence)


def _score_squeeze(signal: Any, _params: dict[str, Any]) -> LayerScore:
    if not getattr(signal, "squeeze_data_valid", False):
        return LayerScore("squeeze_momentum", False, 0, 30, "unavailable", ["Insufficient momentum history"])
    direction = getattr(signal, "direction", "wait")
    score = 0.0
    evidence: list[str] = []
    status_value = getattr(signal, "squeeze_status", "no_squeeze")
    if status_value == "squeeze_fire":
        score += 15
        evidence.append("Squeeze release detected")
    elif status_value == "no_squeeze":
        score += 8
        evidence.append("Momentum is not compressed")
    elif status_value == "squeeze_on":
        order_block = getattr(signal, "order_block", None)
        in_correct_zone = _aligned(
            direction, getattr(signal, "in_discount", False), getattr(signal, "in_premium", False)
        )
        if order_block and in_correct_zone:
            score += 8
            evidence.append("Squeeze compression storing energy at Order Block")
        else:
            score += 2
            evidence.append("Momentum is compressed")
    momentum_direction = getattr(signal, "momentum_direction", "")
    momentum = float(getattr(signal, "squeeze_momentum", 0.0))
    if _aligned(
        direction,
        momentum_direction == "accelerating_up" and momentum > 0,
        momentum_direction == "accelerating_down" and momentum < 0,
    ):
        score += 15
        evidence.append("Momentum acceleration supports trade direction")
    elif _aligned(
        direction,
        momentum_direction == "decelerating_up" and momentum > 0,
        momentum_direction == "decelerating_down" and momentum < 0,
    ):
        score += 8
        evidence.append("Momentum supports direction but is decelerating")
    # score is always >= 0 by construction; status thresholds:
    status = "strong" if score >= 22 else "supporting" if score >= 10 else "weak"
    return LayerScore("squeeze_momentum", True, score, 30, status, evidence)


Scorer = Callable[[Any, dict[str, Any]], LayerScore]


class IndicatorDecisionCore:
    """Score, normalize and explain the configured indicator registry."""

    scorers: dict[str, Scorer] = {
        "smc_structure": _score_smc,
        "volume_delta": _score_volume,
        "squeeze_momentum": _score_squeeze,
    }

    def config(self) -> dict[str, Any]:
        return load_indicator_core_config()

    def _evaluate_direction(
        self, signal: Any, active_config: dict[str, Any], direction: str
    ) -> dict[str, Any]:
        scored_signal = deepcopy(signal)
        scored_signal.direction = direction
        total_weight = 0.0
        available_weight = 0.0
        weighted_points = 0.0
        layers: list[dict[str, Any]] = []
        blocking_reasons: list[str] = []
        squeeze_bonus = 0

        for indicator_id, scorer in self.scorers.items():
            layer_config = active_config["indicators"][indicator_id]
            metadata = INDICATOR_METADATA[indicator_id]
            enabled = bool(layer_config["enabled"])
            weight = float(layer_config["weight"])
            is_bonus = indicator_id == "squeeze_momentum"
            if is_bonus:
                weight = 0.0  # Excluded from core score and data coverage.
            if not enabled:
                layers.append({
                    "id": indicator_id,
                    **metadata,
                    "enabled": False,
                    "required": bool(layer_config["required"]),
                    "available": False,
                    "weight": weight,
                    "score": 0,
                    "weighted_points": 0.0,
                    "status": "disabled",
                    "evidence": ["Disabled by strategy configuration"],
                })
                continue
            total_weight += weight
            layer_score = scorer(scored_signal, layer_config["params"])
            if layer_score.available:
                available_weight += weight
            elif layer_config["required"] and not is_bonus:
                blocking_reasons.append(f"Required indicator unavailable: {metadata['label']}")
            normalized = (
                max(0.0, min(layer_score.raw_score / layer_score.max_score, 1.0))
                if layer_score.max_score > 0 else 0.0
            )
            contribution = normalized * weight if layer_score.available else 0.0
            if (is_bonus and layer_score.available
                    and getattr(scored_signal, "squeeze_status", "") == "squeeze_fire"
                    and _aligned(direction,
                                 getattr(scored_signal, "squeeze_momentum", 0) > 0,
                                 getattr(scored_signal, "squeeze_momentum", 0) < 0)):
                # Display-only bonus; never added to the core approval score.
                squeeze_bonus = round(normalized * 10)
            weighted_points += contribution
            layers.append({
                "id": indicator_id,
                **metadata,
                "enabled": True,
                "required": bool(layer_config["required"]) and not is_bonus,
                "role": "bonus" if is_bonus else "core",
                "available": layer_score.available,
                "weight": weight,
                "score": round(normalized * 100),
                "weighted_points": round(contribution, 2),
                "status": layer_score.status,
                "evidence": layer_score.evidence,
            })

        coverage = round((available_weight / total_weight) * 100, 1) if total_weight else 0.0
        score = round((weighted_points / total_weight) * 100) if total_weight else 0
        minimum_coverage = float(active_config["minimum_data_coverage"])
        if coverage < minimum_coverage:
            blocking_reasons.append(
                f"Indicator data coverage {coverage:.1f}% is below {minimum_coverage:.1f}%"
            )
        return {
            "version": int(active_config["version"]),
            "score": max(0, min(score, 100)),
            "core_score": max(0, min(score, 100)),
            "squeeze_bonus": squeeze_bonus,
            "squeeze_bonus_max": 10,
            "coverage": coverage,
            "minimum_coverage": minimum_coverage,
            "ready": not blocking_reasons,
            "enabled_count": sum(1 for layer in layers if layer["enabled"]),
            "available_count": sum(1 for layer in layers if layer["enabled"] and layer["available"]),
            "blocking_reasons": blocking_reasons,
            "layers": layers,
        }

    def evaluate(self, signal: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
        active_config = validate_indicator_core_config(config or self.config())
        long_result = self._evaluate_direction(signal, active_config, "long")
        short_result = self._evaluate_direction(signal, active_config, "short")
        requested = str(getattr(signal, "direction", "wait")).lower()
        if requested == "long":
            selected = long_result
        elif requested == "short":
            selected = short_result
        else:
            selected = max((long_result, short_result), key=lambda item: item["score"])
            selected = deepcopy(selected)
            selected["squeeze_bonus"] = 0
        selected["selected_direction"] = requested if requested in {"long", "short"} else "evidence_only"
        selected["score_role"] = "setup" if requested in {"long", "short"} else "directional_evidence"
        selected["directional_scores"] = {
            "long": long_result["score"],
            "short": short_result["score"],
        }
        return selected
