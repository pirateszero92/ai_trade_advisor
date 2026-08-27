"""Idempotent JSON-ledger migration and asynchronous PostgreSQL mirror."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any
import uuid

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.json_store import read_json
from app.models.base import async_session_factory
from app.models.phase3 import (
    FillLedgerRecord,
    JsonMigrationCheckpoint,
    OrderLedgerRecord,
    TradeLedgerRecord,
)
from app.services.evidence import fingerprint


LEDGER_NAMESPACE = uuid.UUID("3d491f99-f84b-4f54-95c4-f69c94093058")
MIGRATION_NAME = "phase3-json-ledgers-v1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        return _json_safe(value.item())
    return value


def _quantity(trade: dict[str, Any]) -> float:
    return max(
        0.0,
        _number(
            trade.get("filled_quantity")
            or trade.get("size")
            or trade.get("position_size")
            or trade.get("qty")
        )
        or 0.0,
    )


def _stable_id(trade_id: str, leg: str, kind: str) -> uuid.UUID:
    return uuid.uuid5(LEDGER_NAMESPACE, f"{trade_id}:{leg}:{kind}")


async def _upsert_trade(
    session: AsyncSession,
    trade_id: str,
    raw_trade: dict[str, Any],
) -> tuple[int, int, int]:
    trade = _json_safe({**raw_trade, "id": trade_id})
    symbol = str(trade.get("symbol", "")).strip()
    direction = str(trade.get("direction", "")).lower()
    if not symbol or direction not in {"long", "short"}:
        return (0, 0, 0)

    mode = "live" if str(trade.get("mode", "paper")).lower() == "live" else "paper"
    status = str(trade.get("status", "open")).lower()
    quantity = _quantity(trade)
    opened_at = _timestamp(trade.get("opened_at") or trade.get("created_at"))
    closed_at = _timestamp(trade.get("closed_at"))
    now = _utcnow()
    record = await session.get(TradeLedgerRecord, trade_id)
    if record is None:
        record = TradeLedgerRecord(id=trade_id, imported_at=now, source_payload=trade)
        session.add(record)
    record.mode = mode
    record.broker = str(trade.get("broker") or ("paper" if mode == "paper" else "unknown"))[:30]
    record.symbol = symbol[:30]
    record.direction = direction
    record.status = status[:20]
    record.order_type = str(trade.get("order_type", "market"))[:20]
    record.entry_price = _number(trade.get("fill_price") or trade.get("entry"))
    record.stop_loss = _number(trade.get("stop_loss"))
    record.take_profit = _number(trade.get("take_profit"))
    record.requested_quantity = _number(
        trade.get("requested_quantity") or trade.get("size") or trade.get("position_size") or trade.get("qty")
    )
    record.filled_quantity = quantity if status in {"open", "closed"} else 0.0
    record.close_price = _number(trade.get("close_price"))
    record.realized_pnl = _number(trade.get("pnl"))
    record.mfe = _number(trade.get("mfe"))
    record.mae = _number(trade.get("mae"))
    record.opened_at = opened_at
    record.closed_at = closed_at
    record.mirrored_at = now
    record.source_version = 1
    record.source_payload = trade

    entry_order_id = _stable_id(trade_id, "entry", "order")
    entry_order = await session.get(OrderLedgerRecord, entry_order_id)
    if entry_order is None:
        entry_order = OrderLedgerRecord(id=entry_order_id, trade_id=trade_id, leg="entry")
        session.add(entry_order)
    entry_order.client_order_id = str(trade.get("idempotency_key") or trade_id)[:100]
    entry_order.order_type = str(trade.get("order_type", "market"))[:20]
    entry_order.side = "buy" if direction == "long" else "sell"
    entry_order.status = {
        "pending": "submitted",
        "cancelled": "cancelled",
        "rejected": "rejected",
    }.get(status, "filled")
    entry_order.limit_price = _number(trade.get("entry"))
    entry_order.requested_quantity = max(record.requested_quantity or quantity, 0.0)
    entry_order.filled_quantity = quantity if status in {"open", "closed"} else 0.0
    entry_order.submitted_at = opened_at
    entry_order.completed_at = (
        _timestamp(trade.get("filled_at")) or opened_at
        if entry_order.status == "filled"
        else _timestamp(trade.get("cancelled_at"))
    )
    entry_order.source_payload = {"migration": MIGRATION_NAME, "trade_status": status}

    order_count = 1
    fill_count = 0
    if quantity > 0 and status in {"open", "closed"} and record.entry_price:
        fill_id = _stable_id(trade_id, "entry", "fill")
        if await session.get(FillLedgerRecord, fill_id) is None:
            session.add(
                FillLedgerRecord(
                    id=fill_id,
                    order_id=entry_order_id,
                    trade_id=trade_id,
                    leg="entry",
                    filled_at=_timestamp(trade.get("filled_at")) or opened_at or now,
                    price=record.entry_price,
                    quantity=quantity,
                    fee=_number(trade.get("entry_fee")) or 0.0,
                    spread_cost=_number(trade.get("entry_spread_cost")) or 0.0,
                    slippage_cost=_number(trade.get("entry_slippage_cost")) or 0.0,
                    latency_ms=int(_number(trade.get("entry_latency_ms")) or 0),
                    liquidity=str(trade.get("entry_liquidity", "unknown"))[:12],
                    source_payload={"migration": MIGRATION_NAME},
                )
            )
            fill_count += 1

    if status == "closed" and record.close_price and quantity > 0:
        exit_order_id = _stable_id(trade_id, "exit", "order")
        exit_order = await session.get(OrderLedgerRecord, exit_order_id)
        if exit_order is None:
            exit_order = OrderLedgerRecord(id=exit_order_id, trade_id=trade_id, leg="exit")
            session.add(exit_order)
        exit_order.client_order_id = f"{trade_id}:exit"[:100]
        exit_order.order_type = "market"
        exit_order.side = "sell" if direction == "long" else "buy"
        exit_order.status = "filled"
        exit_order.limit_price = record.close_price
        exit_order.requested_quantity = quantity
        exit_order.filled_quantity = quantity
        exit_order.submitted_at = closed_at
        exit_order.completed_at = closed_at
        exit_order.source_payload = {
            "migration": MIGRATION_NAME,
            "close_reason": trade.get("close_reason"),
        }
        order_count += 1
        exit_fill_id = _stable_id(trade_id, "exit", "fill")
        if await session.get(FillLedgerRecord, exit_fill_id) is None:
            session.add(
                FillLedgerRecord(
                    id=exit_fill_id,
                    order_id=exit_order_id,
                    trade_id=trade_id,
                    leg="exit",
                    filled_at=closed_at or now,
                    price=record.close_price,
                    quantity=quantity,
                    fee=_number(trade.get("exit_fee")) or 0.0,
                    spread_cost=_number(trade.get("exit_spread_cost")) or 0.0,
                    slippage_cost=_number(trade.get("exit_slippage_cost")) or 0.0,
                    latency_ms=int(_number(trade.get("exit_latency_ms")) or 0),
                    liquidity=str(trade.get("exit_liquidity", "unknown"))[:12],
                    source_payload={
                        "migration": MIGRATION_NAME,
                        "close_reason": trade.get("close_reason"),
                    },
                )
            )
            fill_count += 1
    return (1, order_count, fill_count)


async def sync_trade_snapshot(
    session: AsyncSession,
    trades: dict[str, dict[str, Any]],
) -> dict[str, int]:
    stats = {"trades": 0, "orders": 0, "fills_inserted": 0, "skipped": 0}
    for trade_id, trade in trades.items():
        if not isinstance(trade, dict):
            stats["skipped"] += 1
            continue
        trade_count, order_count, fill_count = await _upsert_trade(
            session, str(trade_id)[:64], trade
        )
        if trade_count == 0:
            stats["skipped"] += 1
        stats["trades"] += trade_count
        stats["orders"] += order_count
        stats["fills_inserted"] += fill_count
    await session.flush()
    return stats


def _read_ledgers(paper_path: Path, live_path: Path) -> dict[str, dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {}
    for path, mode in ((paper_path, "paper"), (live_path, "live")):
        raw = read_json(path, dict) if path.exists() else {}
        if not isinstance(raw, dict):
            continue
        for trade_id, value in raw.items():
            if isinstance(value, dict):
                combined[str(trade_id)] = {**value, "mode": mode}
    return combined


async def migrate_json_ledgers(
    paper_path: Path,
    live_path: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Import both isolated JSON ledgers without modifying or deleting them."""
    trades = _read_ledgers(paper_path, live_path)
    source_hash = fingerprint(_json_safe(trades))
    async with async_session_factory() as session:
        checkpoint = await session.get(JsonMigrationCheckpoint, MIGRATION_NAME)
        if checkpoint is not None and checkpoint.source_hash == source_hash and not force:
            return {**checkpoint.stats, "status": "unchanged", "source_hash": source_hash}
        try:
            stats = await sync_trade_snapshot(session, trades)
            if checkpoint is None:
                checkpoint = JsonMigrationCheckpoint(
                    name=MIGRATION_NAME,
                    source_hash=source_hash,
                    stats=stats,
                )
                session.add(checkpoint)
            else:
                checkpoint.source_hash = source_hash
                checkpoint.completed_at = _utcnow()
                checkpoint.stats = stats
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return {**stats, "status": "migrated", "source_hash": source_hash}


