from __future__ import annotations

from pathlib import Path
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.json_store import write_json
from app.engines.risk_engine import RiskEngine
from app.engines.smc_engine import SMCSignal
from app.models.paper_oms import (
    PaperOMSAccount,
    PaperOMSEvent,
    PaperOMSFill,
    PaperOMSOrder,
    PaperOMSPosition,
    PaperOMSRiskHalt,
)
from app.services.paper_oms import PaperOMS, PaperOMSValidation
from app.services.persistent_risk_guard import PersistentRiskGuard


async def _make_oms(tmp_path: Path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        for table in (
            PaperOMSAccount.__table__,
            PaperOMSPosition.__table__,
            PaperOMSOrder.__table__,
            PaperOMSFill.__table__,
            PaperOMSEvent.__table__,
            PaperOMSRiskHalt.__table__,
        ):
            await connection.run_sync(table.create)
    projection = tmp_path / "paper_trades.json"
    config = tmp_path / "paper_portfolio.json"
    write_json(projection, {})
    write_json(config, {"initial_capital": 100000.0, "currency": "USD"})
    oms = PaperOMS(factory)
    await oms.start(projection, config, subscribe=False)
    return oms, factory, engine


def _market_payload(symbol: str, *, size: float = 10.0) -> dict:
    from app.engines.price_hub import price_hub

    price_hub.update_price(
        symbol,
        100.0,
        bid=99.99,
        ask=100.0,
        source="hardening_test",
        transport="websocket",
        data_quality="test",
    )
    return {
        "symbol": symbol,
        "direction": "long",
        "order_type": "market",
        "entry": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "position_size": size,
        "exchange": "binance",
        "risk_pct": 1.0,
        "idempotency_key": f"hardening-{uuid.uuid4()}",
    }


@pytest.mark.anyio
async def test_hrd01_three_consecutive_stop_losses_create_persistent_24h_halt(tmp_path):
    oms, factory, engine = await _make_oms(tmp_path)
    for index in range(3):
        trade = await oms.place_order(
            _market_payload(f"HRDSTOP{index}{uuid.uuid4().hex[:4]}/USDT")
        )
        await oms.close_position(
            trade["id"],
            close_price=90.0,
            reason="Stop Loss (SL Hit) 🛑",
        )

    guard = PersistentRiskGuard(factory)
    halt = await guard.evaluate_and_persist(rolling_loss_limit_pct=99.0)

    assert halt is not None
    assert halt["reason"] == "three_consecutive_stop_losses"
    assert halt["source_payload"]["consecutive_stop_losses"] == 3

    # A new service instance sees the same durable DB halt after a restart.
    recovered_guard = PersistentRiskGuard(factory)
    recovered = await recovered_guard.active_halt()
    assert recovered is not None
    assert recovered["id"] == halt["id"]

    # Re-evaluation while active must not create duplicate halt rows.
    repeated = await recovered_guard.evaluate_and_persist(rolling_loss_limit_pct=99.0)
    assert repeated["id"] == halt["id"]
    async with factory() as session:
        count = await session.scalar(select(func.count(PaperOMSRiskHalt.id)))
    assert count == 1

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_hrd01_rolling_24h_realized_loss_creates_halt(tmp_path):
    oms, factory, engine = await _make_oms(tmp_path)
    trade = await oms.place_order(
        _market_payload(f"HRDLOSS{uuid.uuid4().hex[:5]}/USDT", size=200.0)
    )
    await oms.close_position(
        trade["id"],
        close_price=80.0,
        reason="Manual Loss Test",
    )

    guard = PersistentRiskGuard(factory)
    halt = await guard.evaluate_and_persist(rolling_loss_limit_pct=3.0)

    assert halt is not None
    assert halt["reason"] == "rolling_24h_loss_limit"
    assert halt["source_payload"]["rolling_realized_pnl_pct"] <= -3.0

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_directional_cluster_stop_loss_cooldown_survives_restart(tmp_path):
    oms, factory, engine = await _make_oms(tmp_path)
    trade = await oms.place_order(_market_payload("ETH/USDT"))
    await oms.close_position(
        trade["id"],
        close_price=90.0,
        reason="Stop Loss (SL Hit) 🛑",
    )

    recovered_guard = PersistentRiskGuard(factory)
    blocked = await recovered_guard.cluster_loss_cooldown(
        symbol="SOL/USDT", direction="long", cooldown_minutes=30
    )
    opposite = await recovered_guard.cluster_loss_cooldown(
        symbol="SOL/USDT", direction="short", cooldown_minutes=30
    )

    assert blocked is not None
    assert blocked["reason_code"] == "CLUSTER_LOSS_COOLDOWN"
    assert blocked["trigger_symbol"] == "ETH/USDT"
    assert opposite is None

    await oms.stop()
    await engine.dispose()


def test_hrd02_aggregate_portfolio_notional_is_capped_and_exhaustion_rejects():
    engine = RiskEngine()
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        direction="long",
        entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        risk_reward=3.0,
    )

    capped = engine.evaluate(
        signal,
        account_balance=10_000.0,
        max_leverage=5.0,
        current_gross_notional=49_900.0,
        minimum_rr=1.5,
    )
    assert capped.approved is True
    assert capped.portfolio_gross_notional <= 50_000.0
    assert capped.position_size <= 1.0
    assert capped.max_portfolio_notional == 50_000.0

    exhausted = engine.evaluate(
        signal,
        account_balance=10_000.0,
        max_leverage=5.0,
        current_gross_notional=50_000.0,
        minimum_rr=1.5,
    )
    assert exhausted.approved is False
    assert exhausted.portfolio_notional_ok is False
    assert "Aggregate portfolio notional limit reached" in (
        exhausted.rejection_reason or ""
    )


@pytest.mark.anyio
async def test_hrd03_protection_update_requires_live_quote(tmp_path):
    oms, _factory, engine = await _make_oms(tmp_path)
    symbol = f"HRDPROT{uuid.uuid4().hex[:6]}/USDT"
    payload = {
        "symbol": symbol,
        "direction": "long",
        "order_type": "limit",
        "entry": 100.0,
        "stop_loss": 90.0,
        "take_profit": 130.0,
        "position_size": 10.0,
        "exchange": "binance",
        "risk_pct": 1.0,
        "idempotency_key": f"hardening-{uuid.uuid4()}",
    }
    created = await oms.place_order(payload)
    await oms.process_market_tick({
        "symbol": symbol,
        "price": 99.5,
        "sequence": 1,
        "source": "test",
        "received_timestamp": 1000.0,
    })

    with pytest.raises(PaperOMSValidation, match="Fresh market quote is required"):
        await oms.update_protection(created["id"], stop_loss=105.0)

    await oms.stop()
    await engine.dispose()
