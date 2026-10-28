"""PHASE 24 configuration ownership, validation, and audit metadata."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.evidence import fingerprint

CONFIG_CONTRACT_VERSION = "phase24-v1"

RUNTIME_OWNERSHIP: dict[str, str] = {
    "entry_mode": "execution",
    "binance_trading_mode": "execution",
    "auto_sl_tp": "execution",
    "auto_invalidation": "execution",
    "target_rr": "execution",
    "default_sl_pct": "risk",
    "risk_per_trade": "risk",
    "max_daily_loss": "risk",
    "max_open_positions": "risk",
    "btc_anchor_filter_enabled": "risk",
    "btc_anchor_dump_threshold_pct": "risk",
    "cluster_loss_cooldown_enabled": "risk",
    "cluster_loss_cooldown_minutes": "risk",
    "loss_streak_halt_enabled": "risk",
    "loss_streak_halt_hours": "risk",
    "counter_trend_filter_enabled": "risk",
    "ping_pong_enabled": "strategy",
    "auto_trade_enabled": "execution",
    "auto_trade_require_ai_approval": "ai",
    "auto_trade_ai_policy": "ai",
    "auto_trade_min_grade": "deterministic",
    "auto_trade_entry_type": "execution",
    "auto_trade_entry_policy": "execution",
    "auto_trade_limit_timeout_bars": "execution",
    "execution_shadow_compare_enabled": "execution",
    "auto_trade_cooldown_seconds": "execution",
    "disabled_brokers": "execution",
    "provider": "ai",
    "local_endpoint": "ai",
    "local_model": "ai",
    "local_key": "ai",
    "local_api_key": "ai",
    "gemini_model": "ai",
    "openrouter_model": "ai",
    "scanner_ai_mode": "ai",
    "watchlist": "research",
    "telegram_token": "execution",
    "telegram_chat_id": "execution",
    "line_token": "execution",
    # Compatibility-only persisted field. MTF never becomes an execution gate.
    "mtf_hierarchy_required": "research",
}

LEGACY_RUNTIME_ALIASES: dict[str, dict[str, Any]] = {
    "target_rr": {
        "canonical_name": "minimum_executable_rr",
        "owner": "execution",
        "behavior": "ExecutionRevalidator and RiskEngine executable R:R floor",
    },
    "risk_per_trade": {
        "canonical_name": "risk_per_trade_pct",
        "owner": "risk",
        "behavior": "RiskEngine budget override",
    },
    "default_sl_pct": {
        "canonical_name": "max_stop_distance_pct",
        "owner": "risk",
        "behavior": "RiskEngine stop-distance safety ceiling",
    },
    "mtf_hierarchy_required": {
        "canonical_name": "research_mtf_only",
        "owner": "research",
        "behavior": "forced false; higher timeframes are context-only",
    },
}

STRATEGY_OWNERSHIP: dict[str, str] = {
    "tri_core_policy": "deterministic",
    "analysis": "deterministic",
    "indicator_core": "deterministic",
    "regime_policy": "context_only",
    "timeframe_profiles": "research",
    "filters": "legacy_compatibility",
    "long_conditions": "legacy_compatibility",
    "short_conditions": "legacy_compatibility",
    "symbol_overrides": "legacy_compatibility",
}


def validate_runtime_config(data: dict[str, Any]) -> dict[str, Any]:
    """Validate persisted runtime values without silently coercing authority."""
    clean = deepcopy(data)

    def number(name: str, minimum: float, maximum: float) -> None:
        if name not in clean:
            return
        value = clean[name]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{name} must be numeric")
        if not minimum <= float(value) <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")

    number("target_rr", 1.0, 20.0)
    number("default_sl_pct", 0.01, 20.0)
    number("risk_per_trade", 0.01, 5.0)
    number("max_daily_loss", 0.01, 20.0)
    number("max_open_positions", 1, 100)
    number("auto_trade_cooldown_seconds", 30, 3600)
    number("auto_trade_limit_timeout_bars", 1, 20)
    number("btc_anchor_dump_threshold_pct", 0.2, 5.0)
    number("cluster_loss_cooldown_minutes", 5, 240)
    number("loss_streak_halt_hours", 1, 24)

    if "entry_mode" in clean and clean["entry_mode"] not in {"limit", "market"}:
        raise ValueError("entry_mode must be limit or market")
    if "binance_trading_mode" in clean and clean["binance_trading_mode"] not in {"futures", "spot"}:
        raise ValueError("binance_trading_mode must be futures or spot")
    if "auto_trade_min_grade" in clean and clean["auto_trade_min_grade"] not in {"S", "A", "B"}:
        raise ValueError("auto_trade_min_grade must be S, A or B")
    if "auto_trade_entry_type" in clean and clean["auto_trade_entry_type"] not in {
        "momentum_market", "limit_pullback"
    }:
        raise ValueError("auto_trade_entry_type is invalid")
    if "auto_trade_entry_policy" in clean and clean["auto_trade_entry_policy"] not in {
        "fixed", "adaptive"
    }:
        raise ValueError("auto_trade_entry_policy must be fixed or adaptive")
    if "auto_trade_ai_policy" in clean and clean["auto_trade_ai_policy"] not in {
        "strict", "guarded", "advisory"
    }:
        raise ValueError("auto_trade_ai_policy must be strict, guarded, or advisory")

    for key in (
        "auto_sl_tp", "auto_invalidation", "auto_trade_enabled",
        "auto_trade_require_ai_approval", "mtf_hierarchy_required",
        "execution_shadow_compare_enabled",
        "btc_anchor_filter_enabled",
        "cluster_loss_cooldown_enabled",
        "loss_streak_halt_enabled",
        "counter_trend_filter_enabled",
        "ping_pong_enabled",
    ):
        if key in clean and not isinstance(clean[key], bool):
            raise ValueError(f"{key} must be boolean")

    if "watchlist" in clean and not isinstance(clean["watchlist"], list):
        raise ValueError("watchlist must be a list")
    if "disabled_brokers" in clean and not isinstance(clean["disabled_brokers"], list):
        raise ValueError("disabled_brokers must be a list")

    # Architectural invariant: higher-timeframe context can never become a hard gate.
    if clean.get("mtf_hierarchy_required") is True:
        raise ValueError("mtf_hierarchy_required cannot be enabled; MTF is context-only")
    return clean


def runtime_sections(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sections = {name: {} for name in ("deterministic", "ai", "execution", "risk", "research", "unclassified")}
    for key, value in data.items():
        owner = RUNTIME_OWNERSHIP.get(key, "unclassified")
        sections.setdefault(owner, {})[key] = deepcopy(value)
    return sections


def config_manifest(
    *,
    strategy: dict[str, Any],
    runtime: dict[str, Any],
    runtime_revision: int,
) -> dict[str, Any]:
    runtime_validated = validate_runtime_config(runtime)
    return {
        "contract_version": CONFIG_CONTRACT_VERSION,
        "strategy_hash": fingerprint(strategy),
        "runtime_hash": fingerprint(runtime_validated),
        "runtime_revision": int(runtime_revision),
        "runtime_sections": runtime_sections(runtime_validated),
        "runtime_ownership": deepcopy(RUNTIME_OWNERSHIP),
        "strategy_ownership": deepcopy(STRATEGY_OWNERSHIP),
        "legacy_runtime_aliases": deepcopy(LEGACY_RUNTIME_ALIASES),
        "authority_notes": {
            "structural_rr": "tri_core_policy.minimum_rr",
            "executable_rr": "runtime target_rr compatibility key -> minimum_executable_rr",
            "position_sizing": "RiskEngine only",
            "ai": "reviewer only; no sizing or price authority",
            "mtf": "research/context only; never a hard execution gate",
        },
    }
