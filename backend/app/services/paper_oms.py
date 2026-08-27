"""Transactional, restart-safe Paper Order Management System.

PostgreSQL is the authority for every Paper order, position and fill once this
service has started. The legacy JSON file is written only as a compatibility
projection for older readers; it is never consulted again after bootstrap.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import uuid

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.json_store import read_json, write_json
from app.models.base import async_session_factory
from app.models.paper_oms import (
    PaperOMSAccount,
    PaperOMSEvent,
    PaperOMSFill,
    PaperOMSOrder,
    PaperOMSPosition,
)


ZERO = Decimal("0")
EPSILON = Decimal("0.0000000001")
OMS_NAMESPACE = uuid.UUID("a88ba97a-b600-4b14-9e38-38c106124c10")
ACTIVE_ORDER_STATES = ("submitted", "partially_filled")
ACTIVE_POSITION_STATES = ("pending", "open")


class PaperOMSError(RuntimeError):
    pass


class PaperOMSNotFound(PaperOMSError):
    pass


class PaperOMSConflict(PaperOMSError):
    pass


class PaperOMSValidation(PaperOMSError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _decimal(value: Any, *, name: str = "value", positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaperOMSValidation(f"{name} must be a finite number") from exc
    if not result.is_finite() or (positive and result <= ZERO):
        qualifier = "positive " if positive else "finite "
        raise PaperOMSValidation(f"{name} must be a {qualifier}number")
    return result


def _float(value: Any) -> float:
    return float(value or 0)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        result = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _norm_symbol(symbol: str) -> str:
    return "".join(ch for ch in str(symbol).upper() if ch.isalnum())


class PaperOMS:
    """Authoritative Paper OMS with transactional state transitions."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session_factory = session_factory or async_session_factory
        self.ready = False
        self.running = False
        self.startup_attempted = False
        self.last_error: str | None = None
        self.last_recovery: dict[str, Any] = {}
        self._projection_path: Path | None = None
        self._config_path: Path | None = None
        self._subscribed = False
        self._worker_task: asyncio.Task | None = None
        self._tick_event = asyncio.Event()
        self._latest_ticks: dict[str, dict[str, Any]] = {}
        self._active_symbols: set[str] = set()
        self._projection_lock = asyncio.Lock()

    async def start(
        self,
        projection_path: Path,
        config_path: Path,
        *,
        subscribe: bool = True,
    ) -> dict[str, Any]:
        if self.running:
            return dict(self.last_recovery)
        self.startup_attempted = True
        self._projection_path = projection_path
        self._config_path = config_path
        try:
            imported = await self._bootstrap_account_and_legacy()
            await self._refresh_active_symbols()
            await self._write_projection()
            self.running = True
            self.ready = True
            self.last_error = None
            if subscribe:
                from app.engines.price_hub import price_hub

                price_hub.subscribe(self._enqueue_tick)
                self._subscribed = True
                for symbol in await self.active_symbol_names():
                    price_hub.register_symbol(symbol)
                self._worker_task = asyncio.create_task(
                    self._tick_worker(), name="paper-oms-market-events"
                )
            async with self._session_factory() as session:
                account = await self._active_account(session)
                pending = await session.scalar(
                    select(func.count(PaperOMSPosition.id)).where(
                        PaperOMSPosition.account_id == account.id,
                        PaperOMSPosition.status == "pending",
                    )
                )
                opened = await session.scalar(
                    select(func.count(PaperOMSPosition.id)).where(
                        PaperOMSPosition.account_id == account.id,
                        PaperOMSPosition.status == "open",
                    )
                )
            self.last_recovery = {
                "status": "ready",
                "legacy_imported": imported,
                "pending_recovered": int(pending or 0),
                "open_recovered": int(opened or 0),
                "active_symbols": len(self._active_symbols),
            }
            return dict(self.last_recovery)
        except Exception as exc:
            self.ready = False
            self.running = False
            self.last_error = type(exc).__name__
            logger.exception("[PaperOMS] Startup failed: {}", exc)
            raise

    async def stop(self) -> None:
        self.ready = False
        self.running = False
        if self._subscribed:
            from app.engines.price_hub import price_hub

            price_hub.unsubscribe(self._enqueue_tick)
            self._subscribed = False
        self._tick_event.set()
        if self._worker_task is not None:
            self._worker_task.cancel()
            await asyncio.gather(self._worker_task, return_exceptions=True)
        self._worker_task = None
        self._latest_ticks.clear()

    def health_snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "ready": self.ready,
            "startup_attempted": self.startup_attempted,
            "last_error": self.last_error,
            "recovery": dict(self.last_recovery),
        }

    async def _active_account(
        self, session: AsyncSession, *, lock: bool = False
    ) -> PaperOMSAccount:
        stmt = (
            select(PaperOMSAccount)
            .where(PaperOMSAccount.active.is_(True))
            .order_by(PaperOMSAccount.created_at.desc())
            .limit(1)
        )
        if lock:
            stmt = stmt.with_for_update()
        account = (await session.execute(stmt)).scalar_one_or_none()
        if account is None:
            raise PaperOMSError("Paper OMS has no active account generation")
        return account

    def _read_account_config(self) -> dict[str, Any]:
        if self._config_path is None:
            return {"initial_capital": 100000.0, "currency": "USD"}
        data = read_json(
            self._config_path,
            lambda: {"initial_capital": 100000.0, "currency": "USD"},
        )
        return data if isinstance(data, dict) else {"initial_capital": 100000.0, "currency": "USD"}

    async def _bootstrap_account_and_legacy(self) -> int:
        imported = 0
        async with self._session_factory() as session:
            try:
                account = (await session.execute(
                    select(PaperOMSAccount)
                    .where(PaperOMSAccount.active.is_(True))
                    .order_by(PaperOMSAccount.created_at.desc())
                    .limit(1)
                )).scalar_one_or_none()
                if account is None:
                    config = self._read_account_config()
                    account = PaperOMSAccount(
                        initial_capital=_decimal(
                            config.get("initial_capital", 100000),
                            name="initial_capital",
                            positive=True,
                        ),
                        currency=str(config.get("currency", "USD"))[:8],
                    )
                    session.add(account)
                    await session.flush()

                existing = await session.scalar(
                    select(func.count(PaperOMSPosition.id)).where(
                        PaperOMSPosition.account_id == account.id
                    )
                )
                if not existing and self._projection_path and self._projection_path.exists():
                    raw = read_json(self._projection_path, dict)
                    if isinstance(raw, dict):
                        for trade_id, trade in raw.items():
                            if isinstance(trade, dict) and await self._import_legacy_trade(
                                session, account, str(trade_id), trade
                            ):
                                imported += 1
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        return imported

    async def _import_legacy_trade(
        self,
        session: AsyncSession,
        account: PaperOMSAccount,
        trade_id: str,
        raw: dict[str, Any],
    ) -> bool:
        if str(raw.get("mode", "paper")).lower() != "paper":
            return False
        direction = str(raw.get("direction", "")).lower()
        symbol = str(raw.get("symbol", "")).strip()
        status = str(raw.get("status", "open")).lower()
        if direction not in {"long", "short"} or not symbol:
            return False
        try:
            quantity = _decimal(
                raw.get("filled_quantity")
                or raw.get("size")
                or raw.get("position_size")
                or raw.get("qty"),
                name="quantity",
                positive=True,
            )
            entry = _decimal(
                raw.get("fill_price") or raw.get("entry"),
                name="entry",
                positive=True,
            )
            stop_loss = _decimal(raw.get("stop_loss"), name="stop_loss", positive=True)
            take_profit = _decimal(raw.get("take_profit"), name="take_profit", positive=True)
        except PaperOMSValidation:
            return False

        now = _utcnow()
        created = _timestamp(raw.get("opened_at") or raw.get("created_at")) or now
        opened_quantity = quantity if status in {"open", "closed"} else ZERO
        closed_quantity = quantity if status == "closed" else ZERO
        remaining_quantity = opened_quantity - closed_quantity
        normalized_status = status if status in {"pending", "open", "closed", "cancelled"} else "cancelled"
        position = PaperOMSPosition(
            id=trade_id[:64],
            account_id=account.id,
            symbol=symbol[:30],
            direction=direction,
            status=normalized_status,
            order_type=str(raw.get("order_type", "market"))[:20],
            exchange=str(raw.get("exchange", "binance"))[:30],
            tag=str(raw.get("tag") or f"POS-{trade_id[:8]}")[:100],
            notes=str(raw.get("notes", ""))[:4000],
            requested_quantity=quantity,
            opened_quantity=opened_quantity,
            closed_quantity=closed_quantity,
            remaining_quantity=max(remaining_quantity, ZERO),
            requested_entry_price=entry,
            average_entry_price=entry if opened_quantity > ZERO else None,
            average_exit_price=(
                _decimal(raw.get("close_price"), name="close_price", positive=True)
                if status == "closed" and raw.get("close_price")
                else None
            ),
            stop_loss=stop_loss,
            initial_stop_loss=_decimal(raw.get("initial_stop_loss", stop_loss)),
            take_profit=take_profit,
            realized_pnl_gross=_decimal(raw.get("pnl", 0)),
            realized_pnl_net=_decimal(raw.get("pnl", 0)),
            fees_total=_decimal(raw.get("fees_total", 0)),
            spread_cost_total=_decimal(raw.get("spread_cost_total", 0)),
            slippage_cost_total=_decimal(raw.get("slippage_cost_total", 0)),
            risk_pct=_decimal(raw.get("risk_pct", 1)),
            auto_be=bool(raw.get("auto_be", True)),
            trailing_stop=bool(raw.get("trailing_stop", False)),
            favorable_extreme=(
                _decimal(raw.get("favorable_extreme"))
                if raw.get("favorable_extreme") not in (None, "")
                else None
            ),
            max_r_multiple=_decimal(raw.get("max_r_multiple", 0)),
            protection_stage=str(raw.get("protection_stage", "initial"))[:32],
            protection_updated_at=_timestamp(raw.get("protection_updated_at")),
            close_reason=str(raw.get("close_reason"))[:200] if raw.get("close_reason") else None,
            created_at=created,
            opened_at=created if opened_quantity > ZERO else None,
            closed_at=_timestamp(raw.get("closed_at")) if status == "closed" else None,
            updated_at=_timestamp(raw.get("closed_at")) or created,
            source_payload={"migration": "phase6-json-bootstrap-v1", "legacy": raw},
        )
        session.add(position)
        await session.flush()
        entry_order_id = uuid.uuid5(OMS_NAMESPACE, f"{trade_id}:entry")
        entry_status = {
            "pending": "submitted",
            "cancelled": "cancelled",
        }.get(normalized_status, "filled")
        entry_order = PaperOMSOrder(
            id=entry_order_id,
            account_id=account.id,
            position_id=position.id,
            client_order_id=str(raw.get("idempotency_key") or f"legacy:{trade_id}")[:100],
            leg="entry",
            position_effect="open",
            side="buy" if direction == "long" else "sell",
            order_type=position.order_type,
            status=entry_status,
            requested_quantity=quantity,
            filled_quantity=opened_quantity,
            remaining_quantity=quantity - opened_quantity,
            limit_price=entry,
            average_fill_price=entry if opened_quantity > ZERO else None,
            submitted_at=created,
            completed_at=created if entry_status == "filled" else None,
            cancelled_at=position.closed_at if entry_status == "cancelled" else None,
            source_payload={"migration": "phase6-json-bootstrap-v1"},
        )
        session.add(entry_order)
        await session.flush()
        if opened_quantity > ZERO:
            session.add(PaperOMSFill(
                id=uuid.uuid5(OMS_NAMESPACE, f"{trade_id}:entry-fill"),
                account_id=account.id,
                order_id=entry_order.id,
                position_id=position.id,
                execution_key=f"legacy:{trade_id}:entry"[:180],
                leg="entry",
                position_effect="open",
                side=entry_order.side,
                reference_price=entry,
                fill_price=entry,
                quantity=opened_quantity,
                fee=ZERO,
                spread_cost=ZERO,
                slippage_cost=ZERO,
                liquidity="migration",
                source="legacy_json",
                filled_at=position.opened_at or created,
                latency_ms=0,
                source_payload={"migration": "phase6-json-bootstrap-v1"},
            ))
        if status == "closed" and position.average_exit_price is not None:
            exit_order_id = uuid.uuid5(OMS_NAMESPACE, f"{trade_id}:exit")
            exit_order = PaperOMSOrder(
                id=exit_order_id,
                account_id=account.id,
                position_id=position.id,
                client_order_id=f"legacy:{trade_id}:exit"[:100],
                leg="exit",
                position_effect="reduce",
                side="sell" if direction == "long" else "buy",
                order_type="market",
                status="filled",
                requested_quantity=quantity,
                filled_quantity=quantity,
                remaining_quantity=ZERO,
                limit_price=None,
                average_fill_price=position.average_exit_price,
                close_reason=position.close_reason,
                submitted_at=position.closed_at or created,
                completed_at=position.closed_at or created,
                source_payload={"migration": "phase6-json-bootstrap-v1"},
            )
            session.add(exit_order)
            await session.flush()
            session.add(PaperOMSFill(
                id=uuid.uuid5(OMS_NAMESPACE, f"{trade_id}:exit-fill"),
                account_id=account.id,
                order_id=exit_order.id,
                position_id=position.id,
                execution_key=f"legacy:{trade_id}:exit"[:180],
                leg="exit",
                position_effect="reduce",
                side=exit_order.side,
                reference_price=position.average_exit_price,
                fill_price=position.average_exit_price,
                quantity=quantity,
                fee=ZERO,
                spread_cost=ZERO,
                slippage_cost=ZERO,
                liquidity="migration",
                source="legacy_json",
                filled_at=position.closed_at or created,
                latency_ms=0,
                source_payload={"migration": "phase6-json-bootstrap-v1"},
            ))
        # Explicitly persist the referenced aggregate/order/fill rows before
        # inserting the audit event. PostgreSQL enforces these FKs within the
        # same flush and mapper ordering alone is insufficient without ORM
        # relationships between these deliberately lean models.
        await session.flush()
        self._add_event(
            session,
            account_id=account.id,
            position_id=position.id,
            order_id=entry_order.id,
            event_key=f"legacy:{trade_id}:import",
            event_type="legacy_imported",
            previous_status=None,
            new_status=position.status,
            payload={"source": "paper_trades_store.json"},
        )
        return True

    async def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("symbol", "")).strip()
        direction = str(payload.get("direction", "")).lower()
        order_type = str(payload.get("order_type", "market")).lower()
        if not symbol or direction not in {"long", "short"} or order_type not in {"market", "limit"}:
            raise PaperOMSValidation("Invalid symbol, direction or order_type")
        quantity = _decimal(
            payload.get("position_size", payload.get("size", payload.get("qty"))),
            name="position_size",
            positive=True,
        )
        entry = _decimal(payload.get("entry"), name="entry", positive=True)
        stop_loss = _decimal(payload.get("stop_loss"), name="stop_loss", positive=True)
        take_profit = _decimal(payload.get("take_profit"), name="take_profit", positive=True)
        if direction == "long" and not (stop_loss < entry < take_profit):
            raise PaperOMSValidation("LONG requires stop_loss < entry < take_profit")
        if direction == "short" and not (take_profit < entry < stop_loss):
            raise PaperOMSValidation("SHORT requires take_profit < entry < stop_loss")

        cfg = get_settings()
        risk_pct = min(
            _decimal(payload.get("risk_pct", 1), name="risk_pct", positive=True),
            _decimal(cfg.default_risk_per_trade),
        )
        idempotency_key = str(payload.get("idempotency_key") or "").strip() or None
        now = _utcnow()
        publish: list[dict[str, Any]] = []
        async with self._session_factory() as session:
            try:
                account = await self._active_account(session, lock=True)
                if idempotency_key:
                    existing_order = (await session.execute(
                        select(PaperOMSOrder).where(
                            PaperOMSOrder.account_id == account.id,
                            PaperOMSOrder.client_order_id == idempotency_key,
                        )
                    )).scalar_one_or_none()
                    if existing_order is not None:
                        existing_position = await session.get(PaperOMSPosition, existing_order.position_id)
                        return await self._serialize_with_entry_order(session, existing_position)

                active_count = await session.scalar(
                    select(func.count(PaperOMSPosition.id)).where(
                        PaperOMSPosition.account_id == account.id,
                        PaperOMSPosition.status.in_(ACTIVE_POSITION_STATES),
                    )
                )
                if int(active_count or 0) >= cfg.max_open_positions:
                    raise PaperOMSConflict("Maximum open/pending position limit reached")
                positions = (await session.execute(
                    select(PaperOMSPosition).where(PaperOMSPosition.account_id == account.id)
                )).scalars().all()
                realized = sum((_decimal(item.realized_pnl_net) for item in positions), ZERO)
                equity = max(_decimal(account.initial_capital) + realized, ZERO)
                daily_realized = sum(
                    (
                        _decimal(item.realized_pnl_net)
                        for item in positions
                        if item.closed_at is not None
                        and item.closed_at.astimezone(timezone.utc).date() == now.date()
                    ),
                    ZERO,
                )
                daily_limit = _decimal(account.initial_capital) * _decimal(cfg.max_daily_loss) / Decimal("100")
                if daily_realized <= -daily_limit:
                    raise PaperOMSConflict("Daily loss limit reached; new orders are disabled")
                estimated_loss = abs(entry - stop_loss) * quantity
                allowed_risk = equity * risk_pct / Decimal("100")
                if estimated_loss > allowed_risk * Decimal("1.001"):
                    raise PaperOMSValidation(
                        f"Position risks {float(estimated_loss):.2f}, above allowed "
                        f"{float(allowed_risk):.2f} ({float(risk_pct):.2f}%)"
                    )
                if entry * quantity > equity * Decimal("5"):
                    raise PaperOMSValidation("Position notional exceeds the 5x paper leverage limit")

                position_id = str(uuid.uuid4())
                position = PaperOMSPosition(
                    id=position_id,
                    account_id=account.id,
                    symbol=symbol[:30],
                    direction=direction,
                    status="pending",
                    order_type=order_type,
                    exchange=str(payload.get("exchange", "binance"))[:30],
                    tag=str(payload.get("tag") or f"POS-{position_id[:8]}")[:100],
                    notes=str(payload.get("notes", ""))[:4000],
                    requested_quantity=quantity,
                    opened_quantity=ZERO,
                    closed_quantity=ZERO,
                    remaining_quantity=ZERO,
                    requested_entry_price=entry,
                    average_entry_price=None,
                    stop_loss=stop_loss,
                    initial_stop_loss=stop_loss,
                    take_profit=take_profit,
                    realized_pnl_gross=ZERO,
                    realized_pnl_net=ZERO,
                    fees_total=ZERO,
                    spread_cost_total=ZERO,
                    slippage_cost_total=ZERO,
                    risk_pct=risk_pct,
                    auto_be=bool(payload.get("auto_be", True)),
                    trailing_stop=bool(payload.get("trailing_stop", False)),
                    favorable_extreme=None,
                    max_r_multiple=ZERO,
                    protection_stage="initial",
                    protection_updated_at=None,
                    created_at=now,
                    updated_at=now,
                    source_payload={"phase": 6, "mode": "paper"},
                )
                session.add(position)
                await session.flush()
                order = PaperOMSOrder(
                    account_id=account.id,
                    position_id=position.id,
                    client_order_id=(idempotency_key or f"{position.id}:entry")[:100],
                    leg="entry",
                    position_effect="open",
                    side="buy" if direction == "long" else "sell",
                    order_type=order_type,
                    status="submitted",
                    requested_quantity=quantity,
                    filled_quantity=ZERO,
                    remaining_quantity=quantity,
                    limit_price=entry if order_type == "limit" else None,
                    submitted_at=now,
                    updated_at=now,
                    source_payload={"phase": 6, "mode": "paper"},
                )
                session.add(order)
                await session.flush()
                self._add_event(
                    session,
                    account_id=account.id,
                    position_id=position.id,
                    order_id=order.id,
                    event_key=f"order:{order.id}:submitted",
                    event_type="order_submitted",
                    previous_status=None,
                    new_status="submitted",
                    payload={"side": order.side, "position_effect": "open"},
                )

                quote = self._current_quote(symbol, fallback_price=entry)
                should_fill = order_type == "market" or self._is_marketable(order, quote)
                if should_fill:
                    fill_quantity = quantity if order_type == "market" else self._available_quantity(order, quote)
                    if fill_quantity > ZERO:
                        self._apply_fill(
                            session,
                            position,
                            order,
                            fill_quantity,
                            quote,
                            execution_key=f"api:{order.id}:{quote.get('sequence') or int(now.timestamp() * 1000)}",
                            liquidity="taker" if order_type == "market" else "maker",
                        )
                await session.commit()
                self._active_symbols.add(_norm_symbol(symbol))
                result = await self._serialize_by_id(position.id)
                publish.append(result)
            except Exception:
                await session.rollback()
                raise
        await self._after_mutation(publish)
        return publish[-1]

    async def cancel_entry_order(self, position_id: str, *, reason: str = "Order Cancelled") -> dict[str, Any]:
        async with self._session_factory() as session:
            try:
                account = await self._active_account(session, lock=True)
                position = await self._locked_position(session, account.id, position_id)
                order = await self._active_entry_order(session, account.id, position.id, lock=True)
                if order is None:
                    if position.status == "cancelled":
                        await session.rollback()
                        return await self._serialize_by_id(position_id)
                    raise PaperOMSConflict(
                        f"Entry order is no longer cancellable; position status is '{position.status}'"
                    )
                previous = order.status
                now = _utcnow()
                order.status = "cancelled"
                order.cancelled_at = now
                order.completed_at = now
                order.updated_at = now
                order.close_reason = reason[:200]
                order.version += 1
                if _decimal(position.opened_quantity) <= ZERO:
                    position.status = "cancelled"
                    position.close_reason = reason[:200]
                    position.closed_at = now
                else:
                    position.status = "open"
                position.updated_at = now
                position.version += 1
                self._add_event(
                    session,
                    account_id=account.id,
                    position_id=position.id,
                    order_id=order.id,
                    event_key=f"order:{order.id}:cancelled:{order.version}",
                    event_type="order_cancelled",
                    previous_status=previous,
                    new_status="cancelled",
                    payload={"unfilled_quantity": _float(order.remaining_quantity), "reason": reason},
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        result = await self._serialize_by_id(position_id)
        await self._after_mutation([result])
        return result

    async def close_position(
        self,
        position_id: str,
        *,
        close_price: float | Decimal | None = None,
        reason: str = "manual",
        quantity: float | Decimal | None = None,
        percentage: float | Decimal | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        if quantity is not None and percentage is not None:
            raise PaperOMSValidation("Supply quantity or percentage, not both")
        async with self._session_factory() as session:
            try:
                account = await self._active_account(session, lock=True)
                normalized_client_order_id = str(client_order_id or "").strip() or None
                if normalized_client_order_id:
                    existing = (await session.execute(
                        select(PaperOMSOrder).where(
                            PaperOMSOrder.account_id == account.id,
                            PaperOMSOrder.client_order_id == normalized_client_order_id,
                        )
                    )).scalar_one_or_none()
                    if existing is not None:
                        existing_position_id = existing.position_id
                        await session.rollback()
                        return await self._serialize_by_id(existing_position_id)
                position = await self._locked_position(session, account.id, position_id)
                active_entry = await self._active_entry_order(session, account.id, position.id, lock=True)
                if _decimal(position.opened_quantity) <= ZERO:
                    if active_entry is not None:
                        await session.rollback()
                        return await self.cancel_entry_order(position_id, reason=reason or "Order Cancelled")
                    raise PaperOMSConflict(f"Position is not open (status '{position.status}')")
                if position.status != "open" or _decimal(position.remaining_quantity) <= ZERO:
                    raise PaperOMSConflict(f"Position is not open (status '{position.status}')")
                if active_entry is not None:
                    now = _utcnow()
                    active_entry.status = "cancelled"
                    active_entry.cancelled_at = now
                    active_entry.completed_at = now
                    active_entry.updated_at = now
                    active_entry.close_reason = "Remaining entry quantity cancelled before reduce"
                    active_entry.version += 1

                remaining = _decimal(position.remaining_quantity)
                if percentage is not None:
                    pct = _decimal(percentage, name="percentage", positive=True)
                    if pct > Decimal("100"):
                        raise PaperOMSValidation("percentage must be <= 100")
                    close_quantity = remaining * pct / Decimal("100")
                elif quantity is not None:
                    close_quantity = _decimal(quantity, name="quantity", positive=True)
                else:
                    close_quantity = remaining
                close_quantity = min(close_quantity, remaining)
                if close_quantity <= EPSILON:
                    raise PaperOMSValidation("Close quantity is too small")

                order_id = (
                    normalized_client_order_id or f"{position.id}:exit:{uuid.uuid4().hex[:12]}"
                )[:100]
                now = _utcnow()
                exit_order = PaperOMSOrder(
                    account_id=account.id,
                    position_id=position.id,
                    client_order_id=order_id,
                    leg="exit",
                    position_effect="reduce",
                    side="sell" if position.direction == "long" else "buy",
                    order_type="market",
                    status="submitted",
                    requested_quantity=close_quantity,
                    filled_quantity=ZERO,
                    remaining_quantity=close_quantity,
                    close_reason=(reason or "manual")[:200],
                    submitted_at=now,
                    updated_at=now,
                    source_payload={"phase": 6, "reduce_only": True},
                )
                session.add(exit_order)
                await session.flush()
                quote = self._current_quote(
                    position.symbol,
                    fallback_price=_decimal(close_price) if close_price is not None else position.average_entry_price,
                )
                if close_price is not None:
                    quote = self._synthetic_quote(_decimal(close_price), source="manual_close_price")
                self._apply_fill(
                    session,
                    position,
                    exit_order,
                    close_quantity,
                    quote,
                    execution_key=f"api:{exit_order.id}:close",
                    liquidity="taker",
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        result = await self._serialize_by_id(position_id)
        await self._after_mutation([result])
        return result

    async def update_protection(
        self,
        position_id: str,
        *,
        stop_loss: float | Decimal | None = None,
        take_profit: float | Decimal | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        if stop_loss is None and take_profit is None and notes is None:
            raise PaperOMSValidation("No update fields supplied")
        async with self._session_factory() as session:
            try:
                account = await self._active_account(session, lock=True)
                position = await self._locked_position(session, account.id, position_id)
                if position.status != "open":
                    raise PaperOMSConflict("Only open Paper positions can be updated")
                entry = _decimal(position.average_entry_price or position.requested_entry_price)
                new_sl = _decimal(stop_loss, name="stop_loss", positive=True) if stop_loss is not None else _decimal(position.stop_loss)
                new_tp = _decimal(take_profit, name="take_profit", positive=True) if take_profit is not None else _decimal(position.take_profit)
                if position.direction == "long" and not (new_sl < new_tp and new_tp > entry):
                    raise PaperOMSValidation("Invalid LONG protection levels")
                if position.direction == "short" and not (new_tp < new_sl and new_tp < entry):
                    raise PaperOMSValidation("Invalid SHORT protection levels")
                previous = {"stop_loss": _float(position.stop_loss), "take_profit": _float(position.take_profit)}
                position.stop_loss = new_sl
                position.take_profit = new_tp
                if notes is not None:
                    position.notes = str(notes)[:4000]
                position.updated_at = _utcnow()
                position.version += 1
                self._add_event(
                    session,
                    account_id=account.id,
                    position_id=position.id,
                    order_id=None,
                    event_key=f"position:{position.id}:protection:{position.version}",
                    event_type="protection_updated",
                    previous_status="open",
                    new_status="open",
                    payload={
                        "previous": previous,
                        "stop_loss": _float(new_sl),
                        "take_profit": _float(new_tp),
                    },
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        result = await self._serialize_by_id(position_id)
        await self._after_mutation([result])
        return result

    async def update_audit(self, position_id: str, audit: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "ai_review",
            "review_source",
            "execution_rating",
            "lessons",
            "tags",
            "followed_plan",
        }
        clean = {key: value for key, value in audit.items() if key in allowed}
        async with self._session_factory() as session:
            try:
                account = await self._active_account(session, lock=True)
                position = await self._locked_position(session, account.id, position_id)
                if position.status != "closed":
                    raise PaperOMSConflict("Only closed Paper positions can be reviewed")
                payload = dict(position.source_payload or {})
                payload["audit"] = clean
                payload["reviewed_at"] = _utcnow().isoformat()
                position.source_payload = payload
                position.updated_at = _utcnow()
                position.version += 1
                self._add_event(
                    session,
                    account_id=account.id,
                    position_id=position.id,
                    order_id=None,
                    event_key=f"position:{position.id}:audit:{position.version}",
                    event_type="post_trade_reviewed",
                    previous_status="closed",
                    new_status="closed",
                    payload={"review_source": clean.get("review_source")},
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        result = await self._serialize_by_id(position_id)
        await self._after_mutation([result])
        return result

    async def list_fills(self, position_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            account = await self._active_account(session)
            exists = await session.scalar(
                select(PaperOMSPosition.id).where(
                    PaperOMSPosition.id == position_id,
                    PaperOMSPosition.account_id == account.id,
                )
            )
            if exists is None:
                raise PaperOMSNotFound("Paper position not found")
            fills = (await session.execute(
                select(PaperOMSFill)
                .where(
                    PaperOMSFill.account_id == account.id,
                    PaperOMSFill.position_id == position_id,
                )
                .order_by(PaperOMSFill.filled_at.asc())
            )).scalars().all()
        rows = [
            {
                "id": str(fill.id),
                "order_id": str(fill.order_id),
                "position_id": fill.position_id,
                "leg": fill.leg,
                "position_effect": fill.position_effect,
                "side": fill.side,
                "reference_price": _float(fill.reference_price),
                "fill_price": _float(fill.fill_price),
                "quantity": _float(fill.quantity),
                "fee": _float(fill.fee),
                "spread_cost": _float(fill.spread_cost),
                "slippage_cost": _float(fill.slippage_cost),
                "liquidity": fill.liquidity,
                "source": fill.source,
                "source_sequence": fill.source_sequence,
                "filled_at": _iso(fill.filled_at),
                "latency_ms": fill.latency_ms,
            }
            for fill in fills
        ]
        return {"total": len(rows), "fills": rows}

    async def list_positions(
        self, status: str | None = None, *, include_live: bool = True
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            account = await self._active_account(session)
            stmt = (
                select(PaperOMSPosition)
                .where(PaperOMSPosition.account_id == account.id)
                .order_by(PaperOMSPosition.created_at.desc())
            )
            if status:
                stmt = stmt.where(PaperOMSPosition.status == status)
            positions = (await session.execute(stmt)).scalars().all()
            trades = [await self._serialize_with_entry_order(session, item) for item in positions]
            for trade in trades:
                trade["currency"] = account.currency
        return {
            "total": len(trades),
            "trades": [self._with_live_pnl(item) for item in trades] if include_live else trades,
        }

    async def get_position(self, position_id: str) -> dict[str, Any]:
        return await self._serialize_by_id(position_id, include_live=True)

    async def account_snapshot(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            account = await self._active_account(session)
            positions = (await session.execute(
                select(PaperOMSPosition).where(PaperOMSPosition.account_id == account.id)
            )).scalars().all()
        realized_gross = sum((_decimal(p.realized_pnl_gross) for p in positions), ZERO)
        fees = sum((_decimal(p.fees_total) for p in positions), ZERO)
        unrealized = ZERO
        open_count = 0
        pending_count = 0
        for position in positions:
            if position.status == "pending":
                pending_count += 1
            if position.status != "open" or _decimal(position.remaining_quantity) <= ZERO:
                continue
            open_count += 1
            quote = self._current_quote(position.symbol, fallback_price=None)
            price = quote.get("bid") if position.direction == "long" else quote.get("ask")
            if price and position.average_entry_price:
                mark = _decimal(price)
                entry = _decimal(position.average_entry_price)
                qty = _decimal(position.remaining_quantity)
                unrealized += (mark - entry) * qty if position.direction == "long" else (entry - mark) * qty
        initial = _decimal(account.initial_capital)
        cash = initial + realized_gross - fees
        net_worth = cash + unrealized
        return {
            "broker_id": "paper",
            "broker": "Paper Trading Portfolio",
            "account_id": f"PAPER-{str(account.id)[:8].upper()}",
            "currency": account.currency,
            "initial_capital": round(_float(initial), 2),
            "cash": round(_float(cash), 2),
            "buying_power": round(max(_float(cash), 0.0), 2),
            "net_worth": round(_float(net_worth), 2),
            "total_equity": round(_float(net_worth), 2),
            "mode": "paper",
            "is_real": False,
            "asset_count": open_count,
            "realized_pnl": round(_float(realized_gross - fees), 2),
            "unrealized_pnl": round(_float(unrealized), 2),
            "total_pnl": round(_float(realized_gross - fees + unrealized), 2),
            "fees_total": round(_float(fees), 6),
            "closed_trades_count": sum(1 for p in positions if p.status == "closed"),
            "open_trades_count": open_count,
            "pending_orders_count": pending_count,
            "oms_authority": "postgresql",
        }

    async def reset_account(
        self,
        *,
        initial_capital: float,
        currency: str,
        clear_trades: bool,
    ) -> dict[str, Any]:
        capital = _decimal(initial_capital, name="initial_capital", positive=True)
        now = _utcnow()
        async with self._session_factory() as session:
            try:
                account = await self._active_account(session, lock=True)
                if clear_trades:
                    account.active = False
                    account.retired_at = now
                    await session.flush()
                    new_account = PaperOMSAccount(
                        initial_capital=capital,
                        currency=str(currency)[:8],
                        active=True,
                        created_at=now,
                    )
                    session.add(new_account)
                    await session.flush()
                    self._add_event(
                        session,
                        account_id=new_account.id,
                        position_id=None,
                        order_id=None,
                        event_key=f"account:{new_account.id}:created",
                        event_type="account_reset",
                        previous_status=None,
                        new_status="active",
                        payload={"previous_account_id": str(account.id)},
                    )
                else:
                    account.initial_capital = capital
                    account.currency = str(currency)[:8]
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        if self._config_path is not None:
            await asyncio.to_thread(write_json, self._config_path, {
                "initial_capital": float(capital),
                "currency": str(currency)[:8],
                "reset_at": now.isoformat(),
            })
        await self._refresh_active_symbols()
        await self._write_projection()
        return await self.account_snapshot()

    async def process_market_tick(self, quote: dict[str, Any]) -> list[dict[str, Any]]:
        """Apply one market event to eligible orders and protective exits."""
        raw_symbol = str(quote.get("symbol") or quote.get("norm_symbol") or "")
        symbol_key = _norm_symbol(raw_symbol)
        if not symbol_key or symbol_key not in self._active_symbols:
            return []
        price = _decimal(quote.get("price", 0), name="price")
        if price <= ZERO:
            return []
        changed_ids: set[str] = set()
        candidate_symbols = {
            raw_symbol,
            raw_symbol.upper(),
            raw_symbol.lower(),
            symbol_key,
            symbol_key.lower(),
            symbol_key.upper(),
        }
        async with self._session_factory() as session:
            try:
                account = await self._active_account(session, lock=True)
                order_refs = (await session.execute(
                    select(PaperOMSOrder.id, PaperOMSOrder.position_id)
                    .join(PaperOMSPosition, PaperOMSOrder.position_id == PaperOMSPosition.id)
                    .where(
                        PaperOMSOrder.account_id == account.id,
                        PaperOMSOrder.status.in_(ACTIVE_ORDER_STATES),
                        PaperOMSOrder.position_effect == "open",
                        PaperOMSPosition.symbol.in_(candidate_symbols),
                    )
                )).all()
                for order_id, position_id in order_refs:
                    position = await session.get(PaperOMSPosition, position_id, with_for_update=True)
                    if position is None:
                        continue
                    order = await session.get(PaperOMSOrder, order_id, with_for_update=True)
                    if order is None or order.status not in ACTIVE_ORDER_STATES:
                        continue
                    if not self._is_marketable(order, quote):
                        continue
                    sequence = quote.get("sequence")
                    event_stamp = int(float(quote.get("received_timestamp", quote.get("timestamp", 0))) * 1000)
                    execution_key = (
                        f"market:{order.id}:{quote.get('source', 'unknown')}:"
                        f"{sequence if sequence is not None else event_stamp}"
                    )[:180]
                    existing_fill = await session.scalar(
                        select(PaperOMSFill.id).where(PaperOMSFill.execution_key == execution_key)
                    )
                    if existing_fill is not None:
                        continue
                    fill_quantity = self._available_quantity(order, quote)
                    if fill_quantity <= ZERO:
                        continue
                    self._apply_fill(
                        session,
                        position,
                        order,
                        fill_quantity,
                        quote,
                        execution_key=execution_key,
                        liquidity="maker" if order.order_type == "limit" else "taker",
                    )
                    changed_ids.add(position.id)

                positions = (await session.execute(
                    select(PaperOMSPosition)
                    .where(
                        PaperOMSPosition.account_id == account.id,
                        PaperOMSPosition.status == "open",
                        PaperOMSPosition.symbol.in_(candidate_symbols),
                    )
                    .with_for_update()
                )).scalars().all()
                for position in positions:
                    if _decimal(position.remaining_quantity) <= ZERO:
                        continue
                    bid = _decimal(quote.get("bid", price))
                    ask = _decimal(quote.get("ask", price))
                    take_profit = _decimal(position.take_profit)
                    reason: str | None = None
                    trigger_price: Decimal | None = None

                    # A take-profit is terminal, so it wins before advancing a
                    # stop on the same tick. Otherwise protection is advanced
                    # transactionally before checking the new stop level.
                    if position.direction == "long":
                        if bid >= take_profit:
                            reason, trigger_price = "Take Profit (TP Hit) 🎯", take_profit
                    else:
                        if ask <= take_profit:
                            reason, trigger_price = "Take Profit (TP Hit) 🎯", take_profit

                    if reason is None:
                        executable_price = bid if position.direction == "long" else ask
                        if self._advance_trade_protection(session, position, executable_price):
                            changed_ids.add(position.id)
                        stop_loss = _decimal(position.stop_loss)
                        if position.direction == "long" and bid <= stop_loss:
                            trigger_price = stop_loss
                        elif position.direction == "short" and ask >= stop_loss:
                            trigger_price = stop_loss
                        if trigger_price is not None:
                            stage = str(position.protection_stage or "initial")
                            if stage == "breakeven":
                                reason = "Breakeven Shield Exit 🛡️"
                            elif stage.startswith("trailing"):
                                reason = "Trailing Stop (Profit Protected) 📈"
                            else:
                                reason = "Stop Loss (SL Hit) 🛑"
                    if reason is None or trigger_price is None:
                        continue
                    active_entry = await self._active_entry_order(session, account.id, position.id, lock=True)
                    if active_entry is not None:
                        active_entry.status = "cancelled"
                        active_entry.cancelled_at = _utcnow()
                        active_entry.completed_at = active_entry.cancelled_at
                        active_entry.updated_at = active_entry.cancelled_at
                        active_entry.close_reason = "Protective exit cancelled remaining entry quantity"
                        active_entry.version += 1
                    exit_order = PaperOMSOrder(
                        account_id=account.id,
                        position_id=position.id,
                        client_order_id=f"{position.id}:protect:{uuid.uuid4().hex[:12]}"[:100],
                        leg="exit",
                        position_effect="reduce",
                        side="sell" if position.direction == "long" else "buy",
                        order_type="market",
                        status="submitted",
                        requested_quantity=position.remaining_quantity,
                        filled_quantity=ZERO,
                        remaining_quantity=position.remaining_quantity,
                        close_reason=reason,
                        submitted_at=_utcnow(),
                        updated_at=_utcnow(),
                        source_payload={"phase": 6, "protective": True, "reduce_only": True},
                    )
                    session.add(exit_order)
                    await session.flush()
                    protective_quote = dict(quote)
                    protective_quote["price"] = float(trigger_price)
                    if position.direction == "long":
                        protective_quote["bid"] = float(trigger_price)
                    else:
                        protective_quote["ask"] = float(trigger_price)
                    self._apply_fill(
                        session,
                        position,
                        exit_order,
                        _decimal(position.remaining_quantity),
                        protective_quote,
                        execution_key=f"protect:{exit_order.id}:{quote.get('sequence') or int(_utcnow().timestamp()*1000)}",
                        liquidity="taker",
                    )
                    changed_ids.add(position.id)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        changed = [await self._serialize_by_id(item) for item in sorted(changed_ids)]
        if changed:
            await self._after_mutation(changed)
        return changed

    def _advance_trade_protection(
        self,
        session: AsyncSession,
        position: PaperOMSPosition,
        executable_price: Decimal,
    ) -> bool:
        """Advance Auto-BE / trailing protection without ever loosening SL.

        This runs in the same row-locked transaction as protective exits, so a
        fast +1R touch cannot be missed by the slower background scanner.
        """
        if not position.auto_be and not position.trailing_stop:
            return False
        entry = _decimal(position.average_entry_price or position.requested_entry_price)
        initial_stop = _decimal(position.initial_stop_loss)
        risk = abs(entry - initial_stop)
        if entry <= ZERO or risk <= EPSILON:
            return False

        previous_extreme = _decimal(position.favorable_extreme or entry)
        if position.direction == "long":
            extreme = max(previous_extreme, executable_price)
            r_multiple = (extreme - entry) / risk
        else:
            extreme = min(previous_extreme, executable_price)
            r_multiple = (entry - extreme) / risk
        if r_multiple <= ZERO:
            return False

        cfg = get_settings()
        current_sl = _decimal(position.stop_loss)
        candidate = current_sl
        stage = str(position.protection_stage or "initial")
        note = ""

        if position.trailing_stop and r_multiple >= Decimal("2.5"):
            candidate = (
                extreme - risk * Decimal("0.8")
                if position.direction == "long"
                else extreme + risk * Decimal("0.8")
            )
            stage = "trailing_dynamic"
            note = "Dynamic trail: 0.8R behind favorable extreme"
        elif position.trailing_stop and r_multiple >= Decimal("2.0"):
            candidate = (
                entry + risk * Decimal("1.2")
                if position.direction == "long"
                else entry - risk * Decimal("1.2")
            )
            stage = "trailing_2_0r"
            note = "Trailing tier 2.0R: locked +1.2R"
        elif position.trailing_stop and r_multiple >= Decimal("1.5"):
            candidate = (
                entry + risk * Decimal("0.6")
                if position.direction == "long"
                else entry - risk * Decimal("0.6")
            )
            stage = "trailing_1_5r"
            note = "Trailing tier 1.5R: locked +0.6R"
        elif position.auto_be and r_multiple >= _decimal(cfg.paper_oms_auto_be_trigger_r):
            # Entry already includes entry-side spread/slippage. Move the stop
            # far enough to offset accumulated entry fee plus modeled exit fee
            # and adverse exit slippage, not merely to the nominal entry.
            opened = max(_decimal(position.opened_quantity), EPSILON)
            entry_fee_per_unit = _decimal(position.fees_total) / opened
            fee_rate = _decimal(cfg.paper_oms_fee_bps) / Decimal("10000")
            slippage_rate = _decimal(cfg.paper_oms_slippage_bps) / Decimal("10000")
            if position.direction == "long":
                candidate = (entry + entry_fee_per_unit) / (
                    (Decimal("1") - slippage_rate) * (Decimal("1") - fee_rate)
                )
            else:
                candidate = (entry - entry_fee_per_unit) / (
                    (Decimal("1") + slippage_rate) * (Decimal("1") + fee_rate)
                )
            stage = "breakeven"
            note = "Auto-Breakeven: modeled fee/slippage covered"
        else:
            return False

        take_profit = _decimal(position.take_profit)
        if position.direction == "long":
            candidate = min(candidate, take_profit - EPSILON)
            improves = candidate > current_sl
        else:
            candidate = max(candidate, take_profit + EPSILON)
            improves = candidate < current_sl
        if not improves:
            return False

        min_step = risk * _decimal(cfg.paper_oms_trailing_min_step_r)
        stage_changed = stage != str(position.protection_stage or "initial")
        if not stage_changed and abs(candidate - current_sl) < min_step:
            return False

        previous = {
            "stop_loss": _float(current_sl),
            "stage": str(position.protection_stage or "initial"),
            "max_r_multiple": _float(position.max_r_multiple),
        }
        now = _utcnow()
        position.stop_loss = candidate
        position.favorable_extreme = extreme
        position.max_r_multiple = max(_decimal(position.max_r_multiple), r_multiple)
        position.protection_stage = stage
        position.protection_updated_at = now
        position.updated_at = now
        position.version += 1
        self._add_event(
            session,
            account_id=position.account_id,
            position_id=position.id,
            order_id=None,
            event_key=f"position:{position.id}:auto-protection:{position.version}",
            event_type="protection_advanced",
            previous_status="open",
            new_status="open",
            payload={
                "previous": previous,
                "stop_loss": _float(candidate),
                "stage": stage,
                "r_multiple": _float(r_multiple),
                "favorable_extreme": _float(extreme),
                "note": note,
            },
        )
        return True

    def _apply_fill(
        self,
        session: AsyncSession,
        position: PaperOMSPosition,
        order: PaperOMSOrder,
        quantity: Decimal,
        quote: dict[str, Any],
        *,
        execution_key: str,
        liquidity: str,
    ) -> PaperOMSFill:
        remaining = _decimal(order.remaining_quantity)
        quantity = min(_decimal(quantity, name="fill quantity", positive=True), remaining)
        if quantity <= ZERO or order.status not in ACTIVE_ORDER_STATES:
            raise PaperOMSConflict("Order is no longer fillable")
        cfg = get_settings()
        mid = _decimal(quote.get("price"), name="market price", positive=True)
        fallback_half_spread = _decimal(cfg.paper_oms_spread_bps) / Decimal("20000")
        bid = _decimal(quote.get("bid") or mid * (Decimal("1") - fallback_half_spread))
        ask = _decimal(quote.get("ask") or mid * (Decimal("1") + fallback_half_spread))
        reference = ask if order.side == "buy" else bid
        slippage_rate = _decimal(cfg.paper_oms_slippage_bps) / Decimal("10000")
        fill_price = reference * (Decimal("1") + slippage_rate if order.side == "buy" else Decimal("1") - slippage_rate)
        if order.order_type == "limit" and order.limit_price is not None:
            limit = _decimal(order.limit_price)
            fill_price = min(fill_price, limit) if order.side == "buy" else max(fill_price, limit)
        spread_cost = abs(reference - mid) * quantity
        slippage_cost = abs(fill_price - reference) * quantity
        fee = abs(fill_price * quantity) * _decimal(cfg.paper_oms_fee_bps) / Decimal("10000")
        now = _utcnow()
        previous_order_status = order.status
        previous_filled = _decimal(order.filled_quantity)
        new_filled = previous_filled + quantity
        order.average_fill_price = (
            (previous_filled * _decimal(order.average_fill_price or 0) + quantity * fill_price) / new_filled
        )
        order.filled_quantity = new_filled
        order.remaining_quantity = max(_decimal(order.requested_quantity) - new_filled, ZERO)
        order.status = "filled" if _decimal(order.remaining_quantity) <= EPSILON else "partially_filled"
        if order.status == "filled":
            order.remaining_quantity = ZERO
            order.completed_at = now
        order.updated_at = now
        order.version += 1

        if order.position_effect == "open":
            previous_opened = _decimal(position.opened_quantity)
            new_opened = previous_opened + quantity
            position.average_entry_price = (
                (previous_opened * _decimal(position.average_entry_price or 0) + quantity * fill_price) / new_opened
            )
            position.opened_quantity = new_opened
            position.remaining_quantity = new_opened - _decimal(position.closed_quantity)
            position.status = "open"
            position.opened_at = position.opened_at or now
        else:
            available = _decimal(position.remaining_quantity)
            if quantity > available + EPSILON:
                raise PaperOMSConflict("Reduce order exceeds the open Paper quantity")
            entry = _decimal(position.average_entry_price, name="average_entry_price", positive=True)
            previous_closed = _decimal(position.closed_quantity)
            new_closed = previous_closed + quantity
            position.average_exit_price = (
                (previous_closed * _decimal(position.average_exit_price or 0) + quantity * fill_price) / new_closed
            )
            gross_delta = (fill_price - entry) * quantity if position.direction == "long" else (entry - fill_price) * quantity
            position.realized_pnl_gross = _decimal(position.realized_pnl_gross) + gross_delta
            position.closed_quantity = new_closed
            position.remaining_quantity = max(_decimal(position.opened_quantity) - new_closed, ZERO)
            position.close_reason = order.close_reason
            if _decimal(position.remaining_quantity) <= EPSILON:
                position.remaining_quantity = ZERO
                position.status = "closed"
                position.closed_at = now
            else:
                position.status = "open"

        position.fees_total = _decimal(position.fees_total) + fee
        position.spread_cost_total = _decimal(position.spread_cost_total) + spread_cost
        position.slippage_cost_total = _decimal(position.slippage_cost_total) + slippage_cost
        position.realized_pnl_net = _decimal(position.realized_pnl_gross) - _decimal(position.fees_total)
        position.updated_at = now
        position.version += 1
        source_sequence = str(quote.get("sequence")) if quote.get("sequence") is not None else None
        fill = PaperOMSFill(
            account_id=position.account_id,
            order_id=order.id,
            position_id=position.id,
            execution_key=execution_key[:180],
            leg=order.leg,
            position_effect=order.position_effect,
            side=order.side,
            reference_price=reference,
            fill_price=fill_price,
            quantity=quantity,
            fee=fee,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            liquidity=liquidity[:16],
            source=str(quote.get("source", "paper_model"))[:40],
            source_sequence=source_sequence,
            exchange_timestamp=_timestamp(quote.get("exchange_timestamp")),
            received_timestamp=_timestamp(quote.get("received_timestamp") or quote.get("timestamp")),
            filled_at=now,
            latency_ms=max(0, int(quote.get("latency_ms", 0) or 0)),
            source_payload={
                "data_quality": quote.get("data_quality"),
                "transport": quote.get("transport"),
                "aggressor_side": quote.get("aggressor_side"),
            },
        )
        session.add(fill)
        self._add_event(
            session,
            account_id=position.account_id,
            position_id=position.id,
            order_id=order.id,
            event_key=f"{execution_key}:event"[:180],
            event_type=("order_filled" if order.status == "filled" else "order_partially_filled"),
            previous_status=previous_order_status,
            new_status=order.status,
            payload={
                "quantity": _float(quantity),
                "fill_price": _float(fill_price),
                "fee": _float(fee),
                "position_effect": order.position_effect,
                "position_status": position.status,
            },
        )
        return fill

    def _add_event(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        position_id: str | None,
        order_id: uuid.UUID | None,
        event_key: str,
        event_type: str,
        previous_status: str | None,
        new_status: str | None,
        payload: dict[str, Any],
    ) -> None:
        session.add(PaperOMSEvent(
            account_id=account_id,
            position_id=position_id,
            order_id=order_id,
            event_key=event_key[:180],
            event_type=event_type[:40],
            previous_status=previous_status,
            new_status=new_status,
            payload=payload,
        ))

    async def _locked_position(
        self, session: AsyncSession, account_id: uuid.UUID, position_id: str
    ) -> PaperOMSPosition:
        position = (await session.execute(
            select(PaperOMSPosition)
            .where(
                PaperOMSPosition.id == position_id,
                PaperOMSPosition.account_id == account_id,
            )
            .with_for_update()
        )).scalar_one_or_none()
        if position is None:
            raise PaperOMSNotFound("Paper position not found")
        return position

    async def _active_entry_order(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        position_id: str,
        *,
        lock: bool,
    ) -> PaperOMSOrder | None:
        stmt = select(PaperOMSOrder).where(
            PaperOMSOrder.account_id == account_id,
            PaperOMSOrder.position_id == position_id,
            PaperOMSOrder.position_effect == "open",
            PaperOMSOrder.status.in_(ACTIVE_ORDER_STATES),
        )
        if lock:
            stmt = stmt.with_for_update()
        return (await session.execute(stmt)).scalars().first()

    def _synthetic_quote(self, price: Decimal, *, source: str) -> dict[str, Any]:
        cfg = get_settings()
        half_spread = _decimal(cfg.paper_oms_spread_bps) / Decimal("20000")
        now = _utcnow().timestamp()
        return {
            "price": float(price),
            "bid": float(price * (Decimal("1") - half_spread)),
            "ask": float(price * (Decimal("1") + half_spread)),
            "source": source,
            "transport": "paper_model",
            "data_quality": "modeled",
            "timestamp": now,
            "received_timestamp": now,
            "latency_ms": 0,
        }

    def _current_quote(
        self, symbol: str, *, fallback_price: Decimal | Any | None
    ) -> dict[str, Any]:
        from app.engines.price_hub import price_hub

        ticker = price_hub.get_ticker(symbol)
        if ticker and not ticker.get("is_stale") and _float(ticker.get("price")) > 0:
            return ticker
        if fallback_price is None:
            return {}
        price = _decimal(fallback_price, name="fallback price", positive=True)
        return self._synthetic_quote(price, source="paper_fallback")

    def _is_marketable(self, order: PaperOMSOrder, quote: dict[str, Any]) -> bool:
        if order.order_type == "market":
            return True
        if order.limit_price is None or not quote:
            return False
        limit = _decimal(order.limit_price)
        price = _decimal(
            quote.get("ask" if order.side == "buy" else "bid") or quote.get("price", 0)
        )
        if price <= ZERO:
            return False
        return price <= limit if order.side == "buy" else price >= limit

    def _available_quantity(self, order: PaperOMSOrder, quote: dict[str, Any]) -> Decimal:
        cfg = get_settings()
        remaining = _decimal(order.remaining_quantity)
        if order.order_type == "market":
            return remaining
        aggressor = str(quote.get("aggressor_side", "")).lower()
        aggregated_field = "sell_trade_quantity" if order.side == "buy" else "buy_trade_quantity"
        aggregated_quantity = _decimal(quote.get(aggregated_field) or 0)
        trade_quantity = _decimal(quote.get("last_trade_quantity") or 0)
        relevant_trade = (
            (order.side == "buy" and aggressor == "sell")
            or (order.side == "sell" and aggressor == "buy")
        )
        participation = _decimal(cfg.paper_oms_max_volume_participation)
        available = aggregated_quantity * participation
        if available <= ZERO and relevant_trade and trade_quantity > ZERO:
            available = trade_quantity * participation
        if available <= ZERO:
            book_field = "ask_quantity" if order.side == "buy" else "bid_quantity"
            book_quantity = _decimal(quote.get(book_field) or 0)
            if book_quantity > ZERO:
                available = book_quantity * participation
        if available <= ZERO:
            available = remaining * _decimal(cfg.paper_oms_fallback_partial_fill_ratio)
        return min(max(available, ZERO), remaining)

    async def _serialize_by_id(
        self, position_id: str, *, include_live: bool = False
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            account = await self._active_account(session)
            position = (await session.execute(
                select(PaperOMSPosition).where(
                    PaperOMSPosition.id == position_id,
                    PaperOMSPosition.account_id == account.id,
                )
            )).scalar_one_or_none()
            if position is None:
                raise PaperOMSNotFound("Paper position not found")
            result = await self._serialize_with_entry_order(session, position)
            result["currency"] = account.currency
        return self._with_live_pnl(result) if include_live else result

    async def _serialize_with_entry_order(
        self, session: AsyncSession, position: PaperOMSPosition
    ) -> dict[str, Any]:
        entry_order = (await session.execute(
            select(PaperOMSOrder)
            .where(
                PaperOMSOrder.position_id == position.id,
                PaperOMSOrder.position_effect == "open",
            )
            .order_by(PaperOMSOrder.submitted_at.asc())
            .limit(1)
        )).scalar_one_or_none()
        requested = _float(position.requested_quantity)
        opened = _float(position.opened_quantity)
        closed = _float(position.closed_quantity)
        remaining = _float(position.remaining_quantity)
        display_size = remaining if position.status == "open" else requested
        entry = _float(position.average_entry_price or position.requested_entry_price)
        initial_distance = abs(entry - _float(position.initial_stop_loss))
        pnl = _float(position.realized_pnl_net)
        denominator = _float(position.average_entry_price or position.requested_entry_price) * max(opened, EPSILON.__float__())
        result = {
            "id": position.id,
            "mode": "paper",
            "broker": "paper",
            "currency": "USD",
            "symbol": position.symbol,
            "direction": position.direction,
            "status": position.status,
            "order_type": position.order_type,
            "exchange": position.exchange,
            "tag": position.tag,
            "notes": position.notes,
            "entry": entry,
            "requested_entry_price": _float(position.requested_entry_price),
            "fill_price": _float(position.average_entry_price) if position.average_entry_price else None,
            "stop_loss": _float(position.stop_loss),
            "initial_stop_loss": _float(position.initial_stop_loss),
            "initial_sl_dist": initial_distance,
            "take_profit": _float(position.take_profit),
            "position_size": display_size,
            "size": display_size,
            "qty": display_size,
            "requested_quantity": requested,
            "filled_quantity": opened,
            "closed_quantity": closed,
            "remaining_quantity": remaining,
            "entry_order_status": entry_order.status if entry_order else None,
            "entry_order_remaining_quantity": _float(entry_order.remaining_quantity) if entry_order else 0.0,
            "opened_at": _iso(position.opened_at or position.created_at),
            "filled_at": _iso(position.opened_at),
            "closed_at": _iso(position.closed_at),
            "close_price": _float(position.average_exit_price) if position.average_exit_price else None,
            "close_reason": position.close_reason,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / denominator * 100, 2) if denominator > 0 else 0.0,
            "realized_pnl_gross": round(_float(position.realized_pnl_gross), 6),
            "realized_pnl_net": round(pnl, 6),
            "fees_total": round(_float(position.fees_total), 8),
            "spread_cost_total": round(_float(position.spread_cost_total), 8),
            "slippage_cost_total": round(_float(position.slippage_cost_total), 8),
            "risk_pct": _float(position.risk_pct),
            "estimated_risk": round(initial_distance * requested, 6),
            "auto_be": position.auto_be,
            "trailing_stop": position.trailing_stop,
            "be_triggered": str(position.protection_stage or "initial") != "initial",
            "favorable_extreme": _float(position.favorable_extreme) if position.favorable_extreme else None,
            "max_r_multiple": round(_float(position.max_r_multiple), 4),
            "protection_stage": position.protection_stage,
            "protection_updated_at": _iso(position.protection_updated_at),
            "oms_authority": "postgresql",
            "oms_version": position.version,
        }
        audit = dict(position.source_payload or {}).get("audit")
        if isinstance(audit, dict):
            result.update(audit)
            result["reviewed_at"] = dict(position.source_payload or {}).get("reviewed_at")
        return result

    def _with_live_pnl(self, trade: dict[str, Any]) -> dict[str, Any]:
        result = dict(trade)
        if result.get("status") != "open":
            return result
        quote = self._current_quote(result["symbol"], fallback_price=None)
        price = quote.get("bid") if result.get("direction") == "long" else quote.get("ask")
        if not price:
            result.update({
                "live_price": None,
                "live_pnl": None,
                "live_pnl_pct": None,
                "price_status": "unavailable",
            })
            return result
        entry = _float(result.get("entry"))
        quantity = _float(result.get("remaining_quantity"))
        mark = _float(price)
        pnl = (mark - entry) * quantity if result.get("direction") == "long" else (entry - mark) * quantity
        result.update({
            "live_price": mark,
            "live_pnl": round(pnl, 2),
            "live_pnl_pct": round((pnl / (entry * quantity)) * 100, 2) if entry > 0 and quantity > 0 else 0.0,
            "price_status": "live" if quote.get("transport") == "websocket" else "fallback",
        })
        return result

    async def _refresh_active_symbols(self) -> None:
        async with self._session_factory() as session:
            account = await self._active_account(session)
            symbols = (await session.execute(
                select(PaperOMSPosition.symbol).where(
                    PaperOMSPosition.account_id == account.id,
                    PaperOMSPosition.status.in_(ACTIVE_POSITION_STATES),
                )
            )).scalars().all()
        self._active_symbols = {_norm_symbol(symbol) for symbol in symbols}

    async def active_symbol_names(self) -> list[str]:
        async with self._session_factory() as session:
            account = await self._active_account(session)
            symbols = (await session.execute(
                select(PaperOMSPosition.symbol).where(
                    PaperOMSPosition.account_id == account.id,
                    PaperOMSPosition.status.in_(ACTIVE_POSITION_STATES),
                ).distinct()
            )).scalars().all()
        return list(symbols)

    def _enqueue_tick(self, quote: dict[str, Any]) -> None:
        if not self.running or not self.ready:
            return
        key = _norm_symbol(str(quote.get("symbol") or quote.get("norm_symbol") or ""))
        if key not in self._active_symbols:
            return
        previous = self._latest_ticks.get(key, {})
        merged = {**previous, **quote}
        aggressor = str(quote.get("aggressor_side", "")).lower()
        quantity = _float(quote.get("last_trade_quantity"))
        if aggressor in {"buy", "sell"} and quantity > 0:
            aggregate_field = f"{aggressor}_trade_quantity"
            merged[aggregate_field] = _float(previous.get(aggregate_field)) + quantity
        self._latest_ticks[key] = merged
        self._tick_event.set()

    async def _tick_worker(self) -> None:
        while self.running:
            try:
                await self._tick_event.wait()
                self._tick_event.clear()
                batch = list(self._latest_ticks.values())
                self._latest_ticks.clear()
                for quote in batch:
                    try:
                        await self.process_market_tick(quote)
                    except Exception as exc:
                        self.last_error = f"{type(exc).__name__}: {exc}"
                        logger.error(
                            "[PaperOMS] Market event failed for symbol {}: {}",
                            quote.get("symbol") or quote.get("norm_symbol"),
                            exc,
                            exc_info=True,
                        )
                        await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break

    async def _write_projection(self) -> None:
        if self._projection_path is None:
            return
        async with self._projection_lock:
            snapshot = await self.list_positions(include_live=False)
            data = {item["id"]: item for item in snapshot["trades"]}
            await asyncio.to_thread(write_json, self._projection_path, data)
            from app.services.ledger_migration import ledger_mirror

            ledger_mirror.enqueue_snapshot(data)

    async def _after_mutation(self, changed: list[dict[str, Any]]) -> None:
        await self._refresh_active_symbols()
        if self._subscribed:
            from app.engines.price_hub import price_hub

            for trade in changed:
                if trade.get("status") in ACTIVE_POSITION_STATES:
                    price_hub.register_symbol(str(trade.get("symbol", "")))
        await self._write_projection()
        try:
            from app.api.ws import broadcast

            for trade in changed:
                event_type = "trade_closed" if trade.get("status") == "closed" else "trade_updated"
                await broadcast({"type": event_type, "data": trade})
        except Exception as exc:
            logger.debug("[PaperOMS] WebSocket publish failed: {}", exc)


paper_oms = PaperOMS()
