"""
Trades API
Manage paper / live trade orders and open position tracking.
"""

from __future__ import annotations

from typing import Literal, Optional
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import verify_api_key
from app.engines.execution_engine import ExecutionEngine

router = APIRouter()
_execution = ExecutionEngine()

_trades: dict[str, dict] = {}


class PlaceOrderRequest(BaseModel):
    symbol: str
    direction: Literal["long", "short"]
    entry: float
    stop_loss: float
    take_profit: float
    position_size: float
    exchange: str = "binance"
    mode: Optional[Literal["paper", "live"]] = None
    notes: str = ""


class CloseTradeRequest(BaseModel):
    close_price: float
    reason: Optional[str] = "manual"


class UpdateTradeRequest(BaseModel):
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    notes: Optional[str] = None
    status: Optional[Literal["open", "closed", "cancelled"]] = None
    close_price: Optional[float] = None


@router.post("/place")
async def place_order(
    req: PlaceOrderRequest,
    _key: str = Depends(verify_api_key),
):
    """Place a new paper or live trade order."""
    result = await _execution.place_order(
        symbol=req.symbol,
        direction=req.direction,
        entry=req.entry,
        stop_loss=req.stop_loss,
        take_profit=req.take_profit,
        position_size=req.position_size,
        exchange=req.exchange,
        mode=req.mode,
    )
    trade_id = str(uuid4())
    trade = {
        **result,
        "id": trade_id,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
        "notes": req.notes,
        "pnl": 0.0,
        "pnl_pct": 0.0,
    }
    _trades[trade_id] = trade
    return trade


@router.get("/")
async def list_trades(
    status: Optional[str] = None,
    _key: str = Depends(verify_api_key),
):
    """List all trades, optionally filtered by status."""
    trades = list(_trades.values())
    if status:
        trades = [t for t in trades if t.get("status") == status]
    return {"total": len(trades), "trades": trades}


@router.post("/{trade_id}/close")
async def close_trade(
    trade_id: str,
    req: CloseTradeRequest,
    _key: str = Depends(verify_api_key),
):
    """Close an open position and record realized PnL."""
    trade = _trades.get(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    entry = trade.get("entry", req.close_price)
    direction = trade.get("direction", "long")
    size = trade.get("size", 1.0)

    if direction == "long":
        pnl_pct = ((req.close_price - entry) / entry) * 100
        pnl = (req.close_price - entry) * size
    else:
        pnl_pct = ((entry - req.close_price) / entry) * 100
        pnl = (entry - req.close_price) * size

    trade["status"] = "closed"
    trade["closed_at"] = datetime.now(timezone.utc).isoformat()
    trade["close_price"] = req.close_price
    trade["close_reason"] = req.reason
    trade["pnl"] = round(pnl, 2)
    trade["pnl_pct"] = round(pnl_pct, 2)

    _trades[trade_id] = trade
    return trade


@router.get("/{trade_id}")
async def get_trade(
    trade_id: str,
    _key: str = Depends(verify_api_key),
):
    trade = _trades.get(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


@router.delete("/{trade_id}")
async def cancel_trade(
    trade_id: str,
    _key: str = Depends(verify_api_key),
):
    trade = _trades.get(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    trade["status"] = "cancelled"
    return {"message": "Trade cancelled", "trade_id": trade_id}
