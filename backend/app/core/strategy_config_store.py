"""Process-safe helpers for reading and updating strategy configuration.

All strategy sections share one lock because each update replaces the whole
YAML document.  Separate section-level locks could otherwise lose a concurrent
update made by another settings endpoint.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any

import yaml


STRATEGY_CONFIG_LOCK = threading.RLock()


def read_strategy_config(path: Path) -> dict[str, Any]:
    """Read a strategy YAML document while holding the shared process lock."""
    with STRATEGY_CONFIG_LOCK:
        if not path.exists():
            return {}
        with path.open(encoding="utf-8-sig") as handle:
            loaded = yaml.safe_load(handle) or {}
        if not isinstance(loaded, dict):
            raise ValueError("strategy.yaml must contain an object")
        return loaded


def update_strategy_section(
    path: Path,
    section: str,
    value: dict[str, Any],
) -> None:
    """Atomically replace one top-level section without losing other fields."""
    with STRATEGY_CONFIG_LOCK:
        strategy = read_strategy_config(path)
        strategy[section] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temp_path.open("x", encoding="utf-8", newline="\n") as handle:
                yaml.safe_dump(strategy, handle, sort_keys=False, allow_unicode=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
