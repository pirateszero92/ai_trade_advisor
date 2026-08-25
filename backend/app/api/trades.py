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
from loguru import logger

from app.core.security import verify_api_key
from app.core.config import get_settings
from app.engines.execution_engine import ExecutionEngine

import os
from pathlib import Path
import json

router = APIRouter()
_execution = ExecutionEngine()

PAPER_CONFIG_FILE = Path(__file__).parent.parent.parent / "config" / "paper_portfolio.json"
TRADES_STORE_FILE = Path(__file__).parent.parent.parent / "config" / "trades_store.json"

_trades_lock = asyncio.Lock()


def _load_trades() -> dict[str, dict]:
    if TRADES_STORE_FILE.exists():
        try:
            return json.loads(TRADES_STORE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_trades_sync():
    try:
        import tempfile, os
        TRADES_STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=TRADES_STORE_FILE.parent, delete=False, encoding="utf-8") as tf:
            json.dump(_trades, tf, indent=2)
            tempname = tf.name
        os.replace(tempname, TRADES_STORE_FILE)
    except Exception as e:
        logger.error(f"Failed to save trades atomically: {e}")


async def _save_trades_async():
    async with _trades_lock:
        _save_trades_sync()


_save_trades = _save_trades_sync

_trades: dict[str, dict] = _load_trades()


def get_all_trades() -> dict[str, dict]:
    global _trades
    _trades = _load_trades()
    return _trades


def auto_close_trade_sync(trade_id: str, reason: str, close_price: float) -> Optional[dict]:
    global _trades
    _trades = _load_trades()
    trade = _trades.get(trade_id)
    if not trade or trade.get("status") != "open":
        return None

    entry = float(trade.get("entry", close_price))
    direction = str(trade.get("direction", "long")).lower()
    size = float(trade.get("size", trade.get("position_size", 1.0)))

    if direction == "long":
        pnl_pct = ((close_price - entry) / entry) * 100 if entry > 0 else 0.0
        pnl = (close_price - entry) * size
    else:
        pnl_pct = ((entry - close_price) / entry) * 100 if entry > 0 else 0.0
        pnl = (entry - close_price) * size

    trade["status"] = "closed"
    trade["closed_at"] = datetime.now(timezone.utc).isoformat()
    trade["close_price"] = round(close_price, 4)
    trade["close_reason"] = reason
    trade["pnl"] = round(pnl, 2)
    trade["pnl_pct"] = round(pnl_pct, 2)

    _trades[trade_id] = trade
    _save_trades()
    return trade


def update_trade_sl_sync(trade_id: str, new_sl: float, note: str = "") -> Optional[dict]:
    global _trades
    _trades = _load_trades()
    trade = _trades.get(trade_id)
    if not trade or trade.get("status") != "open":
        return None

    trade["stop_loss"] = round(new_sl, 6)
    if note:
        trade["sl_note"] = note
    _trades[trade_id] = trade
    _save_trades()
    return trade


def _load_paper_config() -> dict:
    if PAPER_CONFIG_FILE.exists():
        try:
            return json.loads(PAPER_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"initial_capital": 100000.0, "currency": "USD"}


def _save_paper_config(data: dict):
    try:
        import tempfile, os
        PAPER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=PAPER_CONFIG_FILE.parent, delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, indent=2)
            tempname = tf.name
        os.replace(tempname, PAPER_CONFIG_FILE)
    except Exception as e:
        logger.error(f"Failed to save paper config atomically: {e}")


class ResetAccountRequest(BaseModel):
    initial_capital: float = 100000.0
    clear_trades: bool = True
    currency: str = "USD"


