"""
Trades API
Manage paper / live trade orders and open position tracking.
"""

from __future__ import annotations

import asyncio
import math
import threading
from typing import Literal, Optional
from uuid import uuid4
from datetime import datetime, timezone
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from loguru import logger

from app.core.security import verify_api_key
from app.core.config import get_settings
from app.engines.market_data import MarketDataEngine
from app.engines.execution_engine import PaperExecutionEngine, LiveExecutionEngine
from app.core.json_store import JsonStoreCorruptionError, read_json, write_json
from app.core.live_session import LiveSession, require_live_session, live_session_manager
from app.services.paper_oms import (
    PaperOMSConflict,
    PaperOMSError,
    PaperOMSNotFound,
    PaperOMSValidation,
    paper_oms,
)

from pathlib import Path

router = APIRouter()
_paper_execution = PaperExecutionEngine()
_live_execution = LiveExecutionEngine()
_market_data = MarketDataEngine()

PAPER_CONFIG_FILE = Path(__file__).parent.parent.parent / "config" / "paper_portfolio.json"
LEGACY_TRADES_STORE_FILE = Path(__file__).parent.parent.parent / "config" / "trades_store.json"
PAPER_TRADES_STORE_FILE = Path(__file__).parent.parent.parent / "config" / "paper_trades_store.json"
LIVE_TRADES_STORE_FILE = Path(__file__).parent.parent.parent / "config" / "live_trades_store.json"
# Compatibility alias used by older tests/extensions. It now always points at
# the paper ledger and can never contain live records.
TRADES_STORE_FILE = PAPER_TRADES_STORE_FILE
_trade_store_lock = threading.RLock()


def _paper_oms_available() -> bool:
    """Use JSON only in isolated unit tests that do not run app lifespan."""
    if paper_oms.ready:
        return True
    if paper_oms.startup_attempted:
        raise HTTPException(status_code=503, detail="Paper OMS is unavailable")
    return False


def _raise_paper_oms_http(exc: PaperOMSError) -> None:
    if isinstance(exc, PaperOMSNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PaperOMSConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, PaperOMSValidation):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=503, detail="Paper OMS operation failed") from exc


def _read_partition(path: Path, mode: Literal["paper", "live"]) -> dict[str, dict]:
    data = read_json(path, dict)
    if not isinstance(data, dict):
        raise JsonStoreCorruptionError(f"{mode.title()} trade store root must be a JSON object")
    result: dict[str, dict] = {}
    for trade_id, raw_trade in data.items():
        if not isinstance(raw_trade, dict):
            continue
        trade = dict(raw_trade)
        record_mode = str(trade.get("mode", "paper")).lower()
        if record_mode != mode:
            continue
        trade["mode"] = mode
        result[str(trade_id)] = trade
    return result

def _load_trades() -> dict[str, dict]:
    """Load isolated ledgers, importing legacy mixed data only when needed."""
    paper_exists = TRADES_STORE_FILE.exists()
    live_exists = LIVE_TRADES_STORE_FILE.exists()
    paper = _read_partition(TRADES_STORE_FILE, "paper") if paper_exists else {}
    live = _read_partition(LIVE_TRADES_STORE_FILE, "live") if live_exists else {}

    if (not paper_exists or not live_exists) and LEGACY_TRADES_STORE_FILE.exists():
        legacy = read_json(LEGACY_TRADES_STORE_FILE, dict)
        if not isinstance(legacy, dict):
            raise JsonStoreCorruptionError("Legacy trade store root must be a JSON object")
        for trade_id, raw_trade in legacy.items():
            if not isinstance(raw_trade, dict):
                continue
            trade = dict(raw_trade)
            mode = "live" if str(trade.get("mode", "paper")).lower() == "live" else "paper"
            trade["mode"] = mode
            target = live if mode == "live" else paper
            if (mode == "paper" and not paper_exists) or (mode == "live" and not live_exists):
                target.setdefault(str(trade_id), trade)
    duplicate_ids = set(paper).intersection(live)
    if duplicate_ids:
        raise JsonStoreCorruptionError(
            f"Trade IDs exist in both Paper and Live ledgers: {sorted(duplicate_ids)[:3]}"
        )
    return {**paper, **live}


