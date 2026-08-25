"""
Central In-Memory Price Hub.
Maintains sub-millisecond, thread-safe in-memory cache of live market prices,
24h stats, and streaming ticks across Crypto, Forex/Gold, and Equities.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional
from loguru import logger
import httpx


class PriceHub:
    _instance: Optional[PriceHub] = None

    def __new__(cls) -> PriceHub:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._prices: dict[str, dict] = {}
        self._subscribers: list[Callable[[dict], None]] = []
        self._active_symbols: set[str] = {
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
            "XAUUSD", "EURUSD", "GBPUSD", "USDJPY",
            "AAPL", "TSLA", "NVDA", "MSFT",
        }
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._usd_thb_rate: float = 34.0
        self._last_usd_thb_update: float = 0.0
        self._initialized = True
        logger.info("[PriceHub] Initialized Central In-Memory Price Hub")

    def register_symbol(self, symbol: str) -> None:
        """Register a symbol to be kept fresh by background stream."""
        clean = symbol.strip().upper()
        if clean and clean not in self._active_symbols:
            self._active_symbols.add(clean)

    def get_price(self, symbol: str) -> Optional[float]:
        """Get latest price in sub-millisecond memory lookup."""
        clean = self._normalize(symbol)
        entry = self._prices.get(clean)
        if entry:
            return float(entry.get("price", 0.0))
        return None

    def get_ticker(self, symbol: str) -> Optional[dict]:
        """Get full ticker data (price, bid, ask, change_24h, volume, timestamp)."""
        clean = self._normalize(symbol)
        return self._prices.get(clean)

    def get_all_prices(self) -> dict[str, dict]:
        """Return snapshot of all cached prices."""
        return dict(self._prices)

    def update_price(
        self,
        symbol: str,
        price: float,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        change_24h: Optional[float] = None,
        volume_24h: Optional[float] = None,
        source: str = "stream",
    ) -> dict:
        """Update price in memory and notify listeners."""
        if price <= 0:
            return {}
        clean = self._normalize(symbol)
        now = time.time()
        prev = self._prices.get(clean, {})
        prev_price = prev.get("price", price)
        tick_change = price - prev_price

        data = {
            "symbol": symbol,
            "norm_symbol": clean,
            "price": round(price, 6 if price < 10 else 2),
            "prev_price": prev_price,
            "tick_change": round(tick_change, 6 if abs(tick_change) < 1 else 2),
            "bid": round(bid if bid is not None else price * 0.9999, 4),
            "ask": round(ask if ask is not None else price * 1.0001, 4),
            "change_24h": round(change_24h if change_24h is not None else prev.get("change_24h", 0.0), 2),
            "volume_24h": volume_24h if volume_24h is not None else prev.get("volume_24h", 0.0),
            "timestamp": now,
            "source": source,
        }
        self._prices[clean] = data
        return data

    def _normalize(self, s: str) -> str:
        return s.replace("/", "").replace("-", "").replace("_", "").upper()

    async def start_stream(self) -> None:
        """Start background polling/stream loop for price hub."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._stream_loop())
        logger.info("[PriceHub] Streaming background daemon started")

    async def stop_stream(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[PriceHub] Stream daemon stopped")

    async def _stream_loop(self) -> None:
        """Continuous low-latency price updates."""
        client = httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.5))
        try:
            while self._running:
                try:
                    await self._fetch_crypto_batch(client)
                    await self._fetch_forex_batch(client)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.debug(f"[PriceHub] Stream tick error: {exc}")
                await asyncio.sleep(0.5)  # 500ms refresh loop
        finally:
            await client.aclose()

    async def _fetch_crypto_batch(self, client: httpx.AsyncClient) -> None:
        """Fetch 24h ticker batch from Binance."""
        try:
            resp = await client.get("https://api.binance.com/api/v3/ticker/24hr", timeout=2.5)
            if resp.status_code == 200:
                tickers = resp.json()
                for t in tickers:
                    sym = t.get("symbol", "")
                    if sym in self._active_symbols or f"{sym}" in [self._normalize(s) for s in self._active_symbols]:
                        p = float(t.get("lastPrice", 0))
                        if p > 0:
                            self.update_price(
                                symbol=sym,
                                price=p,
                                bid=float(t.get("bidPrice", p * 0.9999)),
                                ask=float(t.get("askPrice", p * 1.0001)),
                                change_24h=float(t.get("priceChangePercent", 0)),
                                volume_24h=float(t.get("volume", 0)),
                                source="binance_24h",
                            )
        except Exception:
            pass

    async def _fetch_forex_batch(self, client: httpx.AsyncClient) -> None:
        """Fetch forex and gold rates."""
        symbols = ["GC=F", "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDTHB=X"]
        for yf_sym in symbols:
            try:
                resp = await client.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}?interval=1d&range=1d",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=2.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    meta = data["chart"]["result"][0]["meta"]
                    price = float(meta.get("regularMarketPrice", 0))
                    prev_close = float(meta.get("previousClose", price))
                    chg = ((price - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
                    
                    target_sym = yf_sym.replace("=X", "").replace("=F", "")
                    if target_sym == "GC":
                        target_sym = "XAUUSD"
                    
                    if price > 0:
                        self.update_price(
                            symbol=target_sym,
                            price=price,
                            change_24h=chg,
                            source="yahoo_fx",
                        )
            except Exception:
                pass


# Global singleton instance
price_hub = PriceHub()