@router.post("/account/reset")
async def reset_paper_account(req: ResetAccountRequest, _key: str = Depends(verify_api_key)):
    """Reset Paper Trading initial capital and optionally clear paper trade history."""
    init_cap = max(100.0, float(req.initial_capital))
    _save_paper_config({
        "initial_capital": init_cap,
        "currency": req.currency,
        "reset_at": datetime.now(timezone.utc).isoformat(),
    })

    if req.clear_trades:
        global _trades
        # Only delete paper trades, strictly preserving any live broker trades
        _trades = {k: v for k, v in _trades.items() if v.get("mode", "paper") != "paper"}
        _save_trades()

    return await get_account_portfolio(mode="paper")


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
    """Place a new paper or live trade order with strict TP/SL sanity check."""
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

    cfg = get_settings()
    env_mode = os.environ.get("TRADING_MODE")
    effective_mode = req.mode or env_mode or cfg.trading_mode or "paper"
    broker_name = "innovestx" if (effective_mode == "live" and (req.exchange.lower() in ("innovestx", "invx") or "thb" in req.symbol.lower())) else ("paper" if effective_mode == "paper" else req.exchange.lower())
    currency_name = "THB" if broker_name == "innovestx" else "USD"

    try:
        result = await _execution.place_order(
            symbol=req.symbol,
            direction=req.direction,
            entry=entry,
            stop_loss=sl,
            take_profit=tp,
            position_size=effective_size,
            exchange=req.exchange,
            mode=effective_mode,
        )
    except Exception as e:
        logger.error(f"Failed to execute trade {req.symbol} ({effective_mode}): {e}")
        raise HTTPException(status_code=400, detail=str(e))

    # Check initial status for Paper Trading (Pending Limit vs Open Market)
    initial_status = "open"
    if effective_mode == "paper":
        is_limit = "-LIM-" in (req.tag or "").upper() or getattr(req, "order_type", "limit") == "limit"
        if is_limit:
            try:
                from app.engines.market_data import MarketDataEngine
                mde = MarketDataEngine()
                s_up = req.symbol.upper()
                mtype = "forex" if any(f in s_up for f in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]) else ("crypto" if "/" in s_up or "USDT" in s_up or "THB" in s_up else "stock")
                tk = await mde.get_ticker_24h(req.symbol, mtype)
                cur_p = float(tk.get("price", entry))
                if cur_p > 0:
                    if dir_ == "long" and cur_p > entry * 1.0005:
                        initial_status = "pending"
                    elif dir_ == "short" and cur_p < entry * 0.9995:
                        initial_status = "pending"
            except Exception:
                pass

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
        "take_profit": tp,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "status": initial_status,
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
    mode: Optional[str] = None,
    broker: Optional[str] = None,
    _key: str = Depends(verify_api_key),
):
    """List trades partitioned strictly by mode (paper vs live) and broker."""
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

    # Normalize legacy trade records
    for t in trades:
        if "mode" not in t:
            t["mode"] = "paper"
        if "broker" not in t:
            t["broker"] = "paper" if t.get("mode") == "paper" else "innovestx"
        if "currency" not in t:
            t["currency"] = "THB" if t.get("broker") == "innovestx" else "USD"

    # Filter by mode if specified and not 'all'
    if mode and mode.lower() != "all":
        trades = [t for t in trades if str(t.get("mode", "paper")).lower() == mode.lower()]

    # Filter by broker if specified and not 'all'
    if broker and broker.lower() != "all":
        trades = [t for t in trades if str(t.get("broker", "paper")).lower() == broker.lower()]

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
            elif "/" in sym_upper or "USDT" in sym_upper or "THB" in sym_upper:
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
async def get_account_portfolio(
    mode: Optional[str] = None,
    broker: Optional[str] = None,
    _key: str = Depends(verify_api_key)
):
    """
    Return active broker or paper trading account details,
    with strict isolation between paper trades and real broker balances.
    """
    from app.core.config import Settings, get_settings
    from loguru import logger
    import os
    from pathlib import Path

    # Determine effective trading mode from runtime/env
    env_mode = None
    env_file = Path(__file__).parent.parent.parent / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("TRADING_MODE="):
                    env_mode = line.split("=", 1)[1].strip()
        except Exception:
            pass

    cfg = get_settings()
    configured_mode = env_mode or os.environ.get("TRADING_MODE") or cfg.trading_mode or "paper"
    target_mode = (mode or configured_mode).lower()

    alpaca_key = (cfg.alpaca_api_key if cfg.alpaca_api_key is not None else os.environ.get("ALPACA_API_KEY", "")).strip()
    alpaca_sec = (cfg.alpaca_api_secret if cfg.alpaca_api_secret is not None else os.environ.get("ALPACA_API_SECRET", "")).strip()
    alpaca_base = (cfg.alpaca_base_url or os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")).rstrip("/").removesuffix("/v2").removesuffix("/v1")

    innovestx_key = (cfg.innovestx_api_key if cfg.innovestx_api_key is not None else os.environ.get("INNOVESTX_API_KEY", "")).strip()
    innovestx_sec = (cfg.innovestx_api_secret if cfg.innovestx_api_secret is not None else os.environ.get("INNOVESTX_API_SECRET", "")).strip()

    binance_key = (cfg.binance_api_key if cfg.binance_api_key is not None else os.environ.get("BINANCE_API_KEY", "")).strip()
    binance_sec = (cfg.binance_api_secret if cfg.binance_api_secret is not None else os.environ.get("BINANCE_API_SECRET", "")).strip()

    mt5_login = cfg.mt5_login or int(os.environ.get("MT5_LOGIN", "0"))
    mt5_server = cfg.mt5_server or os.environ.get("MT5_SERVER", "")

    paper_cfg = _load_paper_config()
    paper_init_cap = float(paper_cfg.get("initial_capital", 100000.0))

    # Helper function for unrealized PnL calculation
    from app.engines.market_data import MarketDataEngine
    mde = MarketDataEngine()

    async def _calc_open_pnl(t: dict) -> float:
        sym = t.get("symbol", "BTC/USDT")
        entry = float(t.get("entry", 100.0))
        direction = t.get("direction", "long").lower()
        size = float(t.get("position_size", t.get("size", 1.0)))
        sym_upper = sym.upper()
        mtype = "forex" if any(f in sym_upper for f in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]) else ("crypto" if ("/" in sym_upper or "THB" in sym_upper) else "stock")
        try:
            ticker = await mde.get_ticker_24h(sym, mtype)
            cur_price = float(ticker.get("price", entry))
            if cur_price > 0:
                return (cur_price - entry) * size if direction == "long" else (entry - cur_price) * size
        except Exception:
            pass
        return 0.0

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
                    live_closed = [t for t in _trades.values() if t.get("status") == "closed" and str(t.get("mode", "")).lower() == "live" and str(t.get("broker", "")).lower() == "innovestx"]
                    live_open = [t for t in _trades.values() if t.get("status") == "open" and str(t.get("mode", "")).lower() == "live" and str(t.get("broker", "")).lower() == "innovestx"]

                    live_realized = sum(float(t.get("pnl", 0.0)) for t in live_closed)
                    live_unrealized = 0.0
                    if live_open:
                        pnls = await asyncio.gather(*[_calc_open_pnl(t) for t in live_open])
                        live_unrealized = sum(pnls)

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
                        "unrealized_pnl": round(live_unrealized, 2),
                        "total_pnl": round(live_realized + live_unrealized, 2),
                        "current_net_worth": round(total_equity + live_unrealized, 2),
                        "closed_trades_count": len(live_closed),
                        "open_trades_count": len(live_open),
                    }
            except Exception as e:
                logger.warning(f"Error fetching InnovestX account in trades API: {e}")

        # 2. Check Binance if requested or configured
        elif (not broker or broker.lower() == "binance") and (binance_key and binance_sec):
            binance_closed = [t for t in _trades.values() if t.get("status") == "closed" and str(t.get("mode", "")).lower() == "live" and str(t.get("broker", "")).lower() == "binance"]
            binance_open = [t for t in _trades.values() if t.get("status") == "open" and str(t.get("mode", "")).lower() == "live" and str(t.get("broker", "")).lower() == "binance"]
            binance_realized = sum(float(t.get("pnl", 0.0)) for t in binance_closed)
            binance_unrealized = 0.0
            if binance_open:
                pnls = await asyncio.gather(*[_calc_open_pnl(t) for t in binance_open])
                binance_unrealized = sum(pnls)

            masked_bin = f"BIN-{binance_key[:6].upper()}...{binance_key[-4:].upper()}"
            return {
                "broker": "Binance (Crypto Spot & Futures)",
                "broker_id": "binance",
                "account_id": masked_bin,
                "status": "ACTIVE",
                "currency": "USDT",
                "currency_symbol": "$",
                "initial_capital": 10000.0,
                "cash": 10000.0 + binance_realized,
                "buying_power": (10000.0 + binance_realized) * 2,
                "equity": 10000.0 + binance_realized + binance_unrealized,
                "mode": "live",
                "hold_cash": 0.0,
                "asset_count": len(binance_open),
                "realized_pnl": round(binance_realized, 2),
                "unrealized_pnl": round(binance_unrealized, 2),
                "total_pnl": round(binance_realized + binance_unrealized, 2),
                "current_net_worth": round(10000.0 + binance_realized + binance_unrealized, 2),
                "closed_trades_count": len(binance_closed),
                "open_trades_count": len(binance_open),
            }

        # 3. Check MetaTrader 5 if requested or configured
        elif (not broker or broker.lower() in ["mt5", "metatrader"]) and (mt5_login and mt5_login > 0):
            mt5_closed = [t for t in _trades.values() if t.get("status") == "closed" and str(t.get("mode", "")).lower() == "live" and str(t.get("broker", "")).lower() in ["mt5", "metatrader"]]
            mt5_open = [t for t in _trades.values() if t.get("status") == "open" and str(t.get("mode", "")).lower() == "live" and str(t.get("broker", "")).lower() in ["mt5", "metatrader"]]
            mt5_realized = sum(float(t.get("pnl", 0.0)) for t in mt5_closed)
            mt5_unrealized = 0.0
            if mt5_open:
                pnls = await asyncio.gather(*[_calc_open_pnl(t) for t in mt5_open])
                mt5_unrealized = sum(pnls)

            return {
                "broker": f"MetaTrader 5 ({mt5_server or 'Forex/Gold'})",
                "broker_id": "mt5",
                "account_id": f"MT5-{mt5_login}",
                "status": "ACTIVE",
                "currency": "USD",
                "currency_symbol": "$",
                "initial_capital": 50000.0,
                "cash": 50000.0 + mt5_realized,
                "buying_power": (50000.0 + mt5_realized) * 100,
                "equity": 50000.0 + mt5_realized + mt5_unrealized,
                "mode": "live",
                "hold_cash": 0.0,
                "asset_count": len(mt5_open),
                "realized_pnl": round(mt5_realized, 2),
                "unrealized_pnl": round(mt5_unrealized, 2),
                "total_pnl": round(mt5_realized + mt5_unrealized, 2),
                "current_net_worth": round(50000.0 + mt5_realized + mt5_unrealized, 2),
                "closed_trades_count": len(mt5_closed),
                "open_trades_count": len(mt5_open),
            }

        # 4. Check Alpaca Markets if configured
        elif (not broker or broker.lower() == "alpaca") and (alpaca_key and alpaca_sec):
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

                        alpaca_closed = [t for t in _trades.values() if t.get("status") == "closed" and str(t.get("mode", "")).lower() == "live" and str(t.get("broker", "")).lower() == "alpaca"]
                        alpaca_open = [t for t in _trades.values() if t.get("status") == "open" and str(t.get("mode", "")).lower() == "live" and str(t.get("broker", "")).lower() == "alpaca"]

                        alpaca_realized = sum(float(t.get("pnl", 0.0)) for t in alpaca_closed)
                        alpaca_unrealized = 0.0
                        if alpaca_open:
                            pnls = await asyncio.gather(*[_calc_open_pnl(t) for t in alpaca_open])
                            alpaca_unrealized = sum(pnls)

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
                            "unrealized_pnl": round(alpaca_unrealized, 2),
                            "total_pnl": round(alpaca_realized + alpaca_unrealized, 2),
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
    paper_closed = [t for t in _trades.values() if t.get("status") == "closed" and str(t.get("mode", "paper")).lower() == "paper"]
    paper_open = [t for t in _trades.values() if t.get("status") == "open" and str(t.get("mode", "paper")).lower() == "paper"]

    paper_realized = sum(float(t.get("pnl", 0.0)) for t in paper_closed)
    paper_unrealized = 0.0
    if paper_open:
        pnls = await asyncio.gather(*[_calc_open_pnl(t) for t in paper_open])
        paper_unrealized = sum(pnls)

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
        "buying_power": round((paper_init_cap + paper_realized) * 4, 2),
        "equity": round(current_net_worth, 2),
        "mode": "paper",
        "hold_cash": 0.0,
        "asset_count": len(paper_open),
        "realized_pnl": round(paper_realized, 2),
        "unrealized_pnl": round(paper_unrealized, 2),
        "total_pnl": round(paper_realized + paper_unrealized, 2),
        "current_net_worth": round(current_net_worth, 2),
        "closed_trades_count": len(paper_closed),
        "open_trades_count": len(paper_open),
    }


