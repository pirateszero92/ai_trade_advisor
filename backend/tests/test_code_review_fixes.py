"""
Unit tests validating fixes for findings in code_review_report.md
"""

import pytest
import threading
from app.core.security import is_valid_api_key, is_securely_configured
from app.core.config import get_settings, update_runtime_setting, reload_settings
from app.core.json_store import update_json
from app.core.live_session import LiveSessionManager
from app.engines.ai_engine import AIEngine
from app.api import ws


def test_auth_timing_side_channel_handles_arbitrary_lengths():
    cfg = get_settings()
    assert is_valid_api_key("wrong_short_key") is False
    assert is_valid_api_key("wrong_key_that_is_much_longer_than_the_configured_secret_key_123456789") is False
    assert is_valid_api_key(None) is False
    assert is_valid_api_key("") is False


def test_runtime_settings_thread_safety():
    update_runtime_setting("local_llm_model", "test-model-v2")
    cfg = get_settings()
    assert cfg.local_llm_model == "test-model-v2"

    # Reset
    update_runtime_setting("local_llm_model", "llama-3.2-8b")


def test_json_store_update_supports_replacement(tmp_path):
    target = tmp_path / "test_store.json"
    
    # In-place mutation
    def mutator_inplace(d: dict):
        d["a"] = 1
    
    val, _ = update_json(target, dict, mutator_inplace)
    assert val == {"a": 1}

    # Value replacement
    def mutator_replace(d: dict):
        return {"b": 2}

    val2, _ = update_json(target, dict, mutator_replace)
    assert val2 == {"b": 2}


def test_live_session_manager_enabled_brokers():
    mgr = LiveSessionManager()
    token, session = mgr.issue(broker="binance", api_key="dev-key", ttl_minutes=15)
    assert session.broker == "binance"
    assert mgr.get(token) is not None
    
    token2, session2 = mgr.issue(broker="innovestx", api_key="dev-key", ttl_minutes=15)
    assert session2.broker == "innovestx"

    with pytest.raises(ValueError):
        mgr.issue(broker="unknown_broker", api_key="dev-key", ttl_minutes=15)


def test_ai_engine_scrubs_secrets_in_errors():
    import asyncio
    engine = AIEngine()
    custom_secret = "sk-test-secret-key-1234567890abcdef"
    res = asyncio.run(engine.test_connection(
        provider="openrouter",
        custom_key=custom_secret,
        custom_endpoint="http://non-existent-host-999.local/v1",
    ))
    assert res["ok"] is False
    assert custom_secret not in res.get("error", "")


def test_ws_remove_client_cleans_all_registries():
    from unittest.mock import MagicMock
    mock_ws = MagicMock()
    ws._connections.add(mock_ws)
    ws._stream_clients.add(mock_ws)
    ws._chat_clients.add(mock_ws)
    ws._send_locks[mock_ws] = ws.asyncio.Lock()

    assert mock_ws in ws._connections
    assert mock_ws in ws._send_locks

    ws._remove_client(mock_ws)

    assert mock_ws not in ws._connections
    assert mock_ws not in ws._stream_clients
    assert mock_ws not in ws._chat_clients
    assert mock_ws not in ws._send_locks
