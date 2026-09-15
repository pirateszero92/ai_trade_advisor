"""
Derivatives and Sentiment Intelligence Engine
Queries crypto derivatives public market data (Binance Futures / Bybit) to monitor:
1. Open Interest (OI) and OI Delta % across recent 1H bars
2. Funding Rate (Perpetual swap funding rate and annualization)
3. Top Trader Long/Short Ratio (Institutional / Smart account positioning)
4. Squeeze Risk Alerts

Classifies crowd behavior vs smart money positioning:
- BULLISH_EXPANSION: Price Up + OI Up (Fresh capital accumulation)
- SHORT_SQUEEZE_RISK: Price Up + OI Down (Bear capitulation / short covering, trap danger)
- LONG_LIQUIDATION_FLUSH: Price Down + OI Down (Long stop-out wash, capitulation bottom potential)
- BEARISH_EXPANSION: Price Down + OI Up (Aggressive short seller entry)
- CROWDED_LONG: Funding Rate > +0.03% and LS Ratio > 2.0 (High long squeeze vulnerability)
- CROWDED_SHORT: Funding Rate < -0.02% and LS Ratio < 0.7 (High short squeeze vulnerability)
"""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional
import httpx
from loguru import logger


@dataclass
class DerivativesSentimentResult:
    symbol: str
    market_type: str = "crypto"
    exchange: str = "binance"
    open_interest: float = 0.0
    open_interest_value_usd: float = 0.0
    oi_delta_pct_1h: float = 0.0
    funding_rate: float = 0.0
    funding_rate_annualized_pct: float = 0.0
    top_trader_long_ratio: float = 0.50
    top_trader_short_ratio: float = 0.50
    long_short_ratio: float = 1.0
    sentiment_bias: str = "NEUTRAL"
    crowd_risk_warning: Optional[str] = None
    status: str = "ok"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market_type": self.market_type,
            "exchange": self.exchange,
            "open_interest": round(self.open_interest, 2),
            "open_interest_value_usd": round(self.open_interest_value_usd, 2),
            "oi_delta_pct_1h": round(self.oi_delta_pct_1h, 2),
            "funding_rate": round(self.funding_rate, 6),
            "funding_rate_annualized_pct": round(self.funding_rate_annualized_pct, 2),
            "top_trader_long_ratio": round(self.top_trader_long_ratio, 4),
            "top_trader_short_ratio": round(self.top_trader_short_ratio, 4),
            "long_short_ratio": round(self.long_short_ratio, 3),
            "sentiment_bias": self.sentiment_bias,
            "crowd_risk_warning": self.crowd_risk_warning,
            "status": self.status,
            "details": deepcopy(self.details),
        }