def _partition_trades(trades: dict[str, dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    paper: dict[str, dict] = {}
    live: dict[str, dict] = {}
    for trade_id, raw_trade in trades.items():
        trade = dict(raw_trade)
        mode = "live" if str(trade.get("mode", "paper")).lower() == "live" else "paper"
        trade["mode"] = mode
        (live if mode == "live" else paper)[str(trade_id)] = trade
    return paper, live


def _save_trades_sync():
    paper, live = _partition_trades(_trades)
    write_json(TRADES_STORE_FILE, paper)
    write_json(LIVE_TRADES_STORE_FILE, live)


async def _save_trades_async():
    await asyncio.to_thread(_save_trades_sync)


_save_trades = _save_trades_sync

_trades: dict[str, dict] = _load_trades()


def _mutate_trades(mutator):
    global _trades
    with _trade_store_lock:
        current = _load_trades()
        result = mutator(current)
        _trades = current
        _save_trades_sync()
        # Phase 3 mirrors the compatibility JSON ledger asynchronously. The
        # mirror owns no broker client and cannot cross the Paper/Live boundary.
        from app.services.ledger_migration import ledger_mirror
        ledger_mirror.enqueue_snapshot(current)
        return result


def get_all_trades() -> dict[str, dict]:
    global _trades
    with _trade_store_lock:
        _trades = _load_trades()
        return dict(_trades)


def auto_close_trade_sync(trade_id: str, reason: str, close_price: float) -> Optional[dict]:
    def mutate(trades: dict[str, dict]) -> Optional[dict]:
        trade = trades.get(trade_id)
        if not trade or trade.get("mode", "paper") != "paper" or trade.get("status") != "open":
            return None
        try:
            entry = float(trade["entry"])
            direction = str(trade["direction"]).lower()
            size = float(trade.get("size", trade.get("position_size")))
        except (KeyError, TypeError, ValueError):
            return None
        if entry <= 0 or size <= 0 or direction not in {"long", "short"}:
            return None
        if direction == "long":
            pnl_pct = ((close_price - entry) / entry) * 100 if entry > 0 else 0.0
            pnl = (close_price - entry) * size
        else:
            pnl_pct = ((entry - close_price) / entry) * 100 if entry > 0 else 0.0
            pnl = (entry - close_price) * size
        trade.update({
            "status": "closed",
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "close_price": round(close_price, 6),
            "close_reason": reason,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })
        return dict(trade)

    return _mutate_trades(mutate)


def update_trade_sl_sync(trade_id: str, new_sl: float, note: str = "") -> Optional[dict]:
    def mutate(trades: dict[str, dict]) -> Optional[dict]:
        trade = trades.get(trade_id)
        if not trade or trade.get("mode", "paper") != "paper" or trade.get("status") != "open":
            return None
        trade["stop_loss"] = round(float(new_sl), 6)
        if note:
            trade["sl_note"] = note
        trade["sl_updated_at"] = datetime.now(timezone.utc).isoformat()
        return dict(trade)

    return _mutate_trades(mutate)


def fill_pending_trade_sync(trade_id: str, fill_price: float) -> Optional[dict]:
    """Atomically transition a still-pending order to open."""
    def mutate(trades: dict[str, dict]) -> Optional[dict]:
        trade = trades.get(trade_id)
        if not trade or trade.get("mode", "paper") != "paper" or trade.get("status") != "pending":
            return None
        trade["status"] = "open"
        trade["filled_at"] = datetime.now(timezone.utc).isoformat()
        trade["fill_price"] = round(float(fill_price), 6)
        return dict(trade)

    return _mutate_trades(mutate)


def update_trade_audit_sync(trade_id: str, audit: dict) -> Optional[dict]:
    """Atomically attach bounded review metadata to a closed trade."""
    allowed = {"ai_review", "review_source", "execution_rating", "lessons", "tags", "followed_plan"}
    clean = {key: value for key, value in audit.items() if key in allowed}

    def mutate(trades: dict[str, dict]) -> Optional[dict]:
        trade = trades.get(trade_id)
        if not trade or trade.get("status") != "closed":
            return None
        trade.update(clean)
        trade["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        return dict(trade)

    return _mutate_trades(mutate)


def _load_paper_config() -> dict:
    data = read_json(
        PAPER_CONFIG_FILE,
        lambda: {"initial_capital": 100000.0, "currency": "USD"},
    )
    return data if isinstance(data, dict) else {"initial_capital": 100000.0, "currency": "USD"}


def _save_paper_config(data: dict):
    write_json(PAPER_CONFIG_FILE, data)


class ResetAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_capital: float = Field(default=100000.0, ge=100, le=1_000_000_000, allow_inf_nan=False)
    clear_trades: bool = True
    currency: Literal["USD", "THB"] = "USD"


@router.post("/account/reset")
async def reset_paper_account(req: ResetAccountRequest, _key: str = Depends(verify_api_key)):
    """Reset Paper Trading initial capital and optionally clear paper trade history."""
    init_cap = float(req.initial_capital)
    if _paper_oms_available():
        try:
            return await paper_oms.reset_account(
                initial_capital=init_cap,
                currency=req.currency,
                clear_trades=req.clear_trades,
            )
        except PaperOMSError as exc:
            _raise_paper_oms_http(exc)
    _save_paper_config({
        "initial_capital": init_cap,
        "currency": req.currency,
        "reset_at": datetime.now(timezone.utc).isoformat(),
    })

    if req.clear_trades:
        def clear_paper(trades: dict[str, dict]) -> None:
            for trade_id in [
                key for key, value in trades.items()
                if value.get("mode", "paper") == "paper"
            ]:
                trades.pop(trade_id, None)
        _mutate_trades(clear_paper)

    return await get_account_portfolio(mode="paper", _key=_key)


class PlaceOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_./:-]+$")
    direction: Literal["long", "short"]
    order_type: Literal["market", "limit"] = "market"
    entry: float = Field(gt=0, allow_inf_nan=False)
    stop_loss: float = Field(gt=0, allow_inf_nan=False)
    take_profit: float = Field(gt=0, allow_inf_nan=False)
    position_size: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    size: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    qty: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    exchange: Literal["binance", "bybit", "innovestx", "mt5", "alpaca"] = "binance"
    # Generic trade placement is intentionally paper-only. Real-money orders
    # use broker-specific live routes guarded by a Live Session dependency.
    mode: Literal["paper"] = "paper"
    tag: Optional[str] = Field(default=None, max_length=100)
    notes: str = Field(default="", max_length=4000)
    risk_pct: float = Field(default=1.0, gt=0, le=5, allow_inf_nan=False)
    auto_be: bool = True
    trailing_stop: bool = False
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=100)

    @model_validator(mode="after")
    def validate_size_aliases(self):
        supplied = [v for v in (self.position_size, self.size, self.qty) if v is not None]
        if not supplied:
            raise ValueError("One of position_size, size, or qty is required")
        if len(supplied) > 1 and any(not math.isclose(supplied[0], value) for value in supplied[1:]):
            raise ValueError("position_size, size, and qty must agree when supplied together")
        return self

    def get_effective_size(self) -> float:
        return float(next(v for v in (self.position_size, self.size, self.qty) if v is not None))


class CloseTradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    close_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    reason: Optional[str] = Field(default="manual", max_length=200)
    quantity: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    percentage: Optional[float] = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=100)

    @model_validator(mode="after")
    def validate_close_size(self):
        if self.quantity is not None and self.percentage is not None:
            raise ValueError("Supply quantity or percentage, not both")
        return self


class UpdateTradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stop_loss: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    take_profit: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    notes: Optional[str] = Field(default=None, max_length=4000)


@router.post("/place")
async def place_order(
    req: PlaceOrderRequest,
    _key: str = Depends(verify_api_key),
):
    """Place a paper order. This endpoint can never route to a broker."""
    effective_size = req.get_effective_size()
    entry = float(req.entry)
    dir_ = req.direction.lower()
    sl = float(req.stop_loss)
    tp = float(req.take_profit)

    # Strict sanity checks for TP & SL relative to Entry
    if dir_ == "long":
        if sl >= entry or sl <= 0 or tp <= entry or tp <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid SL/TP for LONG: Stop Loss ({sl}) must be below Entry ({entry}) and Take Profit ({tp}) must be above Entry",
            )
    else:
        if sl <= entry or sl <= 0 or tp >= entry or tp <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid SL/TP for SHORT: Stop Loss ({sl}) must be above Entry ({entry}) and Take Profit ({tp}) must be below Entry",
            )

    if _paper_oms_available():
        try:
            payload = req.model_dump()
            payload["position_size"] = effective_size
            return await paper_oms.place_order(payload)
        except PaperOMSError as exc:
            _raise_paper_oms_http(exc)

    cfg = get_settings()
    effective_mode: Literal["paper"] = "paper"
    broker_name = "paper"
    currency_name = "USD"

    current_trades = get_all_trades()
    if req.idempotency_key:
        existing = next(
            (trade for trade in current_trades.values() if trade.get("idempotency_key") == req.idempotency_key),
            None,
        )
        if existing:
            return existing
    open_count = sum(1 for trade in current_trades.values() if trade.get("mode", "paper") == effective_mode and trade.get("status") in {"open", "pending"})
    if open_count >= cfg.max_open_positions:
        raise HTTPException(status_code=409, detail="Maximum open/pending position limit reached")

    paper_cfg = _load_paper_config()
    initial_capital = float(paper_cfg.get("initial_capital", 100000.0))
    realized = sum(
        float(trade.get("pnl", 0.0)) for trade in current_trades.values()
        if trade.get("mode", "paper") == "paper" and trade.get("status") == "closed"
    )
    equity = max(initial_capital + realized, 0.0)
    today_utc = datetime.now(timezone.utc).date()
    daily_realized = 0.0
    for trade in current_trades.values():
        if trade.get("mode", "paper") != "paper" or trade.get("status") != "closed":
            continue
        try:
            closed_at = datetime.fromisoformat(str(trade["closed_at"]).replace("Z", "+00:00"))
            if closed_at.astimezone(timezone.utc).date() == today_utc:
                daily_realized += float(trade.get("pnl", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
    daily_loss_limit = initial_capital * cfg.max_daily_loss / 100.0
    if daily_realized <= -daily_loss_limit:
        raise HTTPException(status_code=409, detail="Daily loss limit reached; new orders are disabled")

    allowed_risk_pct = min(req.risk_pct, cfg.default_risk_per_trade)
    allowed_risk = equity * allowed_risk_pct / 100.0
    estimated_loss = abs(entry - sl) * effective_size
    if estimated_loss > allowed_risk * 1.001:
        raise HTTPException(
            status_code=400,
            detail=f"Position risks {estimated_loss:.2f}, above allowed {allowed_risk:.2f} ({allowed_risk_pct:.2f}%)",
        )
    if entry * effective_size > equity * 5.0:
        raise HTTPException(status_code=400, detail="Position notional exceeds the 5x paper leverage limit")

    try:
        result = await _paper_execution.place_order(
            symbol=req.symbol,
            direction=req.direction,
            entry=entry,
            stop_loss=sl,
            take_profit=tp,
            position_size=effective_size,
            exchange=req.exchange,
            order_type=req.order_type,
        )
    except Exception as e:
        logger.error(f"Failed to execute trade {req.symbol} ({effective_mode}): {e}")
        raise HTTPException(status_code=502, detail="Order execution failed") from e

    if not isinstance(result, dict) or result.get("status") not in {"filled", "submitted"}:
        raise HTTPException(status_code=502, detail="Execution engine did not acknowledge the order")

    # Check initial status for Paper Trading (Pending Limit vs Open Market)
    initial_status = "open"
    if effective_mode == "paper" and req.order_type == "limit":
            try:
                s_up = req.symbol.upper()
                mtype = "forex" if any(f in s_up for f in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]) else ("crypto" if "/" in s_up or "USDT" in s_up or "THB" in s_up else "stock")
                tk = await _market_data.get_ticker_24h(req.symbol, mtype)
                cur_p = float(tk.get("price", 0.0))
                if cur_p <= 0:
                    raise ValueError("provider returned no valid price")
                if dir_ == "long" and cur_p > entry * 1.0005:
                    initial_status = "pending"
                elif dir_ == "short" and cur_p < entry * 0.9995:
                    initial_status = "pending"
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Unable to validate limit order against live price: {exc}") from exc

    trade_id = str(uuid4())
    tag_name = req.tag if (req.tag and req.tag.strip()) else f"POS-{trade_id[:8]}"
    trade = {
        **result,
        "id": trade_id,
        "tag": tag_name,
        "mode": effective_mode,
        "broker": broker_name,
        "currency": currency_name,
        "entry": entry,
        "stop_loss": sl,
        "initial_stop_loss": sl,
        "initial_sl_dist": abs(entry - sl),
        "take_profit": tp,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "status": initial_status,
        "notes": req.notes,
        "order_type": req.order_type,
        "risk_pct": allowed_risk_pct,
        "estimated_risk": round(estimated_loss, 2),
        "auto_be": req.auto_be,
        "trailing_stop": req.trailing_stop,
        "idempotency_key": req.idempotency_key,
        "pnl": 0.0,
        "pnl_pct": 0.0,
    }
    def add_trade(trades: dict[str, dict]) -> dict:
        if req.idempotency_key:
            existing = next(
                (item for item in trades.values() if item.get("idempotency_key") == req.idempotency_key),
                None,
            )
            if existing:
                return dict(existing)
        trades[trade_id] = trade
        return dict(trade)

    saved_trade = _mutate_trades(add_trade)
    from app.engines.price_hub import price_hub
    price_hub.register_symbol(req.symbol)
    return saved_trade


@router.get("/")
async def list_trades(
    status: Optional[Literal["open", "pending", "closed", "cancelled"]] = None,
    mode: Literal["paper", "live", "all"] = "paper",
    broker: Optional[Literal["paper", "innovestx", "alpaca", "binance", "mt5", "all"]] = None,
    _key: str = Depends(verify_api_key),
    live_session_token: str | None = Header(default=None, alias="X-Live-Session-Token"),
):
    """List trades partitioned strictly by mode (paper vs live) and broker."""
    from app.engines.price_hub import price_hub

    if mode in {"live", "all"}:
        live_session_manager.require(live_session_token, api_key=_key)

    if mode == "paper" and (broker is None or broker in {"paper", "all"}) and _paper_oms_available():
        try:
            return await paper_oms.list_positions(status=status)
        except PaperOMSError as exc:
            _raise_paper_oms_http(exc)

    trades = [dict(trade) for trade in get_all_trades().values()]

    # Normalize legacy trade records
    for t in trades:
        if "mode" not in t:
            t["mode"] = "paper"
        if "broker" not in t:
            t["broker"] = "paper" if t.get("mode") == "paper" else "innovestx"
        if "currency" not in t:
            t["currency"] = "THB" if t.get("broker") == "innovestx" else "USD"

    # Filter by mode if specified and not 'all'
    if mode.lower() != "all":
        trades = [t for t in trades if str(t.get("mode", "paper")).lower() == mode.lower()]

    # Filter by broker if specified and not 'all'
    if broker and broker.lower() != "all":
        trades = [t for t in trades if str(t.get("broker", "paper")).lower() == broker.lower()]

    if status:
        trades = [t for t in trades if t.get("status") == status]

    def enrich_trade(t: dict) -> dict:
        t_copy = dict(t)
        if t_copy.get("status") == "open":
            try:
                sym = str(t_copy["symbol"])
                entry = float(t_copy["entry"])
                direction = str(t_copy["direction"]).lower()
                size = float(t_copy.get("position_size", t_copy.get("size")))
            except (KeyError, TypeError, ValueError):
                t_copy.update({
                    "live_price": None,
                    "live_pnl": None,
                    "live_pnl_pct": None,
                    "price_status": "invalid_trade_data",
                })
                return t_copy
            if not sym or entry <= 0 or size <= 0 or direction not in {"long", "short"}:
                t_copy.update({
                    "live_price": None,
                    "live_pnl": None,
                    "live_pnl_pct": None,
                    "price_status": "invalid_trade_data",
                })
                return t_copy
            price_hub.register_symbol(sym)
            ticker = price_hub.get_ticker(sym)
            cur_price = float(ticker.get("price", 0.0)) if ticker else 0.0
            if cur_price > 0:
                t_copy["live_price"] = cur_price
                if direction == "long":
                    live_pnl = (cur_price - entry) * size
                    live_pnl_pct = ((cur_price - entry) / entry) * 100 if entry > 0 else 0.0
                else:
                    live_pnl = (entry - cur_price) * size
                    live_pnl_pct = ((entry - cur_price) / entry) * 100 if entry > 0 else 0.0
                t_copy["live_pnl"] = round(live_pnl, 2)
                t_copy["live_pnl_pct"] = round(live_pnl_pct, 2)
                t_copy["price_status"] = "live"
            else:
                # Do not present the entry price as if it were a current quote.
                t_copy["live_price"] = None
                t_copy["live_pnl"] = None
                t_copy["live_pnl_pct"] = None
                t_copy["price_status"] = "unavailable"
        return t_copy

    enriched_trades = [enrich_trade(t) for t in trades]
    return {"total": len(enriched_trades), "trades": enriched_trades}


@router.post("/{trade_id}/close")
async def close_trade(
    trade_id: str,
    req: CloseTradeRequest,
    _key: str = Depends(verify_api_key),
):
    """Close an open position or cancel a pending order."""
    legacy_trade = get_all_trades().get(trade_id)
    if legacy_trade and legacy_trade.get("mode") == "live":
        raise HTTPException(status_code=409, detail="Live positions must be closed or cancelled at the broker")
    if _paper_oms_available():
        try:
            trade = await paper_oms.get_position(trade_id)
            if trade.get("status") == "pending":
                return await paper_oms.cancel_entry_order(
                    trade_id, reason=req.reason or "Order Cancelled"
                )
            return await paper_oms.close_position(
                trade_id,
                close_price=req.close_price,
                reason=req.reason or "manual",
                quantity=req.quantity,
                percentage=req.percentage,
                client_order_id=req.idempotency_key,
            )
        except PaperOMSError as exc:
            _raise_paper_oms_http(exc)
    trade = legacy_trade
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.get("mode") == "live":
        raise HTTPException(status_code=409, detail="Live positions must be closed or cancelled at the broker")

    if trade.get("status") == "pending":
        def cancel_pending(trades: dict[str, dict]) -> Optional[dict]:
            current = trades.get(trade_id)
            if not current or current.get("status") != "pending":
                return None
            current.update({
                "status": "cancelled",
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "close_price": None,
                "close_reason": req.reason or "Order Cancelled",
                "pnl": 0.0,
                "pnl_pct": 0.0,
            })
            return dict(current)

        cancelled = _mutate_trades(cancel_pending)
        if not cancelled:
            latest = get_all_trades().get(trade_id)
            latest_status = latest.get("status") if latest else "missing"
            raise HTTPException(status_code=409, detail=f"Order state changed to '{latest_status}' before cancellation")
        return cancelled

    if trade.get("status") != "open":
        raise HTTPException(status_code=409, detail=f"Trade {trade_id} is already closed with status '{trade.get('status')}'")

    close_p = req.close_price
    if not close_p or close_p <= 0:
        try:
            from app.engines.market_data import MarketDataEngine
            mde = MarketDataEngine()
            sym = trade.get("symbol", "BTC/USDT")
            sym_upper = sym.upper()
            mtype = "forex" if any(f in sym_upper for f in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]) else ("crypto" if ("/" in sym_upper or "THB" in sym_upper) else "stock")
            df = await mde.get_ohlcv(sym, "1m", mtype, limit=5)
            if not df.empty:
                close_p = float(df["close"].iloc[-1])
            else:
                raise HTTPException(status_code=503, detail="Current market price is unavailable; supply close_price")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Current market price is unavailable; supply close_price") from exc
    closed = auto_close_trade_sync(trade_id, req.reason or "manual", float(close_p))
    if not closed:
        latest = get_all_trades().get(trade_id)
        latest_status = latest.get("status") if latest else "missing"
        raise HTTPException(status_code=409, detail=f"Trade state changed to '{latest_status}' before close")
    return closed


