"""Shared contract for deterministic Tri-Core setup metadata.

Every boundary that persists or transports an actionable setup imports this
module.  Keeping the allow-list here prevents the engine, API and OMS from
silently disagreeing about which causal patterns are executable.
"""

from __future__ import annotations

from typing import Final

TRI_CORE_SETUP_TYPES: Final[frozenset[str]] = frozenset({
    "sweep_reversal",
    "displacement_retest",
    "breaker_retest",
    "fvg_retest",
    "breaker_shallow_retest",
    "fvg_shallow_retest",
    "range_boundary_ping_pong",
    "micro_sniper_3m",
    "micro_sniper_5m",
})


def normalize_tri_core_setup_type(value: object) -> str | None:
    """Return a normalized supported setup type, or ``None`` when omitted."""
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized not in TRI_CORE_SETUP_TYPES:
        allowed = ", ".join(sorted(TRI_CORE_SETUP_TYPES))
        raise ValueError(f"Unsupported setup_type; expected one of: {allowed}")
    return normalized


def strategy_family_for_setup_type(value: object) -> str:
    """Derive a non-authoritative strategy family from the canonical setup type."""
    normalized = normalize_tri_core_setup_type(value)
    if normalized == "range_boundary_ping_pong":
        return "ping_pong"
    if normalized == "sweep_reversal":
        return "reversal"
    if normalized in {
        "displacement_retest", "breaker_retest", "fvg_retest",
        "breaker_shallow_retest", "fvg_shallow_retest",
    }:
        return "swing"
    return "none"