class SentimentDerivativesEngine:
    """
    Asynchronous engine that retrieves and synthesizes derivatives sentiment signals.
    Caches results with 60-second TTL to avoid rate-limiting.
    """

    _cache: dict[str, tuple[float, DerivativesSentimentResult]] = {}
    _cache_ttl_seconds: float = 60.0
    _client: httpx.AsyncClient | None = None

    @classmethod
    @asynccontextmanager
    async def _shared_client(cls):
        if cls._client is None or cls._client.is_closed:
            cls._client = httpx.AsyncClient(
                timeout=httpx.Timeout(2.5, connect=1.0),
                limits=httpx.Limits(max_connections=12, max_keepalive_connections=12),
            )
        yield cls._client

    @classmethod
    async def close(cls):
        if cls._client is not None:
            await cls._client.aclose()
            cls._client = None

    @classmethod
    def _clean_symbol(cls, symbol: str) -> str:
        """Standardize e.g. BTC/USDT -> BTCUSDT for futures endpoints."""
        return symbol.upper().replace("/", "").replace("-", "").replace("_", "")

    @classmethod
    async def get_sentiment(
        cls,
        symbol: str,
        market_type: str = "crypto",
        exchange: str = "binance",
        price_change_pct_1h: float = 0.0,
    ) -> DerivativesSentimentResult:
        """
        Fetch derivatives sentiment. Fast-fails safely with fallback for non-crypto
        or network hiccups.
        """
        if market_type.lower() != "crypto":
            return DerivativesSentimentResult(
                symbol=symbol,
                market_type=market_type,
                exchange=exchange,
                sentiment_bias="NOT_APPLICABLE",
                status="unsupported_market_type",
            )

        clean_sym = cls._clean_symbol(symbol)
        now = time.time()
        cache_key = f"{exchange.lower()}:{clean_sym}"
        cached = cls._cache.get(cache_key)
        if cached and (now - cached[0]) < cls._cache_ttl_seconds:
            res = deepcopy(cached[1])
            cls._enrich_bias(res, price_change_pct_1h)
            return res

        if exchange.lower() == "bybit":
            result = await cls._fetch_bybit_derivatives(clean_sym, symbol)
        elif exchange.lower() == "binance":
            result = await cls._fetch_binance_derivatives(clean_sym, symbol)
        else:
            return DerivativesSentimentResult(symbol=symbol, exchange=exchange, status="unsupported_exchange")

        cls._cache[cache_key] = (now, deepcopy(result))
        if len(cls._cache) > 500:
            cls._cache.pop(next(iter(cls._cache)))
        cls._enrich_bias(result, price_change_pct_1h)
        return result

    @classmethod
    async def _fetch_binance_derivatives(cls, clean_symbol: str, original_symbol: str) -> DerivativesSentimentResult:
        async with cls._shared_client() as client:
            try:
                oi_url = f"https://fapi.binance.com/futures/data/openInterestHist?symbol={clean_symbol}&period=1h&limit=3"
                funding_url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={clean_symbol}&limit=1"
                ratio_url = f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={clean_symbol}&period=1h&limit=1"

                res_oi, res_funding, res_ratio = await asyncio.gather(
                    client.get(oi_url),
                    client.get(funding_url),
                    client.get(ratio_url),
                    return_exceptions=True,
                )
                for response in (res_oi, res_funding, res_ratio):
                    if isinstance(response, Exception):
                        raise ValueError("Incomplete derivatives response") from response
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, list) or not payload:
                        raise ValueError("Missing derivatives observations")
                if len(res_oi.json()) < 2:
                    raise ValueError("Insufficient OI history")

                oi_val = 0.0
                oi_val_usd = 0.0
                oi_delta_pct = 0.0
                if not isinstance(res_oi, Exception) and res_oi.status_code == 200:
                    oi_data = res_oi.json()
                    if len(oi_data) >= 1:
                        oi_val = float(oi_data[-1].get("sumOpenInterest", 0.0))
                        oi_val_usd = float(oi_data[-1].get("sumOpenInterestValue", 0.0))
                    if len(oi_data) >= 2:
                        prev_oi = float(oi_data[-2].get("sumOpenInterest", 0.0))
                        if prev_oi > 0:
                            oi_delta_pct = ((oi_val - prev_oi) / prev_oi) * 100.0

                funding_rate = 0.0
                if not isinstance(res_funding, Exception) and res_funding.status_code == 200:
                    funding_data = res_funding.json()
                    if funding_data and len(funding_data) > 0:
                        funding_rate = float(funding_data[-1].get("fundingRate", 0.0))

                annualized_funding = funding_rate * 3 * 365 * 100.0

                top_long = 0.50
                top_short = 0.50
                ls_ratio = 1.0
                if not isinstance(res_ratio, Exception) and res_ratio.status_code == 200:
                    ratio_data = res_ratio.json()
                    if ratio_data and len(ratio_data) > 0:
                        top_long = float(ratio_data[-1].get("longAccount", 0.50))
                        top_short = float(ratio_data[-1].get("shortAccount", 0.50))
                        ls_ratio = float(ratio_data[-1].get("longShortRatio", 1.0))

                return DerivativesSentimentResult(
                    symbol=original_symbol,
                    market_type="crypto",
                    exchange="binance",
                    open_interest=oi_val,
                    open_interest_value_usd=oi_val_usd,
                    oi_delta_pct_1h=oi_delta_pct,
                    funding_rate=funding_rate,
                    funding_rate_annualized_pct=annualized_funding,
                    top_trader_long_ratio=top_long,
                    top_trader_short_ratio=top_short,
                    long_short_ratio=ls_ratio,
                    status="ok",
                )
            except Exception as exc:
                logger.debug(f"[SentimentEngine] Binance fetch failed for {clean_symbol}: {exc}")
                return DerivativesSentimentResult(
                    symbol=original_symbol,
                    market_type="crypto",
                    exchange="binance",
                    status="fetch_error",
                    crowd_risk_warning="Derivatives data unavailable",
                )

    @classmethod
    async def _fetch_bybit_derivatives(cls, clean_symbol: str, original_symbol: str) -> DerivativesSentimentResult:
        async with cls._shared_client() as client:
            try:
                oi_url = f"https://api.bybit.com/v5/market/open-interest?category=linear&symbol={clean_symbol}&intervalTime=1h&limit=2"
                res = await client.get(oi_url)
                if res.status_code == 200:
                    data = res.json().get("result", {}).get("list", [])
                    if data:
                        oi_val = float(data[0].get("openInterest", 0.0))
                        prev_oi = float(data[1].get("openInterest", oi_val)) if len(data) > 1 else oi_val
                        delta = ((oi_val - prev_oi) / prev_oi * 100.0) if prev_oi > 0 else 0.0
                        return DerivativesSentimentResult(
                            symbol=original_symbol,
                            market_type="crypto",
                            exchange="bybit",
                            open_interest=oi_val,
                            oi_delta_pct_1h=delta,
                            status="partial",
                            details={"available": ["open_interest"], "missing": ["funding", "long_short_ratio"]},
                        )
            except Exception as exc:
                logger.debug(f"[SentimentEngine] Bybit fetch failed: {exc}")

        return DerivativesSentimentResult(
            symbol=original_symbol,
            market_type="crypto",
            exchange="bybit",
            status="fetch_error",
        )

    @classmethod
    def _enrich_bias(cls, result: DerivativesSentimentResult, price_change_pct_1h: float) -> None:
        if result.status != "ok":
            result.sentiment_bias = "NEUTRAL"
            return

        warnings = []
        if result.funding_rate > 0.0003:
            warnings.append("High Long Crowding: Funding rate is heavily positive; long squeeze vulnerability elevated.")
        elif result.funding_rate < -0.0002:
            warnings.append("High Short Crowding: Negative funding rate; short squeeze fuel is dense.")

        if result.long_short_ratio > 2.2:
            warnings.append(f"Top Trader Long Skew ({result.long_short_ratio:.2f}): Retail/Whale longs heavily clustered.")
        elif result.long_short_ratio < 0.65:
            warnings.append(f"Top Trader Short Skew ({result.long_short_ratio:.2f}): Heavy short imbalance.")

        result.crowd_risk_warning = " | ".join(warnings) if warnings else None

        oi_delta = result.oi_delta_pct_1h
        p_delta = price_change_pct_1h

        if p_delta >= 0.3 and oi_delta >= 0.8:
            result.sentiment_bias = "BULLISH_EXPANSION"
        elif p_delta >= 0.3 and oi_delta <= -0.8:
            result.sentiment_bias = "SHORT_SQUEEZE_RISK"
        elif p_delta <= -0.3 and oi_delta >= 0.8:
            result.sentiment_bias = "BEARISH_EXPANSION"
        elif p_delta <= -0.3 and oi_delta <= -0.8:
            result.sentiment_bias = "LONG_LIQUIDATION_FLUSH"
        elif result.funding_rate > 0.0003 or result.long_short_ratio > 2.2:
            result.sentiment_bias = "CROWDED_LONG"
        elif result.funding_rate < -0.0002 or result.long_short_ratio < 0.65:
            result.sentiment_bias = "CROWDED_SHORT"
        else:
            result.sentiment_bias = "NEUTRAL"
