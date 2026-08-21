"""
Trades API
Manage paper / live trade orders and open position tracking.
"""

from __future__ import annotations

import asyncio
from typing import Literal, Optional
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import verify_api_key
from app.engines.execution_engine import ExecutionEngine

from pathlib import Path
import json

router = APIRouter()
_execution = ExecutionEngine()

PAPER_CONFIG_FILE = Path(__file__).parent.parent.parent / "config" / "paper_portfolio.json"
TRADES_STORE_FILE = Path(__file__).parent.parent.parent / "config" / "trades_store.json"


def _load_trades() -> dict[str, dict]:
    if TRADES_STORE_FILE.exists():
        try:
            return json.loads(TRADES_STORE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_trades():
    try:
        TRADES_STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        TRADES_STORE_FILE.write_text(json.dumps(_trades, indent=2), encoding="utf-8")
    except Exception:
        pass


_trades: dict[str, dict] = _load_trades()


def _load_paper_config() -> dict:
    if PAPER_CONFIG_FILE.exists():
        try:
            return json.loads(PAPER_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"initial_capital": 100000.0, "currency": "USD"}


def _save_paper_config(data: dict):
    try:
        PAPER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        PAPER_CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


class ResetAccountRequest(BaseModel):
    initial_capital: float = 100000.0
    clear_trades: bool = True
    currency: str = "USD"


@router.post("/account/reset")
async def reset_paper_account(req: ResetAccountRequest, _key: str = Depends(verify_api_key)):
    """Reset Paper Trading initial capital and optionally clear trade history."""
    init_cap = max(100.0, float(req.initial_capital))
    _save_paper_config({
        "initial_capital": init_cap,
        "currency": req.currency,
        "reset_at": datetime.now(timezone.utc).isoformat(),
    })

    if req.clear_trades:
        _trades.clear()
        _save_trades()

    return await get_account_portfolio()


class PlaceOrderRequest(BaseModel):
    symbol: str
    direction: Literal["long", "short"]
    entry: float
    stop_loss: float
    take_profit: float
    position_size: Optional[float] = None
    size: Optional[float] = None
    qty: Optional[float] = None
    exchange: str = "binance"
    mode: Optional[Literal["paper", "live"]] = None
    tag: Optional[str] = None
    notes: str = ""

    def get_effective_size(self) -> float:
        return float(self.position_size or self.size or self.qty or 1.0)


class CloseTradeRequest(BaseModel):
    close_price: Optional[float] = None
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
    effective_size = req.get_effective_size()
    result = await _execution.place_order(
        symbol=req.symbol,
        direction=req.direction,
        entry=req.entry,
        stop_loss=req.stop_loss,
        take_profit=req.take_profit,
        position_size=effective_size,
        exchange=req.exchange,
        mode=req.mode,
    )
    trade_id = str(uuid4())
    tag_name = req.tag if (req.tag and req.tag.strip()) else f"POS-{trade_id[:8]}"
    trade = {
        **result,
        "id": trade_id,
        "tag": tag_name,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
        "notes": req.notes,
        "pnl": 0.0,
        "pnl_pct": 0.0,
    }
    _trades[trade_id] = trade
    _save_trades()
    return trade


@router.get("/")
async def list_trades(
    status: Optional[Literal["open", "closed", "cancelled"]] = None,
    _key: str = Depends(verify_api_key),
):
    """List all trades, optionally filtered by status, enriched with live price and unrealized PnL."""
    import asyncio
    from app.engines.market_data import MarketDataEngine
    mde = MarketDataEngine()

    try:
        from app.services.event_trigger import MarketMonitor
        monitor = MarketMonitor.get_instance()
        # Fire non-blocking TP/SL background check
        asyncio.create_task(monitor._check_open_positions_tp_sl())
    except Exception:
        pass

    trades = list(_trades.values())
    if status:
        trades = [t for t in trades if t.get("status") == status]

    async def enrich_trade(t: dict) -> dict:
        t_copy = dict(t)
        if t_copy.get("status") == "open":
            sym = t_copy.get("symbol", "BTC/USDT")
            entry = float(t_copy.get("entry", 100.0))
            direction = t_copy.get("direction", "long").lower()
            size = float(t_copy.get("position_size", t_copy.get("size", 1.0)))

            sym_upper = sym.upper()
            if any(f in sym_upper for f in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "AUDUSD", "USDCAD"]):
                mtype = "forex"
            elif "/" in sym_upper or "USDT" in sym_upper:
                mtype = "crypto"
            else:
                mtype = "stock"

            try:
                ticker = await asyncio.wait_for(mde.get_ticker_24h(sym, mtype), timeout=1.5)
                cur_price = float(ticker.get("price", entry))
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
                else:
                    t_copy["live_price"] = entry
                    t_copy["live_pnl"] = 0.0
                    t_copy["live_pnl_pct"] = 0.0
            except Exception:
                t_copy["live_price"] = entry
                t_copy["live_pnl"] = 0.0
                t_copy["live_pnl_pct"] = 0.0
        return t_copy

    enriched_trades = await asyncio.gather(*[enrich_trade(t) for t in trades])
    return {"total": len(enriched_trades), "trades": enriched_trades}


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

    close_p = req.close_price
    if not close_p or close_p <= 0:
        try:
            from app.engines.market_data import MarketDataEngine
            mde = MarketDataEngine()
            sym = trade.get("symbol", "BTC/USDT")
            sym_upper = sym.upper()
            mtype = "forex" if any(f in sym_upper for f in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]) else ("crypto" if "/" in sym_upper else "stock")
            df = await mde.get_ohlcv(sym, "1m", mtype, limit=5)
            if not df.empty:
                close_p = float(df["close"].iloc[-1])
            else:
                close_p = trade.get("entry", 100.0)
        except Exception:
            close_p = trade.get("entry", 100.0)

    entry = trade.get("entry", close_p)
    direction = trade.get("direction", "long")
    size = trade.get("size", trade.get("position_size", 1.0))

    if direction == "long":
        pnl_pct = ((close_p - entry) / entry) * 100 if entry > 0 else 0.0
        pnl = (close_p - entry) * size
    else:
        pnl_pct = ((entry - close_p) / entry) * 100 if entry > 0 else 0.0
        pnl = (entry - close_p) * size

    trade["status"] = "closed"
    trade["closed_at"] = datetime.now(timezone.utc).isoformat()
    trade["close_price"] = close_p
    trade["close_reason"] = req.reason or "manual"
    trade["pnl"] = round(pnl, 2)
    trade["pnl_pct"] = round(pnl_pct, 2)

    _trades[trade_id] = trade
    _save_trades()
    return trade


