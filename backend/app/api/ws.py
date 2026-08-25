"""
WebSocket API.
Real-time high-speed streaming hub for Price Tickers, Open Trade PnL, Signals, and AI Chat.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from app.core.config import get_settings
from app.core.security import is_valid_api_key
from app.engines.ai_engine import AIEngine
from app.engines.market_data import MarketDataEngine
from app.engines.price_hub import price_hub
from app.engines.smc_engine import SMCEngine

router = APIRouter()
_smc = SMCEngine()
_ai = AIEngine()
_market = MarketDataEngine()

# Connection registry
_connections: set[WebSocket] = set()
_stream_clients: set[WebSocket] = set()


def _is_ws_authenticated(websocket: WebSocket) -> bool:
    cfg = get_settings()
    if cfg.app_env == "development":
        return True
    key = (
        websocket.headers.get("X-API-Key")
        or websocket.query_params.get("api_key")
        or websocket.query_params.get("token")
    )
    return is_valid_api_key(key)


async def broadcast(message: dict) -> None:
    """Broadcast a message to all connected WebSocket clients."""
    all_clients = _connections | _stream_clients
    if not all_clients:
        return
    data = json.dumps(message)
    dead: set[WebSocket] = set()
    for ws in list(all_clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    if dead:
        _connections.difference_update(dead)
        _stream_clients.difference_update(dead)


@router.websocket("/stream")
async def ws_stream(websocket: WebSocket):
    """
    Full-Duplex high-speed real-time streaming endpoint.
    Channels: 'tickers', 'trades', 'signals'.
    """
    if not _is_ws_authenticated(websocket):
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    _stream_clients.add(websocket)
    logger.info(f"[WS-Stream] Client connected. Total streaming clients: {len(_stream_clients)}")

    subscribed_channels: set[str] = {"tickers", "trades", "signals"}
    push_task: Optional[asyncio.Task] = None

    async def continuous_ticker_pusher():
        """Push price updates from In-Memory Price Hub every 300ms."""
        last_prices: dict[str, float] = {}
        while True:
            try:
                if "tickers" in subscribed_channels:
                    current_prices = price_hub.get_all_prices()
                    changed: dict[str, dict] = {}
                    for sym, data in current_prices.items():
                        p = data.get("price", 0.0)
                        if sym not in last_prices or abs(last_prices[sym] - p) > 1e-6:
                            changed[sym] = data
                            last_prices[sym] = p

                    if changed:
                        payload = {
                            "type": "price_tick",
                            "timestamp": time.time(),
                            "data": changed,
                        }
                        await websocket.send_text(json.dumps(payload))
            except Exception as exc:
                logger.debug(f"[WS-Stream] Push error: {exc}")
                break
            await asyncio.sleep(0.3)

    # Start low-latency pusher
    push_task = asyncio.create_task(continuous_ticker_pusher())

    # Send initial price snapshot immediately
    try:
        initial_snapshot = price_hub.get_all_prices()
        await websocket.send_text(
            json.dumps({
                "type": "initial_snapshot",
                "timestamp": time.time(),
                "data": initial_snapshot,
            })
        )
    except Exception:
        pass

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                continue

            action = msg.get("action")
            if action == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "timestamp": time.time()}))
            elif action == "subscribe":
                channels = msg.get("channels", ["tickers", "trades", "signals"])
                subscribed_channels.update(channels)
                await websocket.send_text(json.dumps({"type": "subscribed", "channels": list(subscribed_channels)}))
            elif action == "unsubscribe":
                channels = msg.get("channels", [])
                for ch in channels:
                    subscribed_channels.discard(ch)
                await websocket.send_text(json.dumps({"type": "unsubscribed", "channels": list(subscribed_channels)}))
            elif action == "snapshot":
                await websocket.send_text(json.dumps({
                    "type": "price_tick",
                    "timestamp": time.time(),
                    "data": price_hub.get_all_prices(),
                }))
    except WebSocketDisconnect:
        logger.info("[WS-Stream] Client disconnected cleanly")
    except Exception as exc:
        logger.debug(f"[WS-Stream] Connection terminated: {exc}")
    finally:
        if push_task and not push_task.done():
            push_task.cancel()
        _stream_clients.discard(websocket)


@router.websocket("/signals")
async def ws_signals(websocket: WebSocket):
    """WebSocket endpoint for real-time signal streaming."""
    if not _is_ws_authenticated(websocket):
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    _connections.add(websocket)
    logger.info(f"[WS] Signals client connected. Total: {len(_connections)}")

    subscription: dict = {}
    task: asyncio.Task | None = None

    async def stream_signals():
        while True:
            try:
                sym = subscription.get("symbol", "BTC/USDT")
                tf = subscription.get("timeframe", "1H")
                bias = subscription.get("htf_bias", "neutral")
                interval = max(5, int(subscription.get("interval", 60)))

                s_up = sym.upper().replace("/", "").replace("-", "")
                if "market_type" in subscription:
                    m_type = subscription["market_type"]
                elif any(f in s_up for f in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]):
                    m_type = "forex"
                elif "/" in sym or "USDT" in s_up:
                    m_type = "crypto"
                else:
                    m_type = "stock"

                df = await _market.get_ohlcv(symbol=sym, timeframe=tf, market_type=m_type)
                if not df.empty:
                    signal = _smc.analyze(df, sym, tf, bias)
                    await websocket.send_text(
                        json.dumps({"type": "signal", "data": signal.to_dict()})
                    )
            except Exception as exc:
                logger.warning(f"[WS] Stream error: {exc}")
            await asyncio.sleep(interval)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON frame"}))
                continue

            action = msg.get("action")
            if action == "subscribe":
                subscription = msg
                if task and not task.done():
                    task.cancel()
                task = asyncio.create_task(stream_signals())
                await websocket.send_text(
                    json.dumps({"type": "subscribed", "subscription": subscription})
                )
            elif action == "unsubscribe":
                if task and not task.done():
                    task.cancel()
                subscription = {}
                await websocket.send_text(json.dumps({"type": "unsubscribed"}))
            elif action == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            else:
                await websocket.send_text(
                    json.dumps({"type": "error", "message": f"Unknown action: {action}"})
                )
    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected")
    except Exception as exc:
        logger.error(f"[WS] Unexpected error: {exc}")
    finally:
        if task and not task.done():
            task.cancel()
        _connections.discard(websocket)


@router.websocket("/chat")
async def ws_chat(websocket: WebSocket):
    """WebSocket endpoint for real-time AI advisor chat."""
    if not _is_ws_authenticated(websocket):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    logger.info("[WS] Chat client connected")
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON frame"}))
                continue

            messages = msg.get("messages", [])
            if not messages:
                await websocket.send_text(json.dumps({"type": "error", "message": "No messages provided"}))
                continue
            response = await _ai.chat(messages)
            await websocket.send_text(json.dumps({"type": "chat_response", "content": response}))
    except WebSocketDisconnect:
        logger.info("[WS] Chat client disconnected")
    except Exception as exc:
        logger.error(f"[WS] Chat error: {exc}")
