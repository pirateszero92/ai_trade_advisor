"""
WebSocket API
Real-time signal streaming and chat endpoint.
"""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from app.engines.ai_engine import AIEngine
from app.engines.market_data import MarketDataEngine
from app.engines.smc_engine import SMCEngine

router = APIRouter()
_smc = SMCEngine()
_ai = AIEngine()
_market = MarketDataEngine()

# Connection registry
_connections: set[WebSocket] = set()


async def broadcast(message: dict) -> None:
    """Broadcast a message to all connected WebSocket clients."""
    if not _connections:
        return
    data = json.dumps(message)
    dead: set[WebSocket] = set()
    for ws in list(_connections):
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    _connections -= dead


@router.websocket("/signals")
async def ws_signals(websocket: WebSocket):
    """
    WebSocket endpoint for real-time signal streaming.

    Accepts JSON commands from the client:
        {"action": "subscribe", "symbol": "BTC/USDT", "timeframe": "1H", "htf_bias": "bullish"}
        {"action": "unsubscribe"}
        {"action": "ping"}

    Sends signal updates every ``interval`` seconds.
    """
    await websocket.accept()
    _connections.add(websocket)
    logger.info(f"[WS] Client connected. Total: {len(_connections)}")

    subscription: dict = {}
    task: asyncio.Task | None = None

    async def stream_signals():
        while True:
            try:
                sym = subscription.get("symbol", "BTC/USDT")
                tf = subscription.get("timeframe", "1H")
                bias = subscription.get("htf_bias", "neutral")
                interval = subscription.get("interval", 60)

                df = await _market.get_ohlcv(symbol=sym, timeframe=tf)
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
            msg = json.loads(raw)
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
    """
    WebSocket endpoint for real-time AI advisor chat.
    Accepts: {"messages": [...]} and streams back the AI response.
    """
    await websocket.accept()
    logger.info("[WS] Chat client connected")
    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
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
