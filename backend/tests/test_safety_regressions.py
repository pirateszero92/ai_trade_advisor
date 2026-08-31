from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
import yaml

from app.core.json_store import read_json, update_json, write_json
from app.core.live_session import LiveSessionManager
from app.core.runtime_config import get_runtime_trading_mode
from app.core.url_security import validate_service_url
from app.engines.ai_engine import AIEngine
from app.engines.indicators import AdvancedIndicatorsEngine
from app.engines.execution_engine import ExecutionEngine
from app.engines.risk_engine import RiskEngine
from app.engines.smc_engine import SMCSignal
from app.services.event_trigger import (
    _confirmed_invalidation_direction,
    _rejected_strategy_advice,
)


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


def test_rejected_advice_is_wait_only_and_cannot_leak_scenario_actions():
    advice = _rejected_strategy_advice(["Confluence below threshold"])

    assert "WAIT" in advice
    assert "BUY" not in advice.upper()
    assert "SHORT" not in advice.upper()
    assert "เปิด" not in advice


def test_deployed_strategy_config_preserves_conservative_invariants():
    path = Path(__file__).resolve().parents[1] / "config" / "strategy.yaml"
    strategy = yaml.safe_load(path.read_text(encoding="utf-8"))
    policies = strategy["regime_policy"]["policies"]

    assert strategy["filters"]["min_rr"] >= 2.0
    assert strategy["long_conditions"]["price_zone"] == "discount_or_eq"
    assert strategy["short_conditions"]["price_zone"] == "premium_or_eq"
    assert policies["ranging"]["require_liquidity_sweep"] is True
    assert policies["volatile"]["min_confluence"] >= 82
    assert policies["volatile"]["min_rr"] >= 2.5


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


def test_ai_decision_context_excludes_htf_and_free_form_market_context():
    signal = _signal(htf_bias="bearish")

    prompt = AIEngine()._build_context_message(
        signal,
        portfolio_state=None,
        market_context="Confirm with 4H bearish structure",
    )

    assert "Execution-Timeframe Bias" in prompt
    assert "HTF" not in prompt
    assert "bearish structure" not in prompt
    assert "4H" not in prompt


def test_ai_decision_context_includes_reaction_as_observation_only():
    signal = _signal(timeframe="15m")
    signal.reaction = {
        "scenario_id": "S8_SUPPORT_TOUCH_WAIT",
        "archetype": "support_reaction",
        "reaction_state": "TOUCH_WAIT",
        "reaction_evidence": {
            "timeframe": "15m",
            "support_bottom": 0.1926,
            "support_top": 0.1932,
            "delta_ratio": -0.23,
        },
        "actionable": False,
    }

    prompt = AIEngine()._build_context_message(
        signal, portfolio_state=None, market_context=None
    )

    assert "Independent 15M Closed-Candle Reaction" in prompt
    assert "S8_SUPPORT_TOUCH_WAIT" in prompt
    assert "TOUCH_WAIT" in prompt
    assert "cannot authorize BUY/SELL" in prompt


def test_blocked_ai_reply_labels_reaction_as_watch_only():
    reply = AIEngine._blocked_strategy_reply(
        {
            "setup_direction": "short",
            "bias": "bearish",
            "rejection_reasons": ["Scenario is observation-only"],
            "reaction_state": "TOUCH_WAIT",
        }
    )

    assert "WAIT" in reply
    assert "TOUCH_WAIT" in reply
    assert "ใช้เฝ้าดูเท่านั้น" in reply


def test_ai_cross_timeframe_guard_allows_only_execution_timeframe():
    engine = AIEngine()

    assert engine._contains_cross_timeframe_reference("15m BOS confirmed", "15m") is False
    assert engine._contains_cross_timeframe_reference("Confirm with 4H", "15m") is True
    assert engine._contains_cross_timeframe_reference("MTF alignment", "15m") is True


@pytest.mark.anyio
async def test_ai_analysis_rejects_provider_output_using_another_timeframe(monkeypatch):
    engine = AIEngine()
    engine.active_provider = "local"

    async def fake_dispatch(provider, _messages):
        if provider == "local":
            return '{"recommendation":"buy","confidence":90,"reasoning":"4H confirms the trade"}'
        return '{"recommendation":"buy","confidence":80,"reasoning":"Execution structure is valid"}'

    monkeypatch.setattr(engine, "_dispatch", fake_dispatch)
    result = await engine.analyze(_signal(timeframe="15m"))

    assert result.provider == "gemini"
    assert result.recommendation == "buy"
    assert "4H" not in result.reasoning


@pytest.mark.anyio
async def test_ai_chat_refuses_cross_timeframe_decision_before_provider_call(monkeypatch):
    engine = AIEngine()

    async def fail_dispatch(_provider, _messages):
        raise AssertionError("provider must not receive a cross-timeframe decision request")

    monkeypatch.setattr(engine, "_dispatch", fail_dispatch)
    reply = await engine.chat(
        [{"role": "user", "content": "ใช้ 4H ยืนยันว่าควร Long ไหม"}],
        context={"symbol": "BTC/USDT", "timeframe": "15m", "strategy_approved": True},
    )

    assert "ไม่ใช้ข้อมูลข้าม Timeframe" in reply


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
