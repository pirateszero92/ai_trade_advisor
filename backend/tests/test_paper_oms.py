"""Phase 6 authoritative Paper OMS regression tests."""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.json_store import write_json
from app.models.paper_oms import (
    PaperOMSAccount,
    PaperOMSEvent,
    PaperOMSFill,
    PaperOMSOrder,
    PaperOMSPosition,
)
from app.services.paper_oms import PaperOMS


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
        ):
            await connection.run_sync(table.create)
    projection = tmp_path / "paper_trades.json"
    config = tmp_path / "paper_portfolio.json"
    write_json(projection, {})
    write_json(config, {"initial_capital": 100000.0, "currency": "USD"})
    oms = PaperOMS(factory)
    await oms.start(projection, config, subscribe=False)
    return oms, factory, engine, projection, config


def _order_payload(*, direction: str, order_type: str = "market", symbol: str) -> dict:
    if direction == "long":
        stop_loss, take_profit = 95.0, 110.0
    else:
        stop_loss, take_profit = 105.0, 90.0
    return {
        "symbol": symbol,
        "direction": direction,
        "order_type": order_type,
        "entry": 100.0,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "position_size": 10.0,
        "exchange": "binance",
        "risk_pct": 1.0,
        "idempotency_key": f"entry-{uuid.uuid4()}",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("direction", "close_price"),
    (("long", 110.0), ("short", 90.0)),
)
async def test_market_round_trip_models_costs_and_supports_long_short(
    tmp_path, direction, close_price
):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    opened = await oms.place_order(
        _order_payload(direction=direction, symbol=f"P6{direction.upper()}/USDT")
    )
    assert opened["status"] == "open"
    assert opened["filled_quantity"] == pytest.approx(10.0)
    assert opened["fees_total"] > 0
    assert opened["spread_cost_total"] > 0
    assert opened["slippage_cost_total"] > 0

    closed = await oms.close_position(
        opened["id"],
        close_price=close_price,
        reason="test round trip",
        client_order_id=f"close-{uuid.uuid4()}",
    )
    assert closed["status"] == "closed"
    assert closed["remaining_quantity"] == pytest.approx(0.0)
    assert closed["realized_pnl_gross"] > closed["realized_pnl_net"] > 0
    assert closed["fees_total"] > opened["fees_total"]
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_forex_short_is_sell_to_open_and_buy_to_reduce(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    opened = await oms.place_order(
        _order_payload(direction="short", symbol="XAUUSD")
    )
    await oms.close_position(opened["id"], close_price=90.0, reason="cover short")
    fills = await oms.list_fills(opened["id"])
    assert [fill["side"] for fill in fills["fills"]] == ["sell", "buy"]
    assert [fill["position_effect"] for fill in fills["fills"]] == ["open", "reduce"]
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_limit_partial_fill_cancel_remainder_and_partial_tp(tmp_path):
    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6PART{uuid.uuid4().hex[:6]}/USDT"
    placed = await oms.place_order(
        _order_payload(direction="long", order_type="limit", symbol=symbol)
    )
    assert placed["status"] == "pending"

    changed = await oms.process_market_tick({
        "symbol": symbol,
        "price": 99.0,
        "bid": 98.99,
        "ask": 99.0,
        "aggressor_side": "sell",
        "last_trade_quantity": 20.0,
        "sequence": 101,
        "source": "test_ws",
        "transport": "websocket",
        "received_timestamp": 1_787_776_000.0,
    })
    assert len(changed) == 1
    partially_open = changed[0]
    assert partially_open["status"] == "open"
    assert partially_open["entry_order_status"] == "partially_filled"
    assert partially_open["filled_quantity"] == pytest.approx(0.2)
    assert partially_open["entry_order_remaining_quantity"] == pytest.approx(9.8)

    after_cancel = await oms.cancel_entry_order(placed["id"])
    assert after_cancel["status"] == "open"
    assert after_cancel["entry_order_status"] == "cancelled"
    assert after_cancel["remaining_quantity"] == pytest.approx(0.2)

    half = await oms.close_position(
        placed["id"],
        close_price=105.0,
        percentage=50.0,
        reason="TP1",
        client_order_id="partial-tp-idempotency",
    )
    assert half["status"] == "open"
    assert half["closed_quantity"] == pytest.approx(0.1)
    assert half["remaining_quantity"] == pytest.approx(0.1)

    same_half = await oms.close_position(
        placed["id"],
        close_price=105.0,
        percentage=50.0,
        reason="duplicate TP1",
        client_order_id="partial-tp-idempotency",
    )
    assert same_half["closed_quantity"] == pytest.approx(0.1)

    final = await oms.close_position(placed["id"], close_price=106.0, reason="runner")
    assert final["status"] == "closed"
    async with factory() as session:
        exit_orders = await session.scalar(
            select(func.count(PaperOMSOrder.id)).where(
                PaperOMSOrder.position_id == placed["id"],
                PaperOMSOrder.position_effect == "reduce",
            )
        )
    assert exit_orders == 2
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_cancelled_limit_cannot_fill_and_market_event_is_idempotent(tmp_path):
    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6RACE{uuid.uuid4().hex[:6]}/USDT"
    placed = await oms.place_order(
        _order_payload(direction="short", order_type="limit", symbol=symbol)
    )
    cancelled = await oms.cancel_entry_order(placed["id"])
    assert cancelled["status"] == "cancelled"
    quote = {
        "symbol": symbol,
        "price": 101.0,
        "bid": 101.0,
        "ask": 101.01,
        "aggressor_side": "buy",
        "last_trade_quantity": 1000.0,
        "sequence": 202,
        "source": "test_ws",
        "received_timestamp": 1_787_776_001.0,
    }
    assert await oms.process_market_tick(quote) == []
    assert await oms.process_market_tick(quote) == []
    async with factory() as session:
        fill_count = await session.scalar(
            select(func.count(PaperOMSFill.id)).where(PaperOMSFill.position_id == placed["id"])
        )
    assert fill_count == 0
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_restart_recovers_open_and_pending_state_from_postgres(tmp_path):
    oms, factory, engine, projection, config = await _make_oms(tmp_path)
    open_trade = await oms.place_order(
        _order_payload(direction="long", symbol=f"P6OPEN{uuid.uuid4().hex[:6]}/USDT")
    )
    pending_trade = await oms.place_order(
        _order_payload(
            direction="short",
            order_type="limit",
            symbol=f"P6PEND{uuid.uuid4().hex[:6]}/USDT",
        )
    )
    await oms.stop()

    recovered_oms = PaperOMS(factory)
    recovery = await recovered_oms.start(projection, config, subscribe=False)
    assert recovery["legacy_imported"] == 0
    assert recovery["open_recovered"] == 1
    assert recovery["pending_recovered"] == 1
    snapshot = await recovered_oms.list_positions(include_live=False)
    statuses = {trade["id"]: trade["status"] for trade in snapshot["trades"]}
    assert statuses[open_trade["id"]] == "open"
    assert statuses[pending_trade["id"]] == "pending"
    assert recovered_oms.health_snapshot()["ready"] is True
    await recovered_oms.stop()
    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("direction", ["long", "short"])
async def test_auto_be_and_multi_tier_trailing_advance_on_same_tick_and_persist(
    tmp_path, direction
):
    oms, factory, engine, projection, config = await _make_oms(tmp_path)
    symbol = f"P6PROTECT{direction.upper()}{uuid.uuid4().hex[:4]}/USDT"
    payload = _order_payload(direction=direction, symbol=symbol)
    payload.update({
        "take_profit": 125.0 if direction == "long" else 75.0,
        "auto_be": True,
        "trailing_stop": True,
    })
    opened = await oms.place_order(payload)
    entry = opened["entry"]
    risk = abs(entry - opened["initial_stop_loss"])

    async def tick(r_multiple: float, sequence: int):
        executable = entry + risk * r_multiple if direction == "long" else entry - risk * r_multiple
        return await oms.process_market_tick({
            "symbol": symbol,
            "price": executable,
            "bid": executable if direction == "long" else executable - 0.01,
            "ask": executable + 0.01 if direction == "long" else executable,
            "sequence": sequence,
            "source": "test_ws",
            "transport": "websocket",
            "received_timestamp": 1_787_776_000.0 + sequence,
        })

    be = (await tick(1.01, 301))[0]
    assert be["protection_stage"] == "breakeven"
    assert be["be_triggered"] is True
    assert be["stop_loss"] > entry if direction == "long" else be["stop_loss"] < entry

    tier_15 = (await tick(1.51, 302))[0]
    assert tier_15["protection_stage"] == "trailing_1_5r"
    expected_15 = entry + risk * 0.6 if direction == "long" else entry - risk * 0.6
    assert tier_15["stop_loss"] == pytest.approx(expected_15)

    tier_20 = (await tick(2.01, 303))[0]
    assert tier_20["protection_stage"] == "trailing_2_0r"
    dynamic = (await tick(2.60, 304))[0]
    assert dynamic["protection_stage"] == "trailing_dynamic"
    assert dynamic["max_r_multiple"] >= 2.5

    await oms.stop()
    recovered = PaperOMS(factory)
    recovery = await recovered.start(projection, config, subscribe=False)
    assert recovery["open_recovered"] == 1
    persisted = await recovered.get_position(opened["id"])
    assert persisted["protection_stage"] == "trailing_dynamic"
    assert persisted["stop_loss"] == pytest.approx(dynamic["stop_loss"])
    await recovered.stop()
    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("direction", ["long", "short"])
async def test_auto_be_exit_covers_modeled_fee_and_slippage(tmp_path, direction):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6BEEXIT{direction.upper()}{uuid.uuid4().hex[:4]}/USDT"
    payload = _order_payload(direction=direction, symbol=symbol)
    payload.update({
        "take_profit": 125.0 if direction == "long" else 75.0,
        "auto_be": True,
        "trailing_stop": False,
    })
    opened = await oms.place_order(payload)
    entry = opened["entry"]
    risk = abs(entry - opened["initial_stop_loss"])
    favorable = entry + risk * 1.01 if direction == "long" else entry - risk * 1.01
    advanced = (await oms.process_market_tick({
        "symbol": symbol,
        "price": favorable,
        "bid": favorable if direction == "long" else favorable - 0.01,
        "ask": favorable + 0.01 if direction == "long" else favorable,
        "sequence": 401,
        "source": "test_ws",
        "received_timestamp": 1_787_776_401.0,
    }))[0]

    stop = advanced["stop_loss"]
    closed = (await oms.process_market_tick({
        "symbol": symbol,
        "price": stop,
        "bid": stop if direction == "long" else stop - 0.01,
        "ask": stop + 0.01 if direction == "long" else stop,
        "sequence": 402,
        "source": "test_ws",
        "received_timestamp": 1_787_776_402.0,
    }))[0]

    assert closed["status"] == "closed"
    assert "Breakeven Shield" in closed["close_reason"]
    assert closed["realized_pnl_net"] >= -0.00001
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_reset_creates_new_account_generation_without_deleting_audit(tmp_path):
    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    trade = await oms.place_order(
        _order_payload(direction="short", symbol=f"P6RESET{uuid.uuid4().hex[:6]}/USDT")
    )
    await oms.close_position(trade["id"], close_price=90.0)
    async with factory() as session:
        fills_before = await session.scalar(select(func.count(PaperOMSFill.id)))
    account = await oms.reset_account(
        initial_capital=250000.0,
        currency="USD",
        clear_trades=True,
    )
    assert account["initial_capital"] == 250000.0
    assert account["closed_trades_count"] == 0
    async with factory() as session:
        fills_after = await session.scalar(select(func.count(PaperOMSFill.id)))
        account_count = await session.scalar(select(func.count(PaperOMSAccount.id)))
    assert fills_after == fills_before == 2
    assert account_count == 2
    await oms.stop()
    await engine.dispose()