@router.get("/account")
async def get_account_portfolio(
    mode: Literal["paper", "live"] = "paper",
    broker: Optional[Literal["paper", "innovestx", "alpaca", "binance", "mt5"]] = None,
    _key: str = Depends(verify_api_key),
    live_session_token: str | None = Header(default=None, alias="X-Live-Session-Token"),
):
    """
    Return active broker or paper trading account details,
    with strict isolation between paper trades and real broker balances.
    """
    from app.core.config import get_settings
    cfg = get_settings()
    target_mode = mode
    if target_mode == "paper" and (broker is None or broker == "paper") and _paper_oms_available():
        try:
            return await paper_oms.account_snapshot()
        except PaperOMSError as exc:
            _raise_paper_oms_http(exc)
    if target_mode == "live":
        session = live_session_manager.require(live_session_token, api_key=_key)
        if broker and broker not in {session.broker}:
            raise HTTPException(status_code=403, detail="Live Session is not authorized for the requested broker")
        broker = session.broker
    trades_snapshot = get_all_trades()

    alpaca_key = (cfg.alpaca_api_key or "").strip()
    alpaca_sec = (cfg.alpaca_api_secret or "").strip()
    alpaca_base = cfg.alpaca_base_url.rstrip("/").removesuffix("/v2").removesuffix("/v1")

    innovestx_key = (cfg.innovestx_api_key or "").strip()
    innovestx_sec = (cfg.innovestx_api_secret or "").strip()

    paper_cfg = _load_paper_config()
    paper_init_cap = float(paper_cfg.get("initial_capital", 100000.0))

    # Portfolio reads must remain fast and must not fan out to one network call
    # per position.  PriceHub is populated by the background stream; missing
    # quotes are reported explicitly instead of treating Entry as a live price.
    from app.engines.price_hub import price_hub

    def _calc_cached_open_pnl(t: dict) -> Optional[float]:
        try:
            sym = str(t["symbol"])
            entry = float(t["entry"])
            direction = str(t["direction"]).lower()
            size = float(t.get("position_size", t.get("size")))
        except (KeyError, TypeError, ValueError):
            return None
        if entry <= 0 or size <= 0 or direction not in {"long", "short"}:
            return None
        price_hub.register_symbol(sym)
        ticker = price_hub.get_ticker(sym)
        try:
            cur_price = float(ticker["price"]) if ticker else 0.0
        except (KeyError, TypeError, ValueError):
            return None
        if cur_price <= 0:
            return None
        return (cur_price - entry) * size if direction == "long" else (entry - cur_price) * size

    if target_mode == "live":
        # 1. Check InnovestX (Thailand Digital Asset Exchange)
        if (not broker or broker.lower() in ["innovestx", "invx"]) and (innovestx_key and innovestx_sec):
            try:
                from app.engines.innovestx_client import InnovestXClient
                client = InnovestXClient(api_key=innovestx_key, api_secret=innovestx_sec)
                bal_res = await client.get_account_balances()
                if bal_res.get("code") == "0000":
                    bal_list = bal_res.get("data", [])
                    thb_cash = 0.0
                    thb_hold = 0.0
                    non_zero_assets = []

                    for item in bal_list:
                        prod = str(item.get("product", "")).upper()
                        amt = float(item.get("amount", 0.0))
                        hld = float(item.get("hold", 0.0))
                        if prod == "THB":
                            thb_cash += amt
                            thb_hold += hld
                        elif (amt + hld) > 0:
                            non_zero_assets.append({"symbol": prod, "amount": amt, "hold": hld})

                    total_equity = thb_cash + thb_hold
                    masked_id = f"INVX-{innovestx_key[:6].upper()}...{innovestx_key[-4:].upper()}"

                    # Filter ONLY Live InnovestX trades
                    live_closed = [t for t in trades_snapshot.values() if t.get("status") == "closed" and str(t.get("mode", "")).lower() == "live" and str(t.get("broker", "")).lower() == "innovestx"]
                    live_open = [t for t in trades_snapshot.values() if t.get("status") == "open" and str(t.get("mode", "")).lower() == "live" and str(t.get("broker", "")).lower() == "innovestx"]

                    live_realized = sum(float(t.get("pnl", 0.0)) for t in live_closed)
                    return {
                        "broker": "InnovestX (SCBX Digital Asset)",
                        "broker_id": "innovestx",
                        "account_id": masked_id,
                        "status": "ACTIVE",
                        "currency": "THB",
                        "currency_symbol": "฿",
                        "initial_capital": round(total_equity, 2),
                        "cash": round(thb_cash, 2),
                        "buying_power": round(thb_cash, 2),
                        "equity": round(total_equity, 2),
                        "mode": "live",
                        "hold_cash": round(thb_hold, 2),
                        "asset_count": len(non_zero_assets),
                        "realized_pnl": round(live_realized, 2),
                        "unrealized_pnl": None,
                        "total_pnl": None,
                        "current_net_worth": round(total_equity, 2) if not non_zero_assets else None,
                        "valuation_status": "partial" if non_zero_assets else "cash_only",
                        "unpriced_asset_count": len(non_zero_assets),
                        "closed_trades_count": len(live_closed),
                        "open_trades_count": len(live_open),
                    }
            except Exception as e:
                logger.warning(f"Error fetching InnovestX account in trades API: {e}")

        # Check Alpaca Markets if configured. This is a separate branch so an
        # unavailable InnovestX account does not suppress the fallback broker.
        if (not broker or broker.lower() == "alpaca") and (alpaca_key and alpaca_sec):
            try:
                import httpx
                async with httpx.AsyncClient(timeout=6.0) as client:
                    resp = await client.get(
                        f"{alpaca_base}/v2/account",
                        headers={"APCA-API-KEY-ID": alpaca_key, "APCA-API-SECRET-KEY": alpaca_sec},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        required_fields = ("equity", "cash", "buying_power", "account_number", "status", "currency")
                        if any(data.get(field) in (None, "") for field in required_fields):
                            raise ValueError("Alpaca account response is missing required fields")
                        equity = float(data["equity"])
                        cash = float(data["cash"])
                        bp = float(data["buying_power"])
                        acc_num = str(data["account_number"])
                        status = str(data["status"])
                        currency = str(data["currency"])

                        alpaca_closed = [t for t in trades_snapshot.values() if t.get("status") == "closed" and str(t.get("mode", "")).lower() == "live" and str(t.get("broker", "")).lower() == "alpaca"]
                        alpaca_open = [t for t in trades_snapshot.values() if t.get("status") == "open" and str(t.get("mode", "")).lower() == "live" and str(t.get("broker", "")).lower() == "alpaca"]

                        alpaca_realized = sum(float(t.get("pnl", 0.0)) for t in alpaca_closed)
                        return {
                            "broker": "Alpaca Markets (" + ("Paper" if "paper" in alpaca_base else "Live") + ")",
                            "broker_id": "alpaca",
                            "account_id": acc_num,
                            "status": status,
                            "currency": currency,
                            "currency_symbol": "$",
                            "initial_capital": equity,
                            "cash": cash,
                            "buying_power": bp,
                            "equity": equity,
                            "mode": "live",
                            "hold_cash": 0.0,
                            "asset_count": 0,
                            "realized_pnl": round(alpaca_realized, 2),
                            "unrealized_pnl": None,
                            "total_pnl": None,
                            "current_net_worth": equity,
                            "valuation_status": "broker_reported",
                            "closed_trades_count": len(alpaca_closed),
                            "open_trades_count": len(alpaca_open),
                        }
            except Exception as e:
                logger.warning(f"Error fetching Alpaca account in trades API: {e}")

        # If target mode is live, but no broker configured/connected, return disconnected state
        return {
            "broker": "ยังไม่ได้เชื่อมต่อบัญชีจริง (No Broker Connected)",
            "broker_id": "none",
            "account_id": "DISCONNECTED",
            "status": "DISCONNECTED",
            "currency": "THB",
            "currency_symbol": "฿",
            "initial_capital": 0.0,
            "cash": 0.0,
            "buying_power": 0.0,
            "equity": 0.0,
            "mode": "live",
            "hold_cash": 0.0,
            "asset_count": 0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
            "current_net_worth": 0.0,
            "closed_trades_count": 0,
            "open_trades_count": 0,
            "message": "ยังไม่ได้ระบุ API Key ของโบรกเกอร์ในหน้า Settings",
        }

    # Fallback / Default: Paper Trading Account
    paper_closed = [t for t in trades_snapshot.values() if t.get("status") == "closed" and str(t.get("mode", "paper")).lower() == "paper"]
    paper_open = [t for t in trades_snapshot.values() if t.get("status") == "open" and str(t.get("mode", "paper")).lower() == "paper"]

    paper_realized = sum(float(t.get("pnl", 0.0)) for t in paper_closed)
    cached_pnls = [_calc_cached_open_pnl(t) for t in paper_open]
    priced_pnls = [pnl for pnl in cached_pnls if pnl is not None]
    paper_unrealized = sum(priced_pnls)
    unpriced_positions = len(cached_pnls) - len(priced_pnls)

    current_net_worth = paper_init_cap + paper_realized + paper_unrealized
    return {
        "broker": "Paper Trading Portfolio",
        "broker_id": "paper",
        "account_id": "PAPER-PORTFOLIO-01",
        "status": "ACTIVE",
        "currency": paper_cfg.get("currency", "USD"),
        "currency_symbol": "$",
        "initial_capital": paper_init_cap,
        "cash": round(paper_init_cap + paper_realized, 2),
        "buying_power": round(paper_init_cap + paper_realized, 2),
        "equity": round(current_net_worth, 2),
        "mode": "paper",
        "hold_cash": 0.0,
        "asset_count": len(paper_open),
        "realized_pnl": round(paper_realized, 2),
        "unrealized_pnl": round(paper_unrealized, 2),
        "total_pnl": round(paper_realized + paper_unrealized, 2),
        "current_net_worth": round(current_net_worth, 2),
        "valuation_status": "complete" if unpriced_positions == 0 else "partial",
        "unpriced_positions": unpriced_positions,
        "closed_trades_count": len(paper_closed),
        "open_trades_count": len(paper_open),
    }


# ---------------------------------------------------------------------------
# InnovestX (SCBX) Live Broker Endpoints
# ---------------------------------------------------------------------------

class InnovestXCredsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: Optional[str] = Field(default=None, max_length=4096)
    api_secret: Optional[str] = Field(default=None, max_length=4096)


@router.get("/broker/innovestx/status")
async def get_innovestx_status_get(
    _key: str = Depends(verify_api_key),
):
    """Check configured InnovestX credentials without putting secrets in a URL."""
    from app.engines.innovestx_client import InnovestXClient
    cfg = get_settings()
    eff_key = (cfg.innovestx_api_key or "").strip()
    eff_sec = (cfg.innovestx_api_secret or "").strip()
    if not eff_key or not eff_sec:
        return {"connected": False, "message": "InnovestX API Key and Secret are required"}
    client = InnovestXClient(api_key=eff_key, api_secret=eff_sec)
    return await client.test_connection()


@router.post("/broker/innovestx/status")
async def get_innovestx_status_post(
    req: Optional[InnovestXCredsRequest] = None,
    _key: str = Depends(verify_api_key),
):
    """Check connection and authentication status with InnovestX Digital Asset Exchange (POST)."""
    from app.engines.innovestx_client import InnovestXClient
    cfg = get_settings()
    eff_key = ((req.api_key if req else None) or cfg.innovestx_api_key or "").strip()
    eff_sec = ((req.api_secret if req else None) or cfg.innovestx_api_secret or "").strip()
    if not eff_key or not eff_sec:
        return {"connected": False, "message": "InnovestX API Key and Secret are required"}
    client = InnovestXClient(api_key=eff_key, api_secret=eff_sec)
    return await client.test_connection()


@router.get("/broker/innovestx/balances")
async def get_innovestx_balances_get(
    _key: str = Depends(verify_api_key),
    _session: LiveSession = Depends(require_live_session),
):
    """Fetch balances using configured credentials only."""
    from app.engines.innovestx_client import InnovestXClient
    cfg = get_settings()
    eff_key = (cfg.innovestx_api_key or "").strip()
    eff_sec = (cfg.innovestx_api_secret or "").strip()
    if not eff_key or not eff_sec:
        return {"code": "4001", "message": "InnovestX API Key and Secret are required", "data": []}
    client = InnovestXClient(api_key=eff_key, api_secret=eff_sec)
    return await client.get_account_balances()


@router.post("/broker/innovestx/balances")
async def get_innovestx_balances_post(
    req: Optional[InnovestXCredsRequest] = None,
    _key: str = Depends(verify_api_key),
    _session: LiveSession = Depends(require_live_session),
):
    """Fetch live account balances from InnovestX (POST)."""
    from app.engines.innovestx_client import InnovestXClient
    cfg = get_settings()
    eff_key = ((req.api_key if req else None) or cfg.innovestx_api_key or "").strip()
    eff_sec = ((req.api_secret if req else None) or cfg.innovestx_api_secret or "").strip()
    if not eff_key or not eff_sec:
        return {"code": "4001", "message": "InnovestX API Key and Secret are required", "data": []}
    client = InnovestXClient(api_key=eff_key, api_secret=eff_sec)
    return await client.get_account_balances()


@router.get("/broker/innovestx/open-orders")
async def get_innovestx_open_orders(
    _key: str = Depends(verify_api_key),
    _session: LiveSession = Depends(require_live_session),
):
    """Fetch live working orders from InnovestX."""
    return await _live_execution.innovestx.get_open_orders()


@router.get("/broker/innovestx/history")
async def get_innovestx_order_history(
    symbol: Optional[str] = None,
    _key: str = Depends(verify_api_key),
    _session: LiveSession = Depends(require_live_session),
):
    """Fetch order history from InnovestX."""
    return await _live_execution.innovestx.get_order_history(symbol=symbol)


class InnovestXOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_./:-]+$")
    side: Literal["BUY", "SELL", "buy", "sell"]
    order_type: Literal["LIMIT", "MARKET", "limit", "market"] = "LIMIT"
    price: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    quantity: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    value_thb: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    client_order_id: int = Field(gt=0)
    live_confirmation: Literal["I_UNDERSTAND_THIS_IS_LIVE"]

    @model_validator(mode="after")
    def validate_live_order(self):
        if (self.quantity is None) == (self.value_thb is None):
            raise ValueError("Specify exactly one of quantity or value_thb")
        if self.order_type.upper() == "LIMIT" and self.price <= 0:
            raise ValueError("A positive price is required for a limit order")
        return self


