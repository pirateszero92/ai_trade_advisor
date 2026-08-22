from __future__ import annotations

import time
from typing import Literal, Optional
from loguru import logger
from app.core.config import get_settings
from app.engines.innovestx_client import InnovestXClient


class ExecutionEngine:
    """Paper and live trade execution engine supporting InnovestX, CCXT, MT5, and Alpaca."""

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
        exchange: str = "innovestx",
        mode: Optional[Literal["paper", "live"]] = None,
        order_type: Literal["limit", "market"] = "limit",
    ) -> dict:
        if position_size <= 0:
            raise ValueError(f"Invalid position size: {position_size}")
        if entry <= 0:
            raise ValueError(f"Invalid entry price: {entry}")
        if stop_loss <= 0 or take_profit <= 0:
            raise ValueError("SL and TP must be positive numbers")

        effective_mode = mode or self.cfg.trading_mode
        if effective_mode == "paper":
            return await self._paper_order(symbol, direction, entry, stop_loss, take_profit, position_size)
        else:
            return await self._live_order(
                symbol=symbol,
                direction=direction,
                entry=entry,
                sl=stop_loss,
                tp=take_profit,
                size=position_size,
                exchange=exchange,
                order_type=order_type,
            )

    async def _paper_order(self, symbol: str, direction: str, entry: float, sl: float, tp: float, size: float) -> dict:
        logger.info(f"[Execution] PAPER ORDER: {direction.upper()} {size} {symbol} @ {entry} SL={sl} TP={tp}")
        return {
            "mode": "paper",
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "stop_loss": sl,
            "take_profit": tp,
            "size": size,
            "status": "filled",
            "filled_at": time.time(),
        }

    async def _live_order(
        self,
        symbol: str,
        direction: str,
        entry: float,
        sl: float,
        tp: float,
        size: float,
        exchange: str,
        order_type: str = "limit",
    ) -> dict:
        logger.info(f"[Execution] LIVE ORDER REQUEST: {direction.upper()} {size} {symbol} on {exchange}")

        # 1. InnovestX (Thailand Digital Asset Exchange)
        if exchange.lower() in ("innovestx", "invx") or "thb" in symbol.lower():
            if not self.innovestx.is_configured():
                raise ValueError("InnovestX API Key and Secret are not configured in backend/.env")

            side: Literal["BUY", "SELL"] = "BUY" if direction.lower() == "long" else "SELL"
            ord_type: Literal["LIMIT", "MARKET"] = "MARKET" if order_type.lower() == "market" else "LIMIT"

            res = await self.innovestx.place_order(
                symbol=symbol,
                side=side,
                order_type=ord_type,
                price=entry,
                quantity=size,
            )

            if isinstance(res, dict) and res.get("code") == "0000":
                order_data = res.get("data", {})
                logger.info(f"[Execution] InnovestX Order SUCCESS: {order_data}")
                return {
                    "mode": "live",
                    "broker": "InnovestX",
                    "status": "submitted",
                    "symbol": symbol,
                    "direction": direction,
                    "entry": entry,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "size": size,
                    "broker_order_id": order_data.get("orderId"),
                    "raw": res,
                }
            else:
                err_msg = res.get("message") or res.get("error", "Unknown error from InnovestX")
                logger.error(f"[Execution] InnovestX Order FAILED: {err_msg}")
                raise RuntimeError(f"InnovestX Live Order Failed: {err_msg}")

        # 2. Other Brokers (Binance / Bybit / MT5 / Alpaca)
        raise NotImplementedError(
            f"Live trading for exchange '{exchange}' is not yet enabled. "
            "Currently supported Live Broker: 'innovestx'."
        )
