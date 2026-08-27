"""
WebSocket API.
Real-time high-speed streaming hub for Price Tickers, Open Trade PnL, Signals, and AI Chat.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
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
from app.engines.strategy_engine import StrategyEngine

router = APIRouter()
_smc = SMCEngine()
_ai = AIEngine()
_market = MarketDataEngine()
_strategy = StrategyEngine()

# Connection registry
_connections: set[WebSocket] = set()
_stream_clients: set[WebSocket] = set()
_chat_clients: set[WebSocket] = set()
_send_locks: dict[WebSocket, asyncio.Lock] = {}
MAX_WS_CONNECTIONS = 100
MAX_WS_MESSAGE_BYTES = 64 * 1024
ALLOWED_CHANNELS = {"tickers", "trades", "signals"}


def _at_connection_limit() -> bool:
    return len(_connections | _stream_clients | _chat_clients) >= MAX_WS_CONNECTIONS


def _remove_client(websocket: WebSocket) -> None:
    """Atomic cleanup of websocket from all registries and lock stores."""
    _connections.discard(websocket)
    _stream_clients.discard(websocket)
    _chat_clients.discard(websocket)
    _send_locks.pop(websocket, None)


async def _send_json(websocket: WebSocket, payload: dict) -> None:
    lock = _send_locks.setdefault(websocket, asyncio.Lock())
    async with lock:
        await asyncio.wait_for(websocket.send_text(json.dumps(payload)), timeout=3.0)


async def _cancel_task(task: Optional[asyncio.Task]) -> None:
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _decode_protocol_key(protocol: str) -> Optional[str]:
    prefix = "api-key."
    if not protocol.startswith(prefix):
        return None
    encoded = protocol[len(prefix):]
    try:
        encoded += "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
    except Exception:
        return None


def _ws_authentication(websocket: WebSocket) -> tuple[bool, Optional[str]]:
    cfg = get_settings()
    if cfg.app_env == "development" and os.getenv("ALLOW_INSECURE_DEV_AUTH") == "1":
        return True, None

    selected_protocol = None
    key = websocket.headers.get("X-API-Key")
    for protocol in websocket.headers.get("sec-websocket-protocol", "").split(","):
        candidate = protocol.strip()
        protocol_key = _decode_protocol_key(candidate)
        if protocol_key:
            key = key or protocol_key
            selected_protocol = candidate
            break

    # Query-string credentials are disabled by default because proxies and
    # access logs commonly retain full URLs.
    if not key and os.getenv("ALLOW_LEGACY_WS_QUERY_AUTH") == "1":
        key = websocket.query_params.get("api_key") or websocket.query_params.get("token")
    return is_valid_api_key(key), selected_protocol


async def broadcast(message: dict) -> None:
    """Broadcast a message to all connected WebSocket clients."""
    all_clients = _connections | _stream_clients | _chat_clients
    if not all_clients:
        return
    dead: set[WebSocket] = set()
    async def send_one(ws: WebSocket) -> None:
        try:
            await _send_json(ws, message)
        except Exception:
            dead.add(ws)
    await asyncio.gather(*(send_one(ws) for ws in list(all_clients)))
    if dead:
        for ws in dead:
            _remove_client(ws)


@router.websocket("/stream")
async def ws_stream(websocket: WebSocket):
    """
    Full-Duplex high-speed real-time streaming endpoint.
    Channels: 'tickers', 'trades', 'signals'.
    """
    authenticated, auth_protocol = _ws_authentication(websocket)
    if not authenticated:
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return
    if _at_connection_limit():
        await websocket.accept(subprotocol=auth_protocol)
        await websocket.close(code=1013, reason="Server connection limit reached")
        return

    await websocket.accept(subprotocol=auth_protocol)
    _send_locks[websocket] = asyncio.Lock()
    _stream_clients.add(websocket)
    logger.info(f"[WS-Stream] Client connected. Total streaming clients: {len(_stream_clients)}")

    subscribed_channels: set[str] = {"tickers", "trades", "signals"}
    push_task: Optional[asyncio.Task] = None
    ticker_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=2048)

    def enqueue_ticker(data: dict) -> None:
        # Keep the newest event under burst load. The stream is a UI transport,
        # while sequence integrity and CVD are maintained upstream in PriceHub.
        if ticker_queue.full():
            try:
                ticker_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            ticker_queue.put_nowait(data)
        except asyncio.QueueFull:
            pass

    async def continuous_ticker_pusher():
        """Push event-driven price updates with a 20ms burst-coalescing window."""
        while True:
            try:
                first = await ticker_queue.get()
                changed = {str(first.get("norm_symbol", first.get("symbol", ""))): first}
                await asyncio.sleep(0.02)
                while not ticker_queue.empty():
                    item = ticker_queue.get_nowait()
                    key = str(item.get("norm_symbol", item.get("symbol", "")))
                    if key:
                        changed[key] = item
                if "tickers" in subscribed_channels and changed:
                    await _send_json(websocket, {
                        "type": "price_tick",
                        "timestamp": time.time(),
                        "data": changed,
                    })
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug(f"[WS-Stream] Push error: {exc}")
                try:
                    await _send_json(websocket, {"type": "stream_warning", "message": "Ticker stream error"})
                except Exception:
                    break
                await asyncio.sleep(0.5)

    price_hub.subscribe(enqueue_ticker)
    push_task = asyncio.create_task(continuous_ticker_pusher())

    # Send initial price snapshot immediately
    try:
        initial_snapshot = price_hub.get_all_prices()
        await _send_json(
            websocket,
            {
                "type": "initial_snapshot",
                "timestamp": time.time(),
                "data": initial_snapshot,
            },
        )
    except Exception:
        pass

    try:
        last_message_at = 0.0
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > MAX_WS_MESSAGE_BYTES:
                await websocket.close(code=1009, reason="Message too large")
                break
            now = time.monotonic()
            if now - last_message_at < 0.05:
                await _send_json(websocket, {"type": "error", "message": "Rate limit exceeded"})
                continue
            last_message_at = now
            try:
                msg = json.loads(raw)
            except Exception:
                await _send_json(websocket, {"type": "error", "message": "Invalid JSON"})
                continue

            action = msg.get("action")
            if action == "ping":
                await _send_json(websocket, {"type": "pong", "timestamp": time.time()})
            elif action == "subscribe":
                channels = msg.get("channels", ["tickers", "trades", "signals"])
                if not isinstance(channels, list) or len(channels) > len(ALLOWED_CHANNELS) or not set(channels).issubset(ALLOWED_CHANNELS):
                    await _send_json(websocket, {"type": "error", "message": "Invalid channels"})
                    continue
                subscribed_channels.update(channels)
                await _send_json(websocket, {"type": "subscribed", "channels": sorted(subscribed_channels)})
            elif action == "unsubscribe":
                channels = msg.get("channels", [])
                if not isinstance(channels, list) or not set(channels).issubset(ALLOWED_CHANNELS):
                    await _send_json(websocket, {"type": "error", "message": "Invalid channels"})
                    continue
                for ch in channels:
                    subscribed_channels.discard(ch)
                await _send_json(websocket, {"type": "unsubscribed", "channels": sorted(subscribed_channels)})
            elif action == "snapshot":
                await _send_json(websocket, {
                    "type": "price_tick",
                    "timestamp": time.time(),
                    "data": price_hub.get_all_prices(),
                })
            else:
                await _send_json(websocket, {"type": "error", "message": "Unknown action"})
    except WebSocketDisconnect:
        logger.info("[WS-Stream] Client disconnected cleanly")
    except Exception as exc:
        logger.debug(f"[WS-Stream] Connection terminated: {exc}")
    finally:
        price_hub.unsubscribe(enqueue_ticker)
        await _cancel_task(push_task)
        _remove_client(websocket)


@router.websocket("/signals")
async def ws_signals(websocket: WebSocket):
    """WebSocket endpoint for real-time signal streaming."""
    authenticated, auth_protocol = _ws_authentication(websocket)
    if not authenticated:
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return
    if _at_connection_limit():
        await websocket.accept(subprotocol=auth_protocol)
        await websocket.close(code=1013, reason="Server connection limit reached")
        return

    await websocket.accept(subprotocol=auth_protocol)
    _send_locks[websocket] = asyncio.Lock()
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
                interval = max(5, min(3600, int(subscription.get("interval", 60))))

                s_up = sym.upper().replace("/", "").replace("-", "")
                if subscription.get("market_type"):
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
                    strategy = _strategy.evaluate(signal)
                    await _send_json(websocket, {
                        "type": "signal",
                        "data": {
                            **signal.to_dict(),
                            "strategy": strategy.to_dict(),
                            "actionable": strategy.approved,
                        },
                    })
            except Exception as exc:
                logger.warning(f"[WS] Stream error: {exc}")
            await asyncio.sleep(interval)

    try:
        last_message_at = 0.0
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > MAX_WS_MESSAGE_BYTES:
                await websocket.close(code=1009, reason="Message too large")
                break
            now = time.monotonic()
            if now - last_message_at < 0.1:
                await _send_json(websocket, {"type": "error", "message": "Rate limit exceeded"})
                continue
            last_message_at = now
            try:
                msg = json.loads(raw)
            except Exception:
                await _send_json(websocket, {"type": "error", "message": "Invalid JSON frame"})
                continue

            action = msg.get("action")
            if action == "subscribe":
                symbol = str(msg.get("symbol", "BTC/USDT")).strip()
                timeframe = str(msg.get("timeframe", "1h"))
                market_type = msg.get("market_type")
                htf_bias = msg.get("htf_bias", "neutral")
                try:
                    interval = int(msg.get("interval", 60))
                except (TypeError, ValueError):
                    interval = 60
                valid_timeframes = {"1m", "2m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"}
                if (
                    not re.fullmatch(r"[A-Za-z0-9_./:-]{1,30}", symbol)
                    or timeframe not in valid_timeframes
                    or market_type not in {None, "crypto", "forex", "stock"}
                    or htf_bias not in {"bullish", "bearish", "neutral"}
                ):
                    await _send_json(websocket, {"type": "error", "message": "Invalid subscription"})
                    continue
                subscription = {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "market_type": market_type,
                    "htf_bias": htf_bias,
                    "interval": max(5, min(3600, interval)),
                }
                await _cancel_task(task)
                task = asyncio.create_task(stream_signals())
                await _send_json(websocket, {"type": "subscribed", "subscription": subscription})
            elif action == "unsubscribe":
                await _cancel_task(task)
                task = None
                subscription = {}
                await _send_json(websocket, {"type": "unsubscribed"})
            elif action == "ping":
                await _send_json(websocket, {"type": "pong"})
            else:
                await _send_json(websocket, {"type": "error", "message": "Unknown action"})
    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected")
    except Exception as exc:
        logger.error(f"[WS] Unexpected error: {exc}")
    finally:
        await _cancel_task(task)
        _remove_client(websocket)


@router.websocket("/chat")
async def ws_chat(websocket: WebSocket):
    """WebSocket endpoint for real-time AI advisor chat."""
    authenticated, auth_protocol = _ws_authentication(websocket)
    if not authenticated:
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return
    if _at_connection_limit():
        await websocket.accept(subprotocol=auth_protocol)
        await websocket.close(code=1013, reason="Server connection limit reached")
        return

    await websocket.accept(subprotocol=auth_protocol)
    _send_locks[websocket] = asyncio.Lock()
    _chat_clients.add(websocket)
    logger.info("[WS] Chat client connected")
    try:
        last_message_at = 0.0
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > MAX_WS_MESSAGE_BYTES:
                await websocket.close(code=1009, reason="Message too large")
                break
            now = time.monotonic()
            if now - last_message_at < 1.0:
                await _send_json(websocket, {"type": "error", "message": "Rate limit exceeded"})
                continue
            last_message_at = now
            try:
                msg = json.loads(raw)
            except Exception:
                await _send_json(websocket, {"type": "error", "message": "Invalid JSON frame"})
                continue

            messages = msg.get("messages", [])
            if not isinstance(messages, list) or not messages:
                await _send_json(websocket, {"type": "error", "message": "No valid messages list provided"})
                continue
            
            # Validate individual messages structure
            valid = True
            for m in messages:
                if not isinstance(m, dict) or "role" not in m or "content" not in m:
                    valid = False
                    break
                if not isinstance(m["content"], str) or len(m["content"]) > 10_000:
                    valid = False
                    break
            if not valid:
                await _send_json(websocket, {"type": "error", "message": "Invalid message structure in messages array"})
                continue

            try:
                response = await asyncio.wait_for(_ai.chat(messages), timeout=60.0)
            except (ValueError, asyncio.TimeoutError) as exc:
                await _send_json(websocket, {"type": "error", "message": str(exc)})
                continue
            except Exception as exc:
                logger.error(f"[WS-Chat] Chat AI error: {exc}")
                await _send_json(websocket, {"type": "error", "message": "AI analysis unavailable"})
                continue
            await _send_json(websocket, {"type": "chat_response", "content": response})
    except WebSocketDisconnect:
        logger.info("[WS] Chat client disconnected")
    except Exception as exc:
        logger.error(f"[WS] Chat error: {exc}")
    finally:
        _remove_client(websocket)