@router.post("/broker/innovestx/order")
async def place_innovestx_order(
    req: InnovestXOrderRequest,
    _key: str = Depends(verify_api_key),
    session: LiveSession = Depends(require_live_session),
):
    """Reject new Live exposure until broker-side protective orders exist."""
    if session.broker != "innovestx":
        raise HTTPException(status_code=403, detail="Live Session is not authorized for InnovestX")
    raise HTTPException(
        status_code=501,
        detail="Live order placement is disabled until broker-side protective-order OMS is implemented",
    )


class InnovestXCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: int = Field(gt=0)
    live_confirmation: Literal["I_UNDERSTAND_THIS_IS_LIVE"]


@router.post("/broker/innovestx/cancel")
async def cancel_innovestx_order(
    req: InnovestXCancelRequest,
    _key: str = Depends(verify_api_key),
    session: LiveSession = Depends(require_live_session),
):
    """Cancel an active open order on InnovestX."""
    if session.broker != "innovestx":
        raise HTTPException(status_code=403, detail="Live Session is not authorized for InnovestX")
    res = await _live_execution.innovestx.cancel_order(order_id=req.order_id)
    if isinstance(res, dict) and res.get("code") == "0000":
        return {"success": True, "message": f"Order #{req.order_id} cancelled"}
    raise HTTPException(status_code=400, detail=res.get("message", "InnovestX order cancellation failed"))


