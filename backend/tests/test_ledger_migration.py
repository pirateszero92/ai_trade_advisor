"""Phase 3 JSON-to-PostgreSQL normalized ledger tests."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import pytest

from app.models.phase3 import FillLedgerRecord, OrderLedgerRecord, TradeLedgerRecord
from app.services.ledger_migration import sync_trade_snapshot


@pytest.mark.anyio
async def test_trade_order_fill_migration_is_idempotent():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(TradeLedgerRecord.__table__.create)
        await connection.run_sync(OrderLedgerRecord.__table__.create)
        await connection.run_sync(FillLedgerRecord.__table__.create)

    snapshot = {
        "trade-1": {
            "id": "trade-1",
            "mode": "paper",
            "broker": "paper",
            "symbol": "BTC/USDT",
            "direction": "long",
            "status": "closed",
            "order_type": "limit",
            "entry": 100.0,
            "fill_price": 100.1,
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "size": 2.0,
            "opened_at": "2026-01-01T00:00:00+00:00",
            "filled_at": "2026-01-01T01:00:00+00:00",
            "closed_at": "2026-01-01T05:00:00+00:00",
            "close_price": 109.5,
            "pnl": 18.8,
        }
    }
    async with factory() as session:
        first = await sync_trade_snapshot(session, snapshot)
        await session.commit()
    async with factory() as session:
        second = await sync_trade_snapshot(session, snapshot)
        await session.commit()
        trade_count = await session.scalar(select(func.count(TradeLedgerRecord.id)))
        order_count = await session.scalar(select(func.count(OrderLedgerRecord.id)))
        fill_count = await session.scalar(select(func.count(FillLedgerRecord.id)))

    assert first == {"trades": 1, "orders": 2, "fills_inserted": 2, "skipped": 0}
    assert second == {"trades": 1, "orders": 2, "fills_inserted": 0, "skipped": 0}
    assert trade_count == 1
    assert order_count == 2
    assert fill_count == 2
    await engine.dispose()


@pytest.mark.anyio
async def test_invalid_legacy_trade_is_skipped_without_partial_rows():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(TradeLedgerRecord.__table__.create)
        await connection.run_sync(OrderLedgerRecord.__table__.create)
        await connection.run_sync(FillLedgerRecord.__table__.create)
    async with factory() as session:
        stats = await sync_trade_snapshot(session, {"bad": {"status": "open"}})
        await session.commit()
    assert stats["trades"] == 0
    assert stats["skipped"] == 1
    await engine.dispose()

