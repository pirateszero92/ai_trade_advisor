"""Phase 3 evidence integrity and deterministic replay tests."""

from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.strategy_config_store import read_strategy_config
from app.engines.strategy_engine import STRATEGY_FILE
from app.engines.smc_engine import SMCEngine
from app.engines.strategy_engine import StrategyEngine
from app.models.evidence_event import EvidenceEvent
from app.models.base import get_db
from app.models.phase3 import BacktestRun
from app.main import app
from app.services.evidence import (
    append_evidence_event,
    build_decision_evidence,
    fingerprint,
    replay_decision_payload,
    serialize_market_window,
)


def _market_frame() -> pd.DataFrame:
    bars = 120
    index = pd.date_range("2026-01-01", periods=bars, freq="h", tz="UTC")
    path = 100.0 + np.linspace(0.0, 18.0, bars) + np.sin(np.arange(bars) / 4.0)
    return pd.DataFrame(
        {
            "open": path - 0.15,
            "high": path + 0.65,
            "low": path - 0.70,
            "close": path,
            "volume": 1000.0 + (np.arange(bars) % 11) * 25.0,
        },
        index=index,
    )


def _envelope():
    frame = _market_frame()
    config = read_strategy_config(STRATEGY_FILE)
    signal = SMCEngine().analyze(
        frame,
        "BTC/USDT",
        "1h",
        htf_bias="bullish",
        indicator_config=config["indicator_core"],
        regime_config=config["regime_policy"],
    )
    strategy = StrategyEngine(strategy_config=config).evaluate(signal)
    if strategy.effective_policy:
        signal.market_regime["effective_policy"] = strategy.effective_policy
    return build_decision_evidence(
        source="manual_analysis",
        symbol="BTC/USDT",
        timeframe="1h",
        market_type="crypto",
        exchange="binance",
        market_data=frame,
        htf_bias="bullish",
        entry_mode=signal.entry_type,
        signal=signal.to_dict(),
        strategy=strategy.to_dict(),
        risk={"approved": False},
        config_snapshot=config,
    )


def test_canonical_fingerprint_is_order_independent():
    assert fingerprint({"b": 2, "a": 1}) == fingerprint({"a": 1, "b": 2})


def test_market_window_rejects_non_finite_ohlc():
    frame = _market_frame()
    frame.iloc[-1, frame.columns.get_loc("close")] = np.nan
    with pytest.raises(ValueError, match="non-finite OHLC"):
        serialize_market_window(frame)


def test_recorded_decision_replays_with_identical_hash():
    envelope = _envelope()
    replay = replay_decision_payload(envelope.payload)
    assert replay["match"] is True
    assert replay["replayed_decision_hash"] == envelope.decision_hash


def test_replay_rejects_tampered_market_window():
    envelope = _envelope()
    tampered = deepcopy(envelope.payload)
    tampered["market_data"]["candles"][-1]["c"] += 100.0
    with pytest.raises(ValueError, match="hash mismatch"):
        replay_decision_payload(tampered)


@pytest.mark.anyio
async def test_append_evidence_event_round_trip():
    envelope = _envelope()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(EvidenceEvent.__table__.create)
    async with session_factory() as session:
        await append_evidence_event(envelope, session)
        await session.commit()
    async with session_factory() as session:
        stored = await session.scalar(
            select(EvidenceEvent).where(EvidenceEvent.id == envelope.event_id)
        )
    assert stored is not None
    assert stored.payload_hash == envelope.payload_hash
    assert stored.decision_hash == envelope.decision_hash
    await engine.dispose()


@pytest.mark.anyio
async def test_read_only_evidence_api_lists_and_replays_event():
    envelope = _envelope()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(EvidenceEvent.__table__.create)
    async with session_factory() as session:
        await append_evidence_event(envelope, session)
        await session.commit()

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    cfg = get_settings()
    original_secret = cfg.app_secret_key
    cfg.app_secret_key = "phase-three-test-secret-key-00000000000"
    headers = {"X-API-Key": cfg.app_secret_key}
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            listed = await client.get("/api/v1/evidence/events", headers=headers)
            replayed = await client.post(
                f"/api/v1/evidence/events/{envelope.event_id}/replay",
                headers=headers,
            )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["events"][0]["id"] == str(envelope.event_id)
        assert replayed.status_code == 200
        assert replayed.json()["match"] is True
    finally:
        cfg.app_secret_key = original_secret
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.mark.anyio
async def test_batch_replay_api_persists_reproducibility_run():
    envelope = _envelope()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(EvidenceEvent.__table__.create)
        await connection.run_sync(BacktestRun.__table__.create)
    async with session_factory() as session:
        await append_evidence_event(envelope, session)
        await session.commit()

    async def override_db():
        async with session_factory() as session:
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
                "/api/v1/evidence/batch-replay",
                json={"event_ids": [str(envelope.event_id)]},
                headers={"X-API-Key": cfg.app_secret_key},
            )
        assert response.status_code == 200
        assert response.json()["metrics"]["matches"] == 1
        assert response.json()["metrics"]["match_rate"] == 1.0
        async with session_factory() as session:
            stored = await session.scalar(select(BacktestRun))
        assert stored is not None
        assert stored.run_type == "batch_replay"
    finally:
        cfg.app_secret_key = original_secret
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
