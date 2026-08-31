"""Exchange-native price/quantity filters for executable order intents."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math

from app.engines.market_data import get_shared_http_client


@dataclass(frozen=True)
class InstrumentRules:
    price_tick: float
    quantity_step: float
    min_quantity: float
    min_notional: float

    @staticmethod
    def _floor(value: float, step: float) -> float:
        return math.floor((value + step * 1e-9) / step) * step

    @staticmethod
    def _ceil(value: float, step: float) -> float:
        return math.ceil((value - step * 1e-9) / step) * step

    def floor_quantity(self, value: float) -> float:
        return self._floor(value, self.quantity_step)

    def price(self, value: float, *, upward: bool) -> float:
        rounded = self._ceil(value, self.price_tick) if upward else self._floor(value, self.price_tick)
        return round(rounded, max(0, int(round(-math.log10(self.price_tick))) + 2))


class InstrumentRulesService:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[datetime, InstrumentRules]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, *, symbol: str, market_type: str, exchange: str) -> InstrumentRules:
        if market_type == "stock":
            return InstrumentRules(0.01, 1.0, 1.0, 0.0)
        if market_type != "crypto" or exchange.lower() != "binance":
            return InstrumentRules(0.000001, 0.000001, 0.000001, 0.0)
        compact = symbol.upper().replace("/", "").replace("-", "").replace("_", "")
        now = datetime.now(timezone.utc)
        cached = self._cache.get(compact)
        if cached and now < cached[0]:
            return cached[1]
        lock = self._locks.setdefault(compact, asyncio.Lock())
        async with lock:
            cached = self._cache.get(compact)
            if cached and now < cached[0]:
                return cached[1]
            response = await get_shared_http_client().get(
                "https://api.binance.com/api/v3/exchangeInfo",
                params={"symbol": compact},
            )
            response.raise_for_status()
            payload = response.json()
            symbols = payload.get("symbols") or []
            if not symbols:
                raise ValueError(f"Binance instrument rules unavailable for {symbol}")
            filters = {
                item.get("filterType"): item for item in symbols[0].get("filters", [])
            }
            price_filter = filters.get("PRICE_FILTER") or {}
            lot_filter = filters.get("LOT_SIZE") or {}
            notional_filter = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
            rules = InstrumentRules(
                price_tick=float(price_filter.get("tickSize", 0) or 0),
                quantity_step=float(lot_filter.get("stepSize", 0) or 0),
                min_quantity=float(lot_filter.get("minQty", 0) or 0),
                min_notional=float(notional_filter.get("minNotional", 0) or 0),
            )
            if rules.price_tick <= 0 or rules.quantity_step <= 0 or rules.min_quantity < 0:
                raise ValueError(f"Invalid Binance instrument rules for {symbol}")
            self._cache[compact] = (now + timedelta(hours=6), rules)
            return rules


instrument_rules = InstrumentRulesService()
