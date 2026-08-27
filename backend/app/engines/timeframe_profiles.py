"""Validated Phase 5 timeframe-role profiles.

The project deliberately keeps the same three indicator families on every
timeframe.  A profile changes their sensitivity and responsibility; it does
not register a fourth indicator or average independent timeframe scores into
an executable decision.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from loguru import logger

from app.core.strategy_config_store import (
    STRATEGY_CONFIG_LOCK,
    read_strategy_config,
    update_strategy_section,
)
from app.engines.indicator_core import (
    DEFAULT_INDICATOR_CORE,
    load_indicator_core_config,
    validate_indicator_core_config,
)
from app.engines.market_data import canonical_timeframe


STRATEGY_FILE = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"
PROFILE_ROLES = ("bias", "setup", "trigger")
TRIGGER_TYPES = {"bos", "choch", "liquidity_sweep", "squeeze_fire"}
_TIMEFRAME_SECONDS = {
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
    "1M": 2_592_000,
}


DEFAULT_TIMEFRAME_PROFILES: dict[str, Any] = {
    "version": 1,
    "enabled": True,
    "roles": {
        "bias": {
            "timeframe": "4h",
            "lookback": 180,
            "smc": {
                "swing_length": 7,
                "internal_swing_length": 4,
                "eql_tolerance": 0.002,
                "order_block_lookback": 60,
                "fvg_lookback": 40,
                "atr_length": 14,
            },
            "indicator_overrides": {
                "smc_structure": {"weight": 60.0},
                "volume_delta": {"weight": 25.0},
                "squeeze_momentum": {"weight": 15.0},
            },
            "gate": {
                "min_confluence": 20.0,
                "require_indicator_readiness": True,
                "require_order_block": False,
                "allow_neutral_structure": False,
                "require_any_trigger": [],
            },
        },
        "setup": {
            "timeframe": "1h",
            "lookback": 300,
            "smc": {
                "swing_length": 5,
                "internal_swing_length": 3,
                "eql_tolerance": 0.002,
                "order_block_lookback": 50,
                "fvg_lookback": 30,
                "atr_length": 14,
            },
            "indicator_overrides": {
                "smc_structure": {"weight": 50.0},
                "volume_delta": {"weight": 30.0},
                "squeeze_momentum": {"weight": 20.0},
            },
            "gate": {
                "min_confluence": 45.0,
                "require_indicator_readiness": True,
                "require_order_block": True,
                "allow_neutral_structure": True,
                "require_any_trigger": [],
            },
        },
        "trigger": {
            "timeframe": "15m",
            "lookback": 300,
            "smc": {
                "swing_length": 3,
                "internal_swing_length": 2,
                "eql_tolerance": 0.0015,
                "order_block_lookback": 48,
                "fvg_lookback": 36,
                "atr_length": 14,
            },
            "indicator_overrides": {
                "smc_structure": {"weight": 35.0},
                "volume_delta": {"weight": 30.0},
                "squeeze_momentum": {"weight": 35.0},
            },
            "gate": {
                "min_confluence": 45.0,
                "require_indicator_readiness": True,
                "require_order_block": False,
                "allow_neutral_structure": True,
                "require_any_trigger": [
                    "bos",
                    "choch",
                    "liquidity_sweep",
                    "squeeze_fire",
                ],
            },
        },
    },
}

_CACHE: dict[str, Any] | None = None
_CACHE_MTIME_NS: int | None = None


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


def _merge_indicator_overrides(
    base: dict[str, Any], overrides: Any, *, role: str
) -> dict[str, Any]:
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise ValueError(f"timeframe_profiles.roles.{role}.indicator_overrides must be an object")
    unknown = set(overrides) - set(DEFAULT_INDICATOR_CORE["indicators"])
    if unknown:
        raise ValueError(f"Unregistered indicators in {role} profile: {sorted(unknown)}")

    merged = deepcopy(base)
    for indicator_id, supplied in overrides.items():
        if not isinstance(supplied, dict):
            raise ValueError(f"{role}.{indicator_id} override must be an object")
        unknown_fields = set(supplied) - {"enabled", "required", "weight", "params"}
        if unknown_fields:
            raise ValueError(
                f"Unknown {role}.{indicator_id} override fields: {sorted(unknown_fields)}"
            )
        layer = merged["indicators"][indicator_id]
        for field in ("enabled", "required", "weight"):
            if field in supplied:
                layer[field] = supplied[field]
        if "params" in supplied:
            if not isinstance(supplied["params"], dict):
                raise ValueError(f"{role}.{indicator_id}.params must be an object")
            layer["params"] = {**layer.get("params", {}), **supplied["params"]}
    return validate_indicator_core_config(merged)


def validate_timeframe_profiles(raw: Any) -> dict[str, Any]:
    """Return a canonical fail-closed 4H/1H/15m role configuration."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("timeframe_profiles must be an object")
    unknown_top = set(raw) - {"version", "enabled", "roles"}
    if unknown_top:
        raise ValueError(f"Unknown timeframe_profiles fields: {sorted(unknown_top)}")
    if raw.get("version", 1) != 1:
        raise ValueError("timeframe_profiles.version must be 1")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("timeframe_profiles.enabled must be boolean")

    requested_roles = raw.get("roles", {})
    if not isinstance(requested_roles, dict):
        raise ValueError("timeframe_profiles.roles must be an object")
    unknown_roles = set(requested_roles) - set(PROFILE_ROLES)
    if unknown_roles:
        raise ValueError(f"Unknown timeframe profile roles: {sorted(unknown_roles)}")

    canonical: dict[str, Any] = {"version": 1, "enabled": enabled, "roles": {}}
    for role in PROFILE_ROLES:
        defaults = DEFAULT_TIMEFRAME_PROFILES["roles"][role]
        supplied = requested_roles.get(role, {})
        if not isinstance(supplied, dict):
            raise ValueError(f"timeframe_profiles.roles.{role} must be an object")
        unknown_fields = set(supplied) - {
            "timeframe", "lookback", "smc", "indicator_overrides", "gate"
        }
        if unknown_fields:
            raise ValueError(f"Unknown {role} profile fields: {sorted(unknown_fields)}")

        timeframe = canonical_timeframe(supplied.get("timeframe", defaults["timeframe"]))
        lookback = _number(
            supplied.get("lookback", defaults["lookback"]),
            name=f"{role}.lookback",
            minimum=100,
            maximum=1500,
            integer=True,
        )

        smc_raw = supplied.get("smc", {})
        if not isinstance(smc_raw, dict):
            raise ValueError(f"{role}.smc must be an object")
        unknown_smc = set(smc_raw) - set(defaults["smc"])
        if unknown_smc:
            raise ValueError(f"Unknown {role}.smc fields: {sorted(unknown_smc)}")
        smc_values = {**defaults["smc"], **smc_raw}
        smc = {
            "swing_length": _number(
                smc_values["swing_length"], name=f"{role}.swing_length",
                minimum=2, maximum=30, integer=True,
            ),
            "internal_swing_length": _number(
                smc_values["internal_swing_length"], name=f"{role}.internal_swing_length",
                minimum=1, maximum=20, integer=True,
            ),
            "eql_tolerance": _number(
                smc_values["eql_tolerance"], name=f"{role}.eql_tolerance",
                minimum=0.0001, maximum=0.05,
            ),
            "order_block_lookback": _number(
                smc_values["order_block_lookback"], name=f"{role}.order_block_lookback",
                minimum=10, maximum=500, integer=True,
            ),
            "fvg_lookback": _number(
                smc_values["fvg_lookback"], name=f"{role}.fvg_lookback",
                minimum=5, maximum=500, integer=True,
            ),
            "atr_length": _number(
                smc_values["atr_length"], name=f"{role}.atr_length",
                minimum=5, maximum=100, integer=True,
            ),
        }
        if smc["internal_swing_length"] >= smc["swing_length"]:
            raise ValueError(f"{role}.internal_swing_length must be below swing_length")

        gate_raw = supplied.get("gate", {})
        if not isinstance(gate_raw, dict):
            raise ValueError(f"{role}.gate must be an object")
        unknown_gate = set(gate_raw) - set(defaults["gate"])
        if unknown_gate:
            raise ValueError(f"Unknown {role}.gate fields: {sorted(unknown_gate)}")
        gate_values = {**defaults["gate"], **gate_raw}
        for flag in (
            "require_indicator_readiness", "require_order_block", "allow_neutral_structure"
        ):
            if not isinstance(gate_values[flag], bool):
                raise ValueError(f"{role}.gate.{flag} must be boolean")
        triggers = gate_values["require_any_trigger"]
        if not isinstance(triggers, list) or any(not isinstance(item, str) for item in triggers):
            raise ValueError(f"{role}.gate.require_any_trigger must be a string list")
        unknown_triggers = set(triggers) - TRIGGER_TYPES
        if unknown_triggers:
            raise ValueError(f"Unknown {role} trigger types: {sorted(unknown_triggers)}")
        if role != "trigger" and triggers:
            raise ValueError(f"{role} profile cannot require entry triggers")
        gate = {
            "min_confluence": _number(
                gate_values["min_confluence"], name=f"{role}.gate.min_confluence",
                minimum=0, maximum=100,
            ),
            "require_indicator_readiness": gate_values["require_indicator_readiness"],
            "require_order_block": gate_values["require_order_block"],
            "allow_neutral_structure": gate_values["allow_neutral_structure"],
            "require_any_trigger": list(dict.fromkeys(triggers)),
        }

        overrides = deepcopy(supplied.get("indicator_overrides", defaults["indicator_overrides"]))
        # Validate the override against the immutable three-indicator registry.
        _merge_indicator_overrides(DEFAULT_INDICATOR_CORE, overrides, role=role)
        canonical["roles"][role] = {
            "timeframe": timeframe,
            "lookback": lookback,
            "smc": smc,
            "indicator_overrides": overrides,
            "gate": gate,
        }

    role_seconds = [
        _TIMEFRAME_SECONDS[canonical["roles"][role]["timeframe"]]
        for role in PROFILE_ROLES
    ]
    if not role_seconds[0] > role_seconds[1] > role_seconds[2]:
        raise ValueError("Timeframe hierarchy must be bias > setup > trigger")
    return canonical


def resolve_profile_indicator_config(
    profile: dict[str, Any], base: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Merge one role's overrides into the active three-indicator registry."""
    return _merge_indicator_overrides(
        base or load_indicator_core_config(),
        profile.get("indicator_overrides", {}),
        role=str(profile.get("timeframe", "profile")),
    )


def load_timeframe_profiles() -> dict[str, Any]:
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
            loaded = validate_timeframe_profiles(strategy.get("timeframe_profiles"))
        except Exception as exc:
            logger.warning("Invalid timeframe_profiles configuration; using defaults: {}", exc)
            loaded = validate_timeframe_profiles(DEFAULT_TIMEFRAME_PROFILES)
        _CACHE = loaded
        _CACHE_MTIME_NS = mtime
        return deepcopy(loaded)


def save_timeframe_profiles(raw: Any) -> dict[str, Any]:
    global _CACHE, _CACHE_MTIME_NS
    config = validate_timeframe_profiles(raw)
    with STRATEGY_CONFIG_LOCK:
        update_strategy_section(STRATEGY_FILE, "timeframe_profiles", config)
        _CACHE = config
        _CACHE_MTIME_NS = STRATEGY_FILE.stat().st_mtime_ns
    return deepcopy(config)