@router.get("/account")
async def get_account_portfolio(_key: str = Depends(verify_api_key)):
    """Return active broker or paper trading account details, initial capital, equity, and net worth."""
    from app.core.config import get_settings
    from loguru import logger
    cfg = get_settings()

    alpaca_key = cfg.alpaca_api_key
    alpaca_sec = cfg.alpaca_api_secret
    alpaca_base = cfg.alpaca_base_url.rstrip("/").removesuffix("/v2").removesuffix("/v1")

    paper_cfg = _load_paper_config()
    paper_init_cap = float(paper_cfg.get("initial_capital", 100000.0))

    account_info = {
        "broker": "Paper Trading",
        "account_id": "PAPER-PORTFOLIO-01",
        "status": "ACTIVE",
        "currency": paper_cfg.get("currency", "USD"),
        "initial_capital": paper_init_cap,
        "cash": paper_init_cap,
        "buying_power": paper_init_cap * 4,
        "equity": paper_init_cap,
        "mode": cfg.trading_mode,
    }

    if cfg.trading_mode == "live" and alpaca_key and alpaca_sec:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(
                    f"{alpaca_base}/v2/account",
                    headers={"APCA-API-KEY-ID": alpaca_key, "APCA-API-SECRET-KEY": alpaca_sec},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    equity = float(data.get("equity", 100000.0))
                    cash = float(data.get("cash", equity))
                    bp = float(data.get("buying_power", equity * 4))
                    acc_num = data.get("account_number", "PA3QEVYDQV6I")
                    status = data.get("status", "ACTIVE")
                    currency = data.get("currency", "USD")

                    account_info = {
                        "broker": "Alpaca Markets (" + ("Paper" if "paper" in alpaca_base else "Live") + ")",
                        "account_id": acc_num,
                        "status": status,
                        "currency": currency,
                        "initial_capital": equity,
                        "cash": cash,
                        "buying_power": bp,
                        "equity": equity,
                        "mode": cfg.trading_mode,
                    }
        except Exception as e:
            logger.warning(f"Error fetching Alpaca account in trades API: {e}")

    # Calculate aggregate realized PnL and open unrealized PnL from trades
    closed_trades = [t for t in _trades.values() if t.get("status") == "closed"]
    open_trades = [t for t in _trades.values() if t.get("status") == "open"]

    realized_pnl = sum(float(t.get("pnl", 0.0)) for t in closed_trades)

    unrealized_pnl = 0.0
    from app.engines.market_data import MarketDataEngine
    mde = MarketDataEngine()

    async def _calc_open_pnl(t: dict) -> float:
        sym = t.get("symbol", "BTC/USDT")
        entry = float(t.get("entry", 100.0))
        direction = t.get("direction", "long").lower()
        size = float(t.get("position_size", t.get("size", 1.0)))
        sym_upper = sym.upper()
        mtype = "forex" if any(f in sym_upper for f in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]) else ("crypto" if "/" in sym_upper else "stock")
        try:
            ticker = await mde.get_ticker_24h(sym, mtype)
            cur_price = float(ticker.get("price", entry))
            if cur_price > 0:
                return (cur_price - entry) * size if direction == "long" else (entry - cur_price) * size
        except Exception:
            pass
        return 0.0

    if open_trades:
        pnls = await asyncio.gather(*[_calc_open_pnl(t) for t in open_trades])
        unrealized_pnl = sum(pnls)

    current_net_worth = account_info["initial_capital"] + realized_pnl + unrealized_pnl

    account_info["realized_pnl"] = round(realized_pnl, 2)
    account_info["unrealized_pnl"] = round(unrealized_pnl, 2)
    account_info["total_pnl"] = round(realized_pnl + unrealized_pnl, 2)
    account_info["current_net_worth"] = round(current_net_worth, 2)
    account_info["closed_trades_count"] = len(closed_trades)
    account_info["open_trades_count"] = len(open_trades)

    return account_info


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
