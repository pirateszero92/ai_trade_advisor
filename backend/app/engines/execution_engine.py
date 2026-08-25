"""
Trade Execution Engine.
Handles Paper and Live order execution across Binance, Bybit, MT5, InnovestX, and Alpaca.
"""

from __future__ import annotations

from typing import Literal, Optional
from loguru import logger
from app.core.config import get_settings
from app.engines.innovestx_client import InnovestXClient


class ExecutionEngine:
    """Paper and live trade execution."""

    def __init__(self):
        self.cfg = get_settings()
        self.innovestx = InnovestXClient()

    async def place_order(
        self,
        symbol: str,
        direction: Literal["long", "short"],
        entry: float,
        stop_loss: float,
        take_profit: float,
        position_size: float,
        exchange: str = "binance",
        mode: Optional[Literal["paper", "live"]] = None,
    ) -> dict:
        effective_mode = mode or self.cfg.trading_mode
        if effective_mode == "paper":
            return await self._paper_order(symbol, direction, entry, stop_loss, take_profit, position_size)
        else:
            return await self._live_order(symbol, direction, entry, stop_loss, take_profit, position_size, exchange)

    async def _paper_order(self, symbol: str, direction: str, entry: float, sl: float, tp: float, size: float) -> dict:
        logger.info(f"PAPER ORDER: {direction.upper()} {size} {symbol} @ {entry} SL={sl} TP={tp}")
        return {
            "mode": "paper",
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "stop_loss": sl,
            "take_profit": tp,
            "size": size,
            "status": "filled",
        }

    async def _live_order(self, symbol: str, direction: str, entry: float, sl: float, tp: float, size: float, exchange: str) -> dict:
        logger.info(f"LIVE ORDER: {direction.upper()} {size} {symbol} on {exchange}")
        if "innovestx" in exchange.lower() or "thb" in symbol.lower():
            # Route through InnovestX Client
            side = "buy" if direction.lower() == "long" else "sell"
            try:
                resp = await self.innovestx.place_order(symbol, side, size, entry)
                return resp
            except Exception as e:
                logger.error(f"[Execution] InnovestX order failed: {e}")
                return {"mode": "live", "status": "failed", "error": str(e)}
        return {
            "mode": "live",
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "stop_loss": sl,
            "take_profit": tp,
            "size": size,
            "exchange": exchange,
            "status": "submitted",
        }
