from typing import Literal, Optional
from loguru import logger
from app.core.config import get_settings


class ExecutionEngine:
    """Paper and live trade execution."""

    def __init__(self):
        self.cfg = get_settings()

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

    async def _paper_order(self, symbol, direction, entry, sl, tp, size) -> dict:
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

    async def _live_order(self, symbol, direction, entry, sl, tp, size, exchange) -> dict:
        logger.warning(f"LIVE ORDER requested for {symbol} — not yet implemented")
        return {"mode": "live", "status": "not_implemented", "message": "Live execution coming in Phase 3"}
