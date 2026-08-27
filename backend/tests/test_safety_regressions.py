from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.core.json_store import read_json, update_json, write_json
from app.core.live_session import LiveSessionManager
from app.core.runtime_config import get_runtime_trading_mode
from app.core.url_security import validate_service_url
from app.engines.ai_engine import AIEngine
from app.engines.indicators import AdvancedIndicatorsEngine
from app.engines.execution_engine import ExecutionEngine
from app.engines.risk_engine import RiskEngine
from app.engines.smc_engine import SMCSignal
from app.services.event_trigger import _confirmed_invalidation_direction


def _signal(**overrides) -> SMCSignal:
    values = {
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "bias": "bullish",
        "direction": "long",
        "entry": 100.0,
        "stop_loss": 98.0,
        "take_profit": 104.0,
        "risk_reward": 999.0,
    }
    values.update(overrides)
    return SMCSignal(**values)


def test_risk_engine_recomputes_rr_instead_of_trusting_signal():
    result = RiskEngine().evaluate(_signal(), account_balance=10_000.0)
    assert result.approved is True
    assert result.risk_reward == 2.0


def test_risk_engine_rejects_wide_stop_and_invalid_numbers():
    wide = RiskEngine().evaluate(_signal(stop_loss=90.0, take_profit=120.0))
    invalid = RiskEngine().evaluate(_signal(entry=float("nan")))
    assert wide.approved is False
    assert "too wide" in (wide.rejection_reason or "")
    assert invalid.approved is False
    assert "finite" in (invalid.rejection_reason or "")


def test_risk_reducing_invalidation_is_not_blocked_by_entry_regime():
    signal = _signal(
        direction="short",
        choch=True,
        confluence=80,
        indicator_decision={"ready": True},
        market_regime={
            "regime": "compression",
            "ready": True,
            "policy": {"entry_allowed": False, "risk_multiplier": 0.0},
        },
    )

    assert _confirmed_invalidation_direction(signal) == "short"


def test_invalidation_fails_closed_without_ready_indicator_data():
    signal = _signal(
        direction="short",
        choch=True,
        confluence=90,
        indicator_decision={"ready": False},
    )

    assert _confirmed_invalidation_direction(signal) == "wait"


def test_json_store_updates_are_atomic_across_threads(tmp_path):
    store = tmp_path / "counter.json"
    write_json(store, {"count": 0})

    def increment(_index: int) -> None:
        def mutate(data: dict) -> None:
            data["count"] += 1
        update_json(store, dict, mutate)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(increment, range(100)))
    assert read_json(store, dict) == {"count": 100}


def test_json_store_reads_legacy_utf8_bom(tmp_path):
    store = tmp_path / "legacy.json"
    store.write_bytes(b"\xef\xbb\xbf{\"currency\": \"USD\"}")

    assert read_json(store, dict) == {"currency": "USD"}


def test_service_url_rejects_credentials_and_unapproved_hosts():
    allowed = {"localhost", "api.example.com"}
    with pytest.raises(ValueError):
        validate_service_url("http://user:pass@localhost:1234", allowed_hosts=allowed, allow_private_ip=True)
    with pytest.raises(ValueError):
        validate_service_url("https://evil.example.net/v1", allowed_hosts=allowed)
    assert validate_service_url(
        "http://localhost:1234/v1", allowed_hosts=allowed, allow_private_ip=True
    ) == "http://localhost:1234/v1"


def test_missing_volume_never_creates_absorption_signal():
    rows = 20
    df = pd.DataFrame({
        "open": [100.0] * rows,
        "high": [101.0] * rows,
        "low": [99.0] * rows,
        "close": [100.5] * rows,
    })
    result = AdvancedIndicatorsEngine.compute_volume_delta(df)
    assert result.is_absorption is False
    assert result.absorption_type is None
    assert "unavailable" in result.description.lower()


def test_ai_parser_clamps_confidence_and_defaults_to_wait():
    engine = AIEngine()
    malformed = engine._parse_response('{"confidence": 900, "reasoning": "no decision"}')
    assert malformed.recommendation == "wait"
    assert malformed.confidence == 100


@pytest.mark.anyio
async def test_ai_chat_cannot_override_rejected_strategy_gate():
    reply = await AIEngine().chat(
        [{"role": "user", "content": "ช่วยวิเคราะห์ว่าควรเปิด Long ตอนนี้ไหม"}],
        context={
            "symbol": "SOL/USDT",
            "timeframe": "1h",
            "price": 100.0,
            "bias": "bullish",
            "confluence": 72,
            "strategy_approved": False,
            "strategy_direction": "wait",
            "setup_direction": "long",
            "rejection_reasons": [
                "Confluence 72 < minimum 75.0",
                "Liquidity sweep required but not detected",
            ],
        },
    )

    assert "WAIT" in reply
    assert "Liquidity sweep" in reply
    assert "ไม่ข้าม Strategy Gate" in reply


def test_live_session_expires_and_cannot_survive_process_restart():
    clock = [datetime(2026, 8, 26, tzinfo=timezone.utc)]
    manager = LiveSessionManager(now=lambda: clock[0])
    token, session = manager.issue(
        broker="innovestx",
        api_key="test-api-key",
        ttl_minutes=1,
    )
    assert manager.get(token) == session
    assert manager.get(token, api_key="different-api-key") is None

    restarted_manager = LiveSessionManager(now=lambda: clock[0])
    assert restarted_manager.get(token) is None

    clock[0] += timedelta(minutes=1, seconds=1)
    assert manager.get(token) is None


@pytest.mark.anyio
async def test_legacy_execution_and_runtime_mode_fail_closed_to_paper():
    assert get_runtime_trading_mode("live") == "paper"
    with pytest.raises(RuntimeError, match="cannot place Live orders"):
        await ExecutionEngine().place_order(
            mode="live",
            symbol="BTC/THB",
            direction="long",
            entry=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            position_size=1.0,
            exchange="innovestx",
            order_type="limit",
        )