class LedgerMirror:
    """Coalescing async mirror; JSON remains a compatibility source in Phase 3."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, dict[str, Any]]] = asyncio.Queue(maxsize=1)
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.running = False
        self.ready = False
        self.last_error: str | None = None
        self.last_stats: dict[str, Any] = {}

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.ready = True
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._worker())

    def enqueue_snapshot(self, trades: dict[str, dict[str, Any]]) -> None:
        if not self.running or self._loop is None or self._loop.is_closed():
            return
        snapshot = deepcopy(trades)
        self._loop.call_soon_threadsafe(self._enqueue_nowait, snapshot)

    def _enqueue_nowait(self, snapshot: dict[str, dict[str, Any]]) -> None:
        if not self.running:
            return
        try:
            if self._queue.full():
                self._queue.get_nowait()
                self._queue.task_done()
            self._queue.put_nowait(snapshot)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            logger.warning("[LedgerMirror] Could not enqueue the latest ledger snapshot")

    async def _worker(self) -> None:
        while self.running:
            try:
                snapshot = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                async with async_session_factory() as session:
                    try:
                        self.last_stats = await sync_trade_snapshot(session, snapshot)
                        await session.commit()
                        self.ready = True
                        self.last_error = None
                    except Exception:
                        await session.rollback()
                        raise
            except Exception as exc:
                self.ready = False
                self.last_error = type(exc).__name__
                logger.error("[LedgerMirror] PostgreSQL mirror failed: {}", exc)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        if not self.running:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=5.0)
        except TimeoutError:
            logger.warning("[LedgerMirror] Timed out while flushing the mirror queue")
        self.running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._loop = None


ledger_mirror = LedgerMirror()