# ---------------------------------------------------------------------------
# InnovestX (SCBX) Live Broker Endpoints
# ---------------------------------------------------------------------------

class InnovestXCredsRequest(BaseModel):
    api_key: Optional[str] = None
    api_secret: Optional[str] = None


@router.get("/broker/innovestx/status")
async def get_innovestx_status_get(
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    _key: str = Depends(verify_api_key),
):
    """Check connection and authentication status with InnovestX Digital Asset Exchange (GET)."""
    from app.engines.innovestx_client import InnovestXClient
    cfg = get_settings()
    eff_key = (api_key or cfg.innovestx_api_key or "").strip()
    eff_sec = (api_secret or cfg.innovestx_api_secret or "").strip()
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
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    _key: str = Depends(verify_api_key),
):
    """Fetch live account balances from InnovestX (GET)."""
    from app.engines.innovestx_client import InnovestXClient
    cfg = get_settings()
    eff_key = (api_key or cfg.innovestx_api_key or "").strip()
    eff_sec = (api_secret or cfg.innovestx_api_secret or "").strip()
    if not eff_key or not eff_sec:
        return {"code": "4001", "message": "InnovestX API Key and Secret are required", "data": []}
    client = InnovestXClient(api_key=eff_key, api_secret=eff_sec)
    return await client.get_account_balances()


