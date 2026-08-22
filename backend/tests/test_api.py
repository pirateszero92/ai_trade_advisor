"""API integration tests for FastAPI backend."""

import pytest
import os
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import get_settings


@pytest.mark.anyio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.1"


@pytest.mark.anyio
async def test_get_active_prompt():
    cfg = get_settings()
    headers = {"X-API-Key": cfg.app_secret_key}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/settings/prompts/active", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "name" in data
        assert "content" in data
        assert "Apex" in data["content"] or "trading" in data["content"].lower()


@pytest.mark.anyio
async def test_auth_enforcement():
    cfg = get_settings()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Valid Key -> Success
        resp = await client.get("/api/v1/trades/account", headers={"X-API-Key": cfg.app_secret_key})
        assert resp.status_code == 200


@pytest.mark.anyio
async def test_trade_invalid_sl_tp_rejection():
    cfg = get_settings()
    headers = {"X-API-Key": cfg.app_secret_key}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # LONG with SL above Entry -> must return 400
        bad_req = {
            "symbol": "BTC/USDT",
            "direction": "long",
            "entry": 50000.0,
            "stop_loss": 51000.0,  # Invalid: SL is above Entry
            "take_profit": 55000.0,
            "risk_pct": 1.0,
            "mode": "paper"
        }
        resp = await client.post("/api/v1/trades/place", json=bad_req, headers=headers)
        assert resp.status_code == 400
        assert "Invalid SL/TP" in resp.json().get("detail", "")


@pytest.mark.anyio
async def test_trade_double_close_prevention():
    cfg = get_settings()
    headers = {"X-API-Key": cfg.app_secret_key}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Place a valid paper trade
        req = {
            "symbol": "BTC/USDT",
            "direction": "long",
            "entry": 50000.0,
            "stop_loss": 49000.0,
            "take_profit": 53000.0,
            "risk_pct": 1.0,
            "mode": "paper"
        }
        resp = await client.post("/api/v1/trades/place", json=req, headers=headers)
        assert resp.status_code == 200
        trade_id = resp.json()["id"]

        # 2. Close trade once -> 200 OK
        close_req = {"close_price": 52000.0, "reason": "TP target reached"}
        resp_close1 = await client.post(f"/api/v1/trades/{trade_id}/close", json=close_req, headers=headers)
        assert resp_close1.status_code == 200
        assert resp_close1.json()["status"] == "closed"

        # 3. Attempt to close again -> 409 Conflict
        resp_close2 = await client.post(f"/api/v1/trades/{trade_id}/close", json=close_req, headers=headers)
        assert resp_close2.status_code == 409
        assert "already closed" in resp_close2.json().get("detail", "")
