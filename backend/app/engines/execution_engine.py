"""Explicitly separated paper and live execution services.

Paper code has no broker client and cannot select a live destination.  Live
execution is exposed through a different object and must only be called after
the API layer validates a short-lived Live Session.
"""

from __future__ import annotations

from typing import Literal
from loguru import logger
from app.engines.innovestx_client import InnovestXClient


class PaperExecutionEngine:
    """Fill simulator boundary. This class deliberately owns no broker client."""

    async def place_order(
        self,
        symbol: str,
        direction: Literal["long", "short"],
        entry: float,
        stop_loss: float,
        take_profit: float,
        position_size: float,
        exchange: str = "binance",
        order_type: Literal["market", "limit"] = "market",
    ) -> dict:
        del exchange, order_type
        logger.info(
            "PAPER ORDER: {} {} {} @ {} SL={} TP={}",
            direction.upper(),
            position_size,
            symbol,
            entry,
            stop_loss,
            take_profit,
        )
        return {
            "mode": "paper",
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "size": position_size,
            "status": "filled",
        }


class LiveExecutionEngine:
    """Real broker boundary. API callers must validate a Live Session first."""

    def __init__(self):
        self.innovestx = InnovestXClient()

    async def place_order(
        self,
        symbol: str,
        direction: Literal["long", "short"],
        entry: float,
        stop_loss: float,
        take_profit: float,
        position_size: float,
        exchange: str,
        order_type: Literal["market", "limit"],
    ) -> dict:
        del symbol, direction, entry, stop_loss, take_profit, position_size, exchange, order_type
        raise RuntimeError(
            "Live order placement is disabled until broker-side protective-order OMS is implemented"
        )


class ExecutionEngine:
    """Deprecated paper-only compatibility wrapper.

    New API code must depend directly on ``PaperExecutionEngine`` or
    ``LiveExecutionEngine``. This wrapper refuses Live so an older call site
    cannot bypass the Live Gateway dependency accidentally.
    """

    def __init__(self):
        self.paper = PaperExecutionEngine()

    async def place_order(self, *, mode: Literal["paper", "live"], **kwargs) -> dict:
        if mode != "paper":
            raise RuntimeError(
                "ExecutionEngine cannot place Live orders; use LiveExecutionEngine behind the Live Gateway"
            )
        return await self.paper.place_order(**kwargs)