@router.post("/broker/innovestx/balances")
async def get_innovestx_balances_post(
    req: Optional[InnovestXCredsRequest] = None,
    _key: str = Depends(verify_api_key),
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
async def get_innovestx_open_orders(_key: str = Depends(verify_api_key)):
    """Fetch live working orders from InnovestX."""
    return await _execution.innovestx.get_open_orders()


@router.get("/broker/innovestx/history")
async def get_innovestx_order_history(
    symbol: Optional[str] = None,
    _key: str = Depends(verify_api_key),
):
    """Fetch order history from InnovestX."""
    return await _execution.innovestx.get_order_history(symbol=symbol)


class InnovestXOrderRequest(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL", "buy", "sell"]
    order_type: Literal["LIMIT", "MARKET", "limit", "market"] = "LIMIT"
    price: float
    quantity: Optional[float] = None
    value_thb: Optional[float] = None


@router.post("/broker/innovestx/order")
async def place_innovestx_order(
    req: InnovestXOrderRequest,
    _key: str = Depends(verify_api_key),
):
    """Directly send a live order to InnovestX Digital Asset Exchange."""
    res = await _execution.innovestx.place_order(
        symbol=req.symbol,
        side=req.side,
        order_type=req.order_type,
        price=req.price,
        quantity=req.quantity,
        value_thb=req.value_thb,
    )
    if isinstance(res, dict) and res.get("code") == "0000":
        return {"success": True, "broker": "InnovestX", "data": res.get("data")}
    raise HTTPException(status_code=400, detail=res.get("message", "InnovestX order placement failed"))


class InnovestXCancelRequest(BaseModel):
    order_id: int


@router.post("/broker/innovestx/cancel")
async def cancel_innovestx_order(
    req: InnovestXCancelRequest,
    _key: str = Depends(verify_api_key),
):
    """Cancel an active open order on InnovestX."""
    res = await _execution.innovestx.cancel_order(order_id=req.order_id)
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
):
    trade = _trades.get(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


@router.patch("/{trade_id}")
async def update_trade(
    trade_id: str,
    req: UpdateTradeRequest,
    _key: str = Depends(verify_api_key),
):
    global _trades
    _trades = _load_trades()
    trade = _trades.get(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if req.stop_loss is not None:
        trade["stop_loss"] = float(req.stop_loss)
    if req.take_profit is not None:
        trade["take_profit"] = float(req.take_profit)
    if req.notes is not None:
        trade["notes"] = req.notes
    if req.status is not None:
        trade["status"] = req.status
    _trades[trade_id] = trade
    _save_trades()
    return trade


@router.delete("/{trade_id}")
async def cancel_trade(
    trade_id: str,
    _key: str = Depends(verify_api_key),
):
    global _trades
    _trades = _load_trades()
    trade = _trades.get(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    trade["status"] = "cancelled"
    _trades[trade_id] = trade
    _save_trades()
    return {"message": "Trade cancelled", "trade_id": trade_id}
