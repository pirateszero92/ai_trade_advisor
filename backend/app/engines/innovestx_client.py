"""
InnovestX Digital Asset Client
Official REST API integration for InnovestX (SCBX) Digital Asset Exchange (Thailand SEC Regulated).
Docs: https://api-docs.innovestxonline.com/#apikey-setup
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlencode, urlparse

import httpx
from loguru import logger
from app.core.config import get_settings


class InnovestXClient:
    """Official client for InnovestX (SCBX) Digital Asset Open API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        cfg = get_settings()
        self.api_key = api_key or cfg.innovestx_api_key
        self.api_secret = api_secret or cfg.innovestx_api_secret
        self.base_url = (base_url or cfg.innovestx_base_url or "https://api.innovestxonline.com").rstrip("/")
        parsed = urlparse(self.base_url)
        self.host = parsed.netloc or "api.innovestxonline.com"

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _generate_signature(
        self,
        method: str,
        path: str,
        query: str,
        content_type: str,
        request_uid: str,
        timestamp: str,
        body_str: str,
    ) -> str:
        """
        Generate SHA256 HMAC signature.
        Formula: HMAC-SHA256(secret, public_key + method + host + path + query + contentType + requestUId + timestamp + body)
        """
        content_to_sign = (
            self.api_key
            + method.upper()
            + self.host.lower()
            + path
            + query
            + content_type
            + request_uid
            + timestamp
            + body_str
        )
        return hmac.new(
            self.api_secret.encode("utf-8"),
            content_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.is_configured():
            raise ValueError("InnovestX API Key and Secret are not configured")

        if not path.startswith("/"):
            path = "/" + path

        query_string = ""
        if params:
            sorted_params = sorted(params.items())
            query_string = "?" + urlencode(sorted_params)

        content_type = "application/json"
        body_str = json.dumps(json_data, separators=(",", ":")) if json_data is not None else ""
        request_uid = str(uuid.uuid4())
        timestamp = str(int(time.time() * 1000))

        sig = self._generate_signature(
            method=method,
            path=path,
            query=query_string,
            content_type=content_type,
            request_uid=request_uid,
            timestamp=timestamp,
            body_str=body_str,
        )

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Content-Type": content_type,
            "Accept": "application/json",
            "X-INVX-APIKEY": self.api_key,
            "X-INVX-TIMESTAMP": timestamp,
            "X-INVX-REQUEST-UID": request_uid,
            "X-INVX-SIGNATURE": sig,
        }

        url = f"{self.base_url}{path}{query_string}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                if method.upper() == "GET":
                    response = await client.get(url, headers=headers)
                elif method.upper() == "POST":
                    response = await client.post(url, headers=headers, content=body_str)
                else:
                    response = await client.request(method=method.upper(), url=url, headers=headers, content=body_str)

                if response.status_code >= 400:
                    logger.error(
                        f"[InnovestX] HTTP {response.status_code} on {path}: {response.text}"
                    )
                    return {
                        "success": False,
                        "status_code": response.status_code,
                        "error": response.text,
                    }
                return response.json()
            except httpx.RequestError as e:
                logger.error(f"[InnovestX] Network error: {e}")
                return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------------
    # Public & Market Data Endpoints
    # -----------------------------------------------------------------------

    @staticmethod
    def format_pair(symbol: str) -> str:
        """Format raw InnovestX symbol like 'BTCTHB' to 'BTC/THB'."""
        s = symbol.upper()
        if s.endswith("THB"):
            base = s[:-3]
            return f"{base}/THB"
        elif s.endswith("USDT"):
            base = s[:-4]
            return f"{base}/USDT"
        return s

    async def get_symbols(self) -> Dict[str, Any]:
        """Fetch list of all tradable symbols on InnovestX."""
        return await self._request("GET", "/api/v1/digital-asset/symbols")

    async def get_formatted_symbols(self) -> List[Dict[str, Any]]:
        """Fetch list of symbols formatted for trading and charting."""
        res = await self.get_symbols()
        pairs = []
        if isinstance(res, dict) and res.get("code") == "0000":
            for item in res.get("data", []):
                raw = item.get("symbol", "")
                formatted = self.format_pair(raw)
                pairs.append({
                    "raw_symbol": raw,
                    "symbol": formatted,
                    "base": formatted.split("/")[0],
                    "quote": formatted.split("/")[1] if "/" in formatted else "THB",
                    "price_increment": item.get("priceIncrement"),
                    "quantity_increment": item.get("quantityIncrement"),
                })
        return pairs

    async def get_products(self) -> Dict[str, Any]:
        """Fetch list of all supported currencies and digital assets."""
        return await self._request("GET", "/api/v1/digital-asset/products")

    async def get_orderbook(self, symbol: str) -> Dict[str, Any]:
        """Fetch Level 2 Orderbook for a symbol (e.g. BTCTHB, ETHTHB, XAUTTHB)."""
        clean_symbol = symbol.replace("/", "").replace("_", "").replace("-", "").upper()
        return await self._request("POST", "/api/v1/digital-asset/orderbook/lvl2", json_data={"symbol": clean_symbol})

    async def get_live_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch real-time Level 2 Best Bid / Best Ask and Last Trade Price from InnovestX."""
        if not self.is_configured():
            return None
        try:
            res = await self.get_orderbook(symbol)
            if isinstance(res, dict) and res.get("code") == "0000":
                data = res.get("data")
                best_bid = 0.0
                best_ask = 0.0
                last_trade_price = 0.0

                if isinstance(data, list):
                    bids = []
                    asks = []
                    for item in data:
                        p = float(item.get("price", 0))
                        s = item.get("side")
                        if s == 0 and p > 0:
                            bids.append(p)
                        elif s == 1 and p > 0:
                            asks.append(p)
                        if not last_trade_price and "lastTradePrice" in item:
                            try:
                                ltp = float(item["lastTradePrice"])
                                if ltp > 0:
                                    last_trade_price = ltp
                            except Exception:
                                pass
                    if bids:
                        best_bid = max(bids)
                    if asks:
                        best_ask = min(asks)
                elif isinstance(data, dict):
                    bids = data.get("bids", [])
                    asks = data.get("asks", [])
                    if bids:
                        first_b = bids[0]
                        best_bid = float(first_b.get("price") if isinstance(first_b, dict) else (first_b[0] if isinstance(first_b, (list, tuple)) else first_b))
                    if asks:
                        first_a = asks[0]
                        best_ask = float(first_a.get("price") if isinstance(first_a, dict) else (first_a[0] if isinstance(first_a, (list, tuple)) else first_a))
                    if "lastTradePrice" in data:
                        try:
                            last_trade_price = float(data["lastTradePrice"])
                        except Exception:
                            pass

                effective_price = last_trade_price or ((best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else (best_bid or best_ask))
                if effective_price > 0:
                    return {
                        "symbol": symbol,
                        "price": effective_price,
                        "last_trade_price": last_trade_price or effective_price,
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                        "spread": round(best_ask - best_bid, 2) if (best_bid > 0 and best_ask > 0) else 0.0,
                        "exchange": "innovestx",
                    }
        except Exception as e:
            logger.warning(f"[InnovestX] Error in get_live_ticker for {symbol}: {e}")
        return None

    # -----------------------------------------------------------------------
    # Account & Balances
    # -----------------------------------------------------------------------

    async def get_account_balances(self) -> Dict[str, Any]:
        """Fetch account balances (THB, BTC, ETH, USDT, etc.)."""
        return await self._request("GET", "/api/v1/digital-asset/account/balance/inquiry")

    # -----------------------------------------------------------------------
    # Trading & Orders
    # -----------------------------------------------------------------------

    async def get_open_orders(self) -> Dict[str, Any]:
        """Fetch active open orders."""
        return await self._request("GET", "/api/v1/digital-asset/order/open/inquiry")

    async def get_estimate_fee(
        self,
        symbol: str,
        amount: float,
        price: float,
        side: Literal["BUY", "SELL", "buy", "sell"] = "BUY",
    ) -> Dict[str, Any]:
        """
        Estimate the transaction fee for a specified order.
        Endpoint: POST /api/v1/digital-asset/order/fee/inquiry
        side: 0=Buy, 1=Sell
        """
        clean_symbol = symbol.replace("/", "").replace("_", "").replace("-", "").upper()
        is_buy = str(side).upper() == "BUY"
        payload = {
            "symbol": clean_symbol,
            "amount": float(amount),
            "price": float(price),
            "side": 0 if is_buy else 1,
        }
        return await self._request("POST", "/api/v1/digital-asset/order/fee/inquiry", json_data=payload)

    async def get_order_history(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Fetch trade order history."""
        payload = {}
        if symbol:
            payload["symbol"] = symbol.replace("/", "").replace("_", "").upper()
        return await self._request("POST", "/api/v1/digital-asset/order/history/inquiry", json_data=payload)

    async def place_order(
        self,
        symbol: str,
        side: Literal["BUY", "SELL", "buy", "sell"],
        order_type: Literal["LIMIT", "MARKET", "limit", "market"],
        price: float,
        quantity: Optional[float] = None,
        value_thb: Optional[float] = None,
        client_order_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Send a Live Order to InnovestX Digital Asset Exchange.
        side: "BUY" or "SELL" (maps to 0=Buy, 1=Sell)
        order_type: "MARKET" or "LIMIT" (maps to 1=Market, 2=Limit)
        """
        clean_symbol = symbol.replace("/", "").replace("_", "").upper()
        is_buy = side.upper() == "BUY"
        is_market = order_type.upper() == "MARKET"

        payload: Dict[str, Any] = {
            "symbol": clean_symbol,
            "side": 0 if is_buy else 1,
            "orderType": 1 if is_market else 2,
            "timeInForce": 1,  # GTC
        }

        if not is_market:
            payload["limitPrice"] = float(price)

        if quantity is not None and quantity > 0:
            payload["quantity"] = float(quantity)
        elif value_thb is not None and value_thb > 0:
            payload["value"] = float(value_thb)
        else:
            raise ValueError("Either quantity or value_thb must be specified")

        if client_order_id:
            payload["clientOrderID"] = int(client_order_id)

        logger.info(f"[InnovestX] Placing Live Order: {payload}")
        return await self._request("POST", "/api/v1/digital-asset/order/send", json_data=payload)

    async def cancel_order(self, order_id: int) -> Dict[str, Any]:
        """Cancel an open order by numeric orderId."""
        payload = {"orderId": int(order_id)}
        logger.info(f"[InnovestX] Cancelling Order #{order_id}")
        return await self._request("POST", "/api/v1/digital-asset/order/cancel", json_data=payload)

    async def test_connection(self) -> Dict[str, Any]:
        """Verify API authentication and fetch balances."""
        if not self.is_configured():
            return {"connected": False, "message": "InnovestX API Key or Secret is not configured in .env"}

        try:
            res = await self.get_account_balances()
            if isinstance(res, dict) and res.get("code") == "0000":
                balances = res.get("data", [])
                active_balances = [
                    b for b in balances
                    if float(b.get("amount", 0)) > 0 or float(b.get("hold", 0)) > 0
                ]
                return {
                    "connected": True,
                    "broker": "InnovestX (SCBX)",
                    "status": "online",
                    "total_assets": len(balances),
                    "active_assets": active_balances,
                    "message": "Authenticated successfully with InnovestX Digital Asset Open API",
                }
            else:
                return {
                    "connected": False,
                    "status": "failed",
                    "error": res.get("message") or res.get("error", "Authentication error"),
                }
        except Exception as e:
            return {"connected": False, "error": str(e)}
