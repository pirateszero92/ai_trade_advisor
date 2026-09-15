"""Durable de-duplication for Tri-Core lifecycle transition alerts."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.core.config import get_settings


class LifecycleStateStore:
    """Persist the last emitted state per causal event.

    Redis is preferred so multiple workers perform an atomic compare-and-set.
    A small atomic JSON projection provides restart safety for a single-process
    deployment when Redis is unavailable; it never affects trade decisions.
    """

    _HASH_KEY = "apex:tri_core:lifecycle:v1"
    _CAS_SCRIPT = """
local current = redis.call('HGET', KEYS[1], ARGV[1])
if current == ARGV[2] then
  return 0
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
return 1
"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).resolve().parents[2] / "data" / "tri_core_lifecycle.json"
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._redis: Any = None
        self.backend = "memory"

    async def start(self) -> None:
        if self._redis is not None:
            return
        await self._load_disk()
        try:
            from redis.asyncio import Redis

            client = Redis.from_url(
                get_settings().redis_url,
                decode_responses=True,
                socket_connect_timeout=0.75,
                socket_timeout=0.75,
            )
            await asyncio.wait_for(client.ping(), timeout=1.0)
            self._redis = client
            self.backend = "redis"
            if self._records:
                pipeline = client.pipeline(transaction=False)
                for key, record in self._records.items():
                    state = str(record.get("state", ""))
                    if state:
                        pipeline.hsetnx(self._HASH_KEY, key, state)
                await pipeline.execute()
            logger.info("[TriCore] Lifecycle state store using Redis")
        except Exception as exc:  # noqa: BLE001 - Redis is an optional runtime dependency.
            self._redis = None
            self.backend = "disk"
            logger.warning(
                "[TriCore] Redis lifecycle store unavailable; using atomic disk fallback: {}",
                type(exc).__name__,
            )

    async def close(self) -> None:
        client, self._redis = self._redis, None
        if client is not None:
            try:
                await client.aclose()
            except Exception as exc:  # noqa: BLE001 - client type is optional/dynamic.
                logger.debug("[TriCore] Redis close failed: {}", type(exc).__name__)

    async def transition(self, key: str, state: str) -> bool:
        """Return True exactly when ``state`` differs from the stored state."""
        if not key or not state:
            return False
        if self._redis is not None:
            try:
                changed = await self._redis.eval(
                    self._CAS_SCRIPT, 1, self._HASH_KEY, key, state
                )
                if changed:
                    await self._mirror_disk(key, state)
                return bool(changed)
            except Exception as exc:  # noqa: BLE001 - fail over for every Redis client error.
                logger.warning(
                    "[TriCore] Redis lifecycle write failed; falling back to disk: {}",
                    type(exc).__name__,
                )
                await self.close()
                self.backend = "disk"

        async with self._lock:
            current = self._records.get(key, {}).get("state")
            if current == state:
                return False
            await self._mirror_disk_locked(key, state)
            return True

    async def _mirror_disk(self, key: str, state: str) -> None:
        async with self._lock:
            await self._mirror_disk_locked(key, state)

    async def _mirror_disk_locked(self, key: str, state: str) -> None:
        self._records[key] = {
            "state": state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Bound fallback growth. Stable event ids can otherwise accumulate
        # indefinitely on a long-running scanner.
        if len(self._records) > 10_000:
            ordered = sorted(
                self._records.items(),
                key=lambda item: str(item[1].get("updated_at", "")),
                reverse=True,
            )
            self._records = dict(ordered[:5_000])
        await asyncio.to_thread(self._write_disk_sync)

    async def _load_disk(self) -> None:
        def read() -> dict[str, dict[str, Any]]:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                return payload if isinstance(payload, dict) else {}
            except FileNotFoundError:
                return {}
            except (json.JSONDecodeError, OSError, UnicodeError) as exc:
                logger.warning("[TriCore] Ignoring invalid lifecycle projection: {}", exc)
                return {}

        self._records = await asyncio.to_thread(read)
        self.backend = "disk"

    def _write_disk_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._records, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.path)


lifecycle_state_store = LifecycleStateStore()
