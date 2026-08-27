"""Shared runtime configuration backed by the atomic local JSON store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.json_store import read_json, update_json

RUNTIME_SETTINGS_FILE = Path(__file__).parent.parent.parent / "config" / "runtime_settings.json"


def load_runtime_config() -> dict[str, Any]:
    data = read_json(RUNTIME_SETTINGS_FILE, dict)
    return data if isinstance(data, dict) else {}


def update_runtime_config(updates: dict[str, Any], removals: tuple[str, ...] = ()) -> dict[str, Any]:
    def mutate(data: dict[str, Any]) -> None:
        data.update(updates)
        for key in removals:
            data.pop(key, None)

    data, _ = update_json(RUNTIME_SETTINGS_FILE, dict, mutate)
    return data


def get_runtime_trading_mode(default: str = "paper") -> str:
    """Deprecated compatibility helper that always fails closed to Paper.

    Live authorization is a short-lived capability from ``LiveSessionManager``
    and must never be recovered from persisted runtime configuration.
    """
    del default
    return "paper"
