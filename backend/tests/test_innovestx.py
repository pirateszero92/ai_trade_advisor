"""Unit and integration tests for InnovestX Live Broker Client."""

import pytest
from app.engines.innovestx_client import InnovestXClient
from app.core.config import get_settings


def test_innovestx_signature_generation():
    client = InnovestXClient(
        api_key="test_api_key_12345",
        api_secret="test_secret_67890",
        base_url="https://api.innovestxonline.com",
    )
    sig = client._generate_signature(
        method="POST",
        path="/api/v1/digital-asset/orderbook/lvl2",
        query="",
        content_type="application/json",
        request_uid="019d1bae-e2f1-42d9-b9e8-23d495dbe9f9",
        timestamp="1567755304968",
        body_str='{"symbol":"BTCTHB"}',
    )
    assert isinstance(sig, str)
    assert len(sig) == 64  # SHA-256 hex string


@pytest.mark.anyio
async def test_innovestx_live_status():
    client = InnovestXClient()
    if not client.is_configured():
        pytest.skip("InnovestX credentials not set")
    
    res = await client.test_connection()
    assert isinstance(res, dict)
    assert res.get("connected") is True
    assert res.get("broker") == "InnovestX (SCBX)"
    assert res.get("status") == "online"


@pytest.mark.anyio
async def test_innovestx_symbols_fetch():
    client = InnovestXClient()
    if not client.is_configured():
        pytest.skip("InnovestX credentials not set")

    res = await client.get_symbols()
    assert isinstance(res, dict)
    assert res.get("code") == "0000"
    symbols = res.get("data", [])
    assert len(symbols) > 0
    symbol_names = [s.get("symbol") for s in symbols]
    assert "BTCTHB" in symbol_names
    assert "ETHTHB" in symbol_names
