"""Phase 3 backtest persistence and release-gate API integration test."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import backtest_api
from app.core.config import get_settings
from app.main import app
from app.models.base import get_db
from app.models.phase3 import BacktestRun, ReleaseGateEvaluation


def _market_frame() -> pd.DataFrame:
    bars = 220
    index = pd.date_range("2025-01-01", periods=bars, freq="15min", tz="UTC")
    close = 100 + np.linspace(0, 20, bars) + np.sin(np.arange(bars) / 5)
    return pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.6,
            "low": close - 0.7,
            "close": close,
            "volume": np.full(bars, 10_000.0),
        },
        index=index,
    )


@pytest.mark.anyio
async def test_backtest_api_persists_run_and_fail_closed_gate(monkeypatch):
    frame = _market_frame()

    async def fake_ohlcv(*_args, **_kwargs):
        return frame

    monkeypatch.setattr(backtest_api._market, "get_ohlcv", fake_ohlcv)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(BacktestRun.__table__.create)
        await connection.run_sync(ReleaseGateEvaluation.__table__.create)

    async def override_db():
        async with factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = override_db
    cfg = get_settings()
    original_secret = cfg.app_secret_key
    cfg.app_secret_key = "phase-three-test-secret-key-00000000000"
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/backtests/runs",
                json={
                    "symbol": "BTC/USDT",
                    "timeframe": "15m",
                    "market_type": "crypto",
                    "exchange": "binance",
                    "limit": 220,
                    "warmup_bars": 60,
                    "oos_fraction": 0.5,
                    "stride_bars": 20,
                    "max_trades": 20,
                    "assumptions": {"max_holding_bars": 6},
                },
                headers={"X-API-Key": cfg.app_secret_key},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["run"]["run_type"] == "oos_backtest"
        assert body["release_gate"]["human_approval_required"] is True
        assert body["release_gate"]["production_eligible"] is False
        async with factory() as session:
            run = await session.scalar(select(BacktestRun))
            gate = await session.scalar(select(ReleaseGateEvaluation))
        assert run is not None
        assert gate is not None
        assert gate.production_eligible is False
    finally:
        cfg.app_secret_key = original_secret
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
