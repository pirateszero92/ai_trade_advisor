import hashlib
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.engines.smc_engine import SMCSignal
from app.engines.strategy_engine import StrategyResult
from app.services.execution_analysis import ExecutionAnalysis, ExecutionAnalysisService


def _analysis(*, config: dict | None = None) -> ExecutionAnalysis:
    frame = pd.DataFrame(
        {
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1000.0],
        },
        index=pd.DatetimeIndex([pd.Timestamp("2026-01-01T00:00:00Z")]),
    )
    signal = SMCSignal(symbol="BTC/USDT", timeframe="15m")
    signal.reaction = {"reaction_state": "TOUCH_WAIT"}
    strategy = StrategyResult(
        approved=True,
        direction="long",
        setup_direction="long",
        rejection_reasons=[],
    )
    now = datetime.now(timezone.utc)
    return ExecutionAnalysis(
        symbol="BTC/USDT",
        timeframe="15m",
        frame=frame,
        signal=signal,
        strategy=strategy,
        config_snapshot=config or {},
        snapshot_id="snapshot-1",
        generated_at=now,
        valid_until=now + timedelta(minutes=15),
    )


@pytest.mark.anyio
async def test_cached_execution_analysis_is_isolated_between_callers(monkeypatch):
    service = ExecutionAnalysisService()
    config: dict = {}
    monkeypatch.setattr(service, "_config_snapshot", lambda: config)
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20]
    key = ("BTC/USDT", "crypto", "binance", "limit", "15m", config_hash)
    canonical = _analysis(config=config)
    service._cache[key] = canonical

    first = await service.get(
        symbol="BTC/USDT",
        market_type="crypto",
        exchange="binance",
        entry_mode="limit",
    )
    second = await service.get(
        symbol="BTC/USDT",
        market_type="crypto",
        exchange="binance",
        entry_mode="limit",
    )

    first.strategy.approved = False
    first.strategy.rejection_reasons.append("caller-only rejection")
    first.signal.reaction["transition"] = {"changed": True}
    first.frame.iloc[0, first.frame.columns.get_loc("close")] = 1.0

    assert canonical.strategy.approved is True
    assert canonical.strategy.rejection_reasons == []
    assert "transition" not in canonical.signal.reaction
    assert canonical.frame.iloc[0]["close"] == 101.0
    assert second.strategy.approved is True
    assert second.strategy.rejection_reasons == []
    assert "transition" not in second.signal.reaction
    assert second.frame.iloc[0]["close"] == 101.0
