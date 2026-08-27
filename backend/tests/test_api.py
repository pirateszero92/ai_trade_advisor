"""API integration tests for FastAPI backend."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import get_settings
from app.api import live_api, trades as trades_api
from app.core.json_store import read_json, write_json
from app.core.live_session import live_session_manager


@pytest.fixture(autouse=True)
def isolated_trade_store(tmp_path, monkeypatch):
    """API tests must never mutate the user's runtime portfolio files."""
    trades_file = tmp_path / "paper_trades.json"
    live_trades_file = tmp_path / "live_trades.json"
    legacy_trades_file = tmp_path / "legacy_trades.json"
    paper_file = tmp_path / "paper.json"
    write_json(trades_file, {})
    write_json(live_trades_file, {})
    write_json(legacy_trades_file, {})
    write_json(paper_file, {"initial_capital": 100000.0, "currency": "USD"})
    monkeypatch.setattr(trades_api, "TRADES_STORE_FILE", trades_file)
    monkeypatch.setattr(trades_api, "PAPER_TRADES_STORE_FILE", trades_file)
    monkeypatch.setattr(trades_api, "LIVE_TRADES_STORE_FILE", live_trades_file)
    monkeypatch.setattr(trades_api, "LEGACY_TRADES_STORE_FILE", legacy_trades_file)
    monkeypatch.setattr(trades_api, "PAPER_CONFIG_FILE", paper_file)
    monkeypatch.setattr(trades_api, "_trades", {})
    cfg = get_settings()
    original_secret = cfg.app_secret_key
    cfg.app_secret_key = "phase-zero-test-secret-key-000000000000"
    live_session_manager.revoke_all()
    yield
    live_session_manager.revoke_all()
    cfg.app_secret_key = original_secret


@pytest.mark.anyio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.2"


@pytest.mark.anyio
async def test_phase4_market_data_status_is_authenticated_and_structured():
    cfg = get_settings()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unauthorized = await client.get("/api/v1/market-data/status")
        response = await client.get(
            "/api/v1/market-data/status",
            headers={"X-API-Key": cfg.app_secret_key},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["phase"] == 4
    assert payload["status"] in {"healthy", "degraded"}
    assert payload["providers"]["binance"]["transport"] == "websocket"


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
        missing = await client.get("/api/v1/trades/account")
        assert missing.status_code == 401


@pytest.mark.anyio
async def test_llm_test_returns_structured_failure_for_blocked_endpoint():
    cfg = get_settings()
    headers = {"X-API-Key": cfg.app_secret_key}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/settings/llm/test",
            json={
                "provider": "local",
                "endpoint": "http://blocked.example.invalid:11434",
                "model": "test-model",
            },
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "not allowed" in response.json()["error"]


@pytest.mark.anyio
async def test_llm_config_rejects_blocked_endpoint_as_validation_error():
    cfg = get_settings()
    headers = {"X-API-Key": cfg.app_secret_key}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/settings/llm/config",
            json={
                "provider": "local",
                "local_endpoint": "http://blocked.example.invalid:11434",
            },
            headers=headers,
        )

    assert response.status_code == 422
    assert "not in the configured allowlist" in response.json()["detail"]


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
            "size": 0.1,
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
            "size": 0.1,
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


@pytest.mark.anyio
async def test_pending_trade_can_be_cancelled():
    cfg = get_settings()
    headers = {"X-API-Key": cfg.app_secret_key}
    trade_id = "pending-cancel-test"
    pending_trade = {
        "id": trade_id,
        "status": "pending",
        "symbol": "BTC/USDT",
        "direction": "long",
        "entry": 50000.0,
        "size": 0.1,
        "pnl": 0.0,
        "pnl_pct": 0.0,
    }
    write_json(trades_api.TRADES_STORE_FILE, {trade_id: pending_trade})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/trades/{trade_id}/close",
            json={"reason": "Order Cancelled"},
            headers=headers,
        )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "cancelled"
    assert result["close_reason"] == "Order Cancelled"
    assert result["close_price"] is None
    assert result["pnl"] == 0.0


