from __future__ import annotations

import pytest

from app.services.lifecycle_state_store import LifecycleStateStore


@pytest.mark.anyio
async def test_disk_fallback_deduplicates_and_survives_restart(tmp_path):
    path = tmp_path / "lifecycle.json"
    first = LifecycleStateStore(path)

    assert await first.transition("BTCUSDT:15m:event-1", "armed") is True
    assert await first.transition("BTCUSDT:15m:event-1", "armed") is False
    assert await first.transition("BTCUSDT:15m:event-1", "entry_ready") is True

    restarted = LifecycleStateStore(path)
    await restarted._load_disk()
    assert await restarted.transition("BTCUSDT:15m:event-1", "entry_ready") is False
    assert await restarted.transition("BTCUSDT:15m:event-1", "expired") is True
