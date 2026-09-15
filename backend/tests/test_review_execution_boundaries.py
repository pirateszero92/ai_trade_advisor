"""Regression tests for execution authority and external data quality."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import httpx
import numpy as np
import pandas as pd
import pytest

from app.engines.ai_engine import AIEngine
from app.engines.sentiment_derivatives_engine import SentimentDerivativesEngine as Derivatives
from app.engines.sentiment_derivatives_engine import DerivativesSentimentResult
from app.engines.smc_engine import SMCSignal, Zone
from app.engines.strategy_engine import StrategyEngine, StrategyResult, DEFAULT_STRATEGY
from app.engines.vpin_engine import VPINEngine
from app.services.execution_analysis import ExecutionAnalysisService, execution_analyses


def candidate():
    return SMCSignal(
        symbol="BTC/USDT", timeframe="1h", bias="bullish", direction="long",
        current_price=100, entry=100, stop_loss=99, take_profit=103,
        risk_reward=3, confluence=100, in_discount=True,
        order_block=Zone("ob", "bullish", top=101, bottom=99),
        scenario={"scenario_id": "TEST_CONFIRMED", "actionable": True},
        volume_quality="exchange_aggressor",
        tri_core_setup={"actionable": True, "direction": "long", "grade": "A",
                        "smc_confirmed": True, "cvd_confirmed": True,
                        "flow_confirmed": True},
    )


@pytest.mark.parametrize("scenario", [
    {}, {"actionable": False}, {"actionable": "true"},
    {"actionable": False, "entry_status": "TOUCH_WAIT"},
    {"actionable": False, "entry_status": "CONFIRMED_NO_ENTRY"},
    *[{"actionable": False, "entry_status": "PRESSURE_WARNING", "pressure_warning": {"score": n}}
      for n in (0, 50, 79, 80, 100)],
])
def test_legacy_scenario_never_changes_tri_core_authority(scenario):
    engine = StrategyEngine(strategy_config=deepcopy(DEFAULT_STRATEGY))
    signal = candidate()
    assert engine.evaluate(signal).approved
    signal.scenario = scenario
    result = engine.evaluate(signal)
    assert result.approved
    assert result.direction == "long"


def test_estimated_vpin_cannot_block_but_measured_imbalance_can():
    frame = pd.DataFrame({"open": np.ones(100), "close": np.full(100, 2.0), "volume": np.full(100, 100.0)})
    estimate = VPINEngine().calculate(frame)
    assert estimate.status == "estimated_advisory"
    assert not estimate.toxic_flow_detected
    frame["buy_volume"] = 95.0
    frame["sell_volume"] = 5.0
    measured = VPINEngine().calculate(frame)
    assert measured.status == "ok"
    assert measured.toxic_flow_detected


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [429, 500, "timeout", "empty"])
async def test_partial_derivatives_are_not_reported_healthy(monkeypatch, failure):
    def handler(request):
        if "fundingRate" in str(request.url):
            if failure == "timeout":
                raise httpx.ReadTimeout("timeout", request=request)
            return httpx.Response(200 if failure == "empty" else failure, json=[])
        return httpx.Response(200, json=[{}, {}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(Derivatives, "_client", client)
        result = await Derivatives._fetch_binance_derivatives("BTCUSDT", "BTC/USDT")
    assert result.status != "ok"
    assert result.sentiment_bias == "NEUTRAL"
    assert "timeout" not in (result.crowd_risk_warning or "")


@pytest.mark.anyio
async def test_derivatives_cache_is_isolated_by_caller_and_exchange(monkeypatch):
    calls = []
    async def binance(*args):
        calls.append("binance")
        return DerivativesSentimentResult(symbol="BTC/USDT", oi_delta_pct_1h=2, details={"x": [1]})
    async def bybit(*args):
        calls.append("bybit")
        return DerivativesSentimentResult(symbol="BTC/USDT", exchange="bybit", status="partial")
    monkeypatch.setattr(Derivatives, "_cache", {})
    monkeypatch.setattr(Derivatives, "_fetch_binance_derivatives", binance)
    monkeypatch.setattr(Derivatives, "_fetch_bybit_derivatives", bybit)
    first = await Derivatives.get_sentiment("BTC/USDT", price_change_pct_1h=1)
    second = await Derivatives.get_sentiment("BTC/USDT", price_change_pct_1h=-1)
    first.details["x"].append(2)
    payload = second.to_dict()
    payload["details"]["x"].append(3)
    assert first.sentiment_bias == "BULLISH_EXPANSION"
    assert second.sentiment_bias == "BEARISH_EXPANSION"
    assert second.details == {"x": [1]}
    third = await Derivatives.get_sentiment("BTC/USDT", exchange="bybit")
    assert third.exchange == "bybit"
    assert calls == ["binance", "bybit"]


@pytest.mark.anyio
async def test_ai_ignores_client_approval_and_uses_server_snapshot(monkeypatch):
    calls = []
    async def canonical(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            signal=candidate(), timeframe="1h", frame=pd.DataFrame({"close": [100.0]}),
            strategy=StrategyResult(approved=False, setup_direction="long", rejection_reasons=["SERVER BLOCK"]),
        )
    monkeypatch.setattr(execution_analyses, "get", canonical)
    reply = await AIEngine().chat(
        [{"role": "user", "content": "Should I buy?"}],
        {"symbol": "BTC/USDT", "market_type": "crypto", "exchange": "bybit",
         "timeframe": "15m", "strategy_approved": True, "confluence": 100},
    )
    assert "WAIT" in reply and "SERVER BLOCK" in reply
    assert calls == [{"symbol": "BTC/USDT", "market_type": "crypto", "exchange": "bybit", "entry_mode": "limit"}]


@pytest.mark.anyio
async def test_non_execution_timeframe_is_rejected_before_fetch(monkeypatch):
    service = ExecutionAnalysisService()
    monkeypatch.setattr(service, "_config_snapshot", lambda: {})  # default execution is 15m
    with pytest.raises(ValueError, match="execution timeframe"):
        await service.get(symbol="BTC/USDT", market_type="crypto", exchange="binance", entry_mode="limit", timeframe="4h")
    assert not service._cache


@pytest.mark.anyio
async def test_idle_locks_are_collectable():
    import gc
    service = ExecutionAnalysisService()
    lock = service._locks.setdefault(("test",), asyncio.Lock())
    async with lock:
        assert len(service._locks) == 1
    del lock
    gc.collect()
    assert len(service._locks) == 0