# ---------------------------------------------------------------------------
# Parameterized Trade Endpoints
# ---------------------------------------------------------------------------

@router.get("/{trade_id}")
async def get_trade(
    trade_id: str,
    _key: str = Depends(verify_api_key),
    live_session_token: str | None = Header(default=None, alias="X-Live-Session-Token"),
):
    legacy_trade = get_all_trades().get(trade_id)
    if legacy_trade and legacy_trade.get("mode", "paper") == "live":
        live_session_manager.require(
            live_session_token,
            broker=str(legacy_trade.get("broker", "")),
            api_key=_key,
        )
        return legacy_trade
    if _paper_oms_available():
        try:
            return await paper_oms.get_position(trade_id)
        except PaperOMSError as exc:
            _raise_paper_oms_http(exc)
    trade = legacy_trade
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.get("mode", "paper") == "live":
        live_session_manager.require(
            live_session_token,
            broker=str(trade.get("broker", "")),
            api_key=_key,
        )
    return trade


@router.patch("/{trade_id}")
async def update_trade(
    trade_id: str,
    req: UpdateTradeRequest,
    _key: str = Depends(verify_api_key),
):
    if _paper_oms_available():
        try:
            return await paper_oms.update_protection(
                trade_id,
                stop_loss=req.stop_loss,
                take_profit=req.take_profit,
                notes=req.notes,
            )
        except PaperOMSError as exc:
            _raise_paper_oms_http(exc)
    def mutate(trades: dict[str, dict]) -> Optional[dict]:
        trade = trades.get(trade_id)
        if not trade:
            return None
        if trade.get("mode", "paper") != "paper":
            raise ValueError("Live trades must be modified through the broker-specific Live API")
        if trade.get("status") not in {"open", "pending"}:
            raise ValueError("Only open or pending trades may be edited")
        entry = float(trade.get("entry", 0.0))
        direction = str(trade.get("direction", "long")).lower()
        new_sl = float(req.stop_loss) if req.stop_loss is not None else float(trade.get("stop_loss", 0.0))
        new_tp = float(req.take_profit) if req.take_profit is not None else float(trade.get("take_profit", 0.0))
        valid = (new_sl < entry < new_tp) if direction == "long" else (new_tp < entry < new_sl)
        if not valid:
            raise ValueError("Stop loss and take profit must remain on the correct sides of entry")
        if req.stop_loss is not None:
            trade["stop_loss"] = new_sl
        if req.take_profit is not None:
            trade["take_profit"] = new_tp
        if req.notes is not None:
            trade["notes"] = req.notes
        trade["updated_at"] = datetime.now(timezone.utc).isoformat()
        return dict(trade)

    try:
        updated = _mutate_trades(mutate)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Trade not found")
    return updated


@router.delete("/{trade_id}")
async def cancel_trade(
    trade_id: str,
    _key: str = Depends(verify_api_key),
):
    if _paper_oms_available():
        try:
            cancelled = await paper_oms.cancel_entry_order(trade_id)
            return {"message": "Trade cancelled", "trade_id": trade_id, "trade": cancelled}
        except PaperOMSError as exc:
            _raise_paper_oms_http(exc)
    def mutate(trades: dict[str, dict]) -> Optional[dict]:
        trade = trades.get(trade_id)
        if not trade:
            return None
        if trade.get("mode", "paper") != "paper" or trade.get("status") != "pending":
            raise ValueError("Only pending paper orders can be cancelled with this endpoint")
        trade.update({
            "status": "cancelled",
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "close_reason": "Order Cancelled",
            "pnl": 0.0,
            "pnl_pct": 0.0,
        })
        return dict(trade)

    try:
        cancelled = _mutate_trades(mutate)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not cancelled:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {"message": "Trade cancelled", "trade_id": trade_id, "trade": cancelled}