@pytest.mark.anyio
async def test_cancelled_pending_trade_cannot_fill():
    cfg = get_settings()
    headers = {"X-API-Key": cfg.app_secret_key}
    trade_id = "pending-race-test"
    write_json(trades_api.TRADES_STORE_FILE, {
        trade_id: {
            "id": trade_id,
            "status": "pending",
            "mode": "paper",
            "symbol": "BTC/USDT",
            "direction": "long",
            "entry": 50000.0,
            "size": 0.1,
        }
    })
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/trades/{trade_id}/close",
            json={"reason": "Order Cancelled"},
            headers=headers,
        )
    assert response.status_code == 200
    assert trades_api.fill_pending_trade_sync(trade_id, 49900.0) is None
    assert trades_api.get_all_trades()[trade_id]["status"] == "cancelled"


@pytest.mark.anyio
async def test_limit_order_fails_closed_without_live_quote(monkeypatch):
    from app.engines.market_data import MarketDataEngine

    async def no_quote(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(MarketDataEngine, "get_ticker_24h", no_quote)
    cfg = get_settings()
    headers = {"X-API-Key": cfg.app_secret_key}
    request = {
        "symbol": "BTC/USDT",
        "direction": "long",
        "order_type": "limit",
        "entry": 50000.0,
        "stop_loss": 49500.0,
        "take_profit": 51500.0,
        "size": 0.1,
        "mode": "paper",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/trades/place", json=request, headers=headers)
    assert response.status_code == 503
    assert trades_api.get_all_trades() == {}


@pytest.mark.anyio
async def test_market_order_idempotency_does_not_duplicate_trade():
    cfg = get_settings()
    headers = {"X-API-Key": cfg.app_secret_key}
    request = {
        "symbol": "BTC/USDT",
        "direction": "long",
        "order_type": "market",
        "entry": 50000.0,
        "stop_loss": 49500.0,
        "take_profit": 51500.0,
        "size": 0.1,
        "mode": "paper",
        "idempotency_key": "same-request-12345",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/trades/place", json=request, headers=headers)
        second = await client.post("/api/v1/trades/place", json=request, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(trades_api.get_all_trades()) == 1


@pytest.mark.anyio
async def test_canonical_paper_order_never_calls_live_engine(monkeypatch):
    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("paper request crossed into live broker execution")

    monkeypatch.setattr(trades_api._live_execution, "place_order", fail_if_called)
    sentinel_live_trade = {
        "id": "live-sentinel",
        "mode": "live",
        "broker": "innovestx",
        "status": "open",
    }
    write_json(
        trades_api.LIVE_TRADES_STORE_FILE,
        {sentinel_live_trade["id"]: sentinel_live_trade},
    )
    cfg = get_settings()
    request = {
        "symbol": "BTC/USDT",
        "direction": "long",
        "entry": 50000.0,
        "stop_loss": 49500.0,
        "take_profit": 51500.0,
        "size": 0.1,
        "mode": "paper",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/paper/orders",
            json=request,
            headers={"X-API-Key": cfg.app_secret_key},
        )
    assert response.status_code == 200
    assert response.json()["mode"] == "paper"
    assert read_json(trades_api.LIVE_TRADES_STORE_FILE, dict) == {
        sentinel_live_trade["id"]: sentinel_live_trade
    }


@pytest.mark.anyio
async def test_generic_order_rejects_live_mode_before_any_broker_call(monkeypatch):
    called = False

    async def broker_call(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"code": "0000", "data": {}}

    monkeypatch.setattr(trades_api._live_execution.innovestx, "place_order", broker_call)
    cfg = get_settings()
    request = {
        "symbol": "BTC/THB",
        "direction": "long",
        "entry": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "size": 1.0,
        "mode": "live",
        "exchange": "innovestx",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/trades/place",
            json=request,
            headers={"X-API-Key": cfg.app_secret_key},
        )
    assert response.status_code == 422
    assert called is False


@pytest.mark.anyio
async def test_live_session_is_required_before_real_money_mutation(monkeypatch):
    called = False

    async def broker_call(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"code": "0000", "data": {"orderId": 123}}

    monkeypatch.setattr(trades_api._live_execution.innovestx, "place_order", broker_call)
    cfg = get_settings()
    request = {
        "symbol": "BTC/THB",
        "side": "BUY",
        "order_type": "LIMIT",
        "price": 100.0,
        "quantity": 0.01,
        "client_order_id": 123456,
        "live_confirmation": "I_UNDERSTAND_THIS_IS_LIVE",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/live/orders/innovestx",
            json=request,
            headers={"X-API-Key": cfg.app_secret_key},
        )
    assert response.status_code == 401
    assert called is False


@pytest.mark.anyio
async def test_live_session_preflight_token_and_revoke_flow(monkeypatch):
    async def successful_preflight(_broker):
        return {"broker": "innovestx", "connected": True, "status": "online"}

    place_called = False

    async def broker_call(*_args, **_kwargs):
        nonlocal place_called
        place_called = True
        return {"code": "0000", "data": {"orderId": 987}}

    async def cancel_call(*_args, **_kwargs):
        return {"code": "0000", "data": {}}

    monkeypatch.setattr(live_api, "_preflight_broker", successful_preflight)
    monkeypatch.setattr(trades_api._live_execution.innovestx, "place_order", broker_call)
    monkeypatch.setattr(trades_api._live_execution.innovestx, "cancel_order", cancel_call)
    cfg = get_settings()
    api_headers = {"X-API-Key": cfg.app_secret_key}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        opened = await client.post(
            "/api/v1/live/session",
            json={
                "broker": "innovestx",
                "confirmation": "ENABLE_LIVE_TRADING",
                "ttl_minutes": 15,
            },
            headers=api_headers,
        )
        assert opened.status_code == 200
        token = opened.json()["session_token"]
        live_headers = {**api_headers, "X-Live-Session-Token": token}

        status = await client.get("/api/v1/live/session", headers=live_headers)
        assert status.status_code == 200
        assert status.json()["mode"] == "live"

        order = await client.post(
            "/api/v1/live/orders/innovestx",
            json={
                "symbol": "BTC/THB",
                "side": "BUY",
                "order_type": "LIMIT",
                "price": 100.0,
                "quantity": 0.01,
                "client_order_id": 987654,
                "live_confirmation": "I_UNDERSTAND_THIS_IS_LIVE",
            },
            headers=live_headers,
        )
        assert order.status_code == 501
        assert place_called is False

        cancelled = await client.post(
            "/api/v1/live/orders/innovestx/cancel",
            json={
                "order_id": 987,
                "live_confirmation": "I_UNDERSTAND_THIS_IS_LIVE",
            },
            headers=live_headers,
        )
        assert cancelled.status_code == 200

        closed = await client.delete("/api/v1/live/session", headers=live_headers)
        assert closed.status_code == 200
        assert closed.json()["mode"] == "paper"

        denied = await client.post(
            "/api/v1/live/orders/innovestx",
            json={
                "symbol": "BTC/THB",
                "side": "BUY",
                "order_type": "LIMIT",
                "price": 100.0,
                "quantity": 0.01,
                "client_order_id": 987655,
                "live_confirmation": "I_UNDERSTAND_THIS_IS_LIVE",
            },
            headers=live_headers,
        )
        assert denied.status_code == 401


@pytest.mark.anyio
async def test_persistent_global_live_mode_is_disabled():
    cfg = get_settings()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/settings/trading-mode",
            json={"mode": "live"},
            headers={"X-API-Key": cfg.app_secret_key},
        )
    assert response.status_code == 409


@pytest.mark.anyio
async def test_live_kill_switch_revokes_every_session():
    cfg = get_settings()
    live_session_manager.issue(
        broker="innovestx",
        api_key=cfg.app_secret_key,
        ttl_minutes=15,
    )
    live_session_manager.issue(
        broker="innovestx",
        api_key=cfg.app_secret_key,
        ttl_minutes=15,
    )
    assert live_session_manager.active_count() == 2

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/live/kill-switch",
            json={"confirmation": "DISABLE_LIVE_TRADING"},
            headers={"X-API-Key": cfg.app_secret_key},
        )
    assert response.status_code == 200
    assert response.json()["revoked_sessions"] == 2
    assert live_session_manager.active_count() == 0


def test_local_paper_helpers_refuse_live_ledger_mutations():
    trade_id = "live-must-remain-broker-owned"
    live_trade = {
        "id": trade_id,
        "mode": "live",
        "broker": "innovestx",
        "status": "open",
        "symbol": "BTC/THB",
        "direction": "long",
        "entry": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "size": 1.0,
    }
    write_json(trades_api.LIVE_TRADES_STORE_FILE, {trade_id: live_trade})

    assert trades_api.auto_close_trade_sync(trade_id, "must not close", 105.0) is None
    assert trades_api.update_trade_sl_sync(trade_id, 100.0) is None
    persisted = read_json(trades_api.LIVE_TRADES_STORE_FILE, dict)[trade_id]
    assert persisted["status"] == "open"
    assert persisted["stop_loss"] == 95.0
