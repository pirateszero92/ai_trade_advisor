"""Shared runtime configuration backed by the atomic local JSON store."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from app.core.config_contract import validate_runtime_config
from app.core.json_store import read_json, update_json

RUNTIME_SETTINGS_FILE = Path(__file__).parent.parent.parent / "config" / "runtime_settings.json"

_REVISION_LOCK = threading.RLock()
_RUNTIME_REVISION = 1


def runtime_config_revision() -> int:
    with _REVISION_LOCK:
        return _RUNTIME_REVISION


def load_runtime_config() -> dict[str, Any]:
    data = read_json(RUNTIME_SETTINGS_FILE, dict)
    if not isinstance(data, dict):
        return {}
    return validate_runtime_config(data)


def update_runtime_config(updates: dict[str, Any], removals: tuple[str, ...] = ()) -> dict[str, Any]:
    global _RUNTIME_REVISION

    def mutate(data: dict[str, Any]) -> None:
        candidate = dict(data)
        candidate.update(updates)
        for key in removals:
            candidate.pop(key, None)
        validated = validate_runtime_config(candidate)
        data.clear()
        data.update(validated)

    data, _ = update_json(RUNTIME_SETTINGS_FILE, dict, mutate)
    with _REVISION_LOCK:
        _RUNTIME_REVISION += 1
    return data


def get_runtime_trading_mode(default: str = "paper") -> str:
    """Deprecated compatibility helper that always fails closed to Paper.

    Live authorization is a short-lived capability from ``LiveSessionManager``
    and must never be recovered from persisted runtime configuration.
    """
    del default
    return "paper"
