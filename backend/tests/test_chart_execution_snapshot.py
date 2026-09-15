from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.api import chart
from app.engines.smc_engine import SMCSignal
from app.engines.strategy_engine import StrategyResult
from app.services.execution_analysis import ExecutionAnalysis


def _frame(index: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(len(index))],
            "high": [101.0 + i for i in range(len(index))],
            "low": [99.0 + i for i in range(len(index))],
            "close": [100.5 + i for i in range(len(index))],
            "volume": [1000.0 + i for i in range(len(index))],
        },
        index=pd.DatetimeIndex(pd.to_datetime(index, utc=True)),
    )


@pytest.mark.anyio
async def test_execution_chart_uses_one_snapshot_for_candles_overlay_and_gate(monkeypatch):
    closed = _frame([
        "2026-01-01T00:00:00Z",
        "2026-01-01T01:00:00Z",
    ])
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        current_price=101.5,
        confluence=72,
        volume_quality="exchange_aggressor",
        delta_source="exchange_aggressor",
        flow_source="binance_taker_volume",
    )
    strategy = StrategyResult(
        approved=False,
        direction="wait",
        setup_direction="long",
        rejection_reasons=["test gate"],
    )
    now = datetime.now(timezone.utc)
    execution = ExecutionAnalysis(
        symbol="BTC/USDT",
        timeframe="15m",
        frame=closed,
        signal=signal,
        strategy=strategy,
        config_snapshot={},
        snapshot_id="canonical-execution-snapshot",
        generated_at=now,
        valid_until=now + timedelta(hours=1),
    )

    async def fake_execution_get(**_kwargs):
        return execution.clone()

    async def fake_chart_tail(**_kwargs):
        return _frame([
            "2026-01-01T01:00:00Z",
            "2026-01-01T02:00:00Z",
        ])

    monkeypatch.setattr(chart.execution_analyses, "get", fake_execution_get)
    monkeypatch.setattr(chart._market, "get_ohlcv", fake_chart_tail)

    payload = await chart.get_smc_overlay(
        symbol="BTC/USDT",
            timeframe="15m",
        market_type="crypto",
        exchange="binance",
        htf_bias="neutral",
        htf_timeframe=None,
        entry_mode="limit",
        _key="test",
    )

    assert payload["analysis_snapshot"]["snapshot_id"] == execution.snapshot_id
    assert payload["decision_snapshot_id"] == execution.snapshot_id
    assert [item["t"] for item in payload["candles"]] == [
        timestamp.isoformat() for timestamp in closed.index
    ]
    assert payload["confluence"] == signal.confluence
    assert payload["strategy"] == strategy.to_dict()
    assert payload["volume_quality"] == "exchange_aggressor"
    assert payload["delta_source"] == "exchange_aggressor"
    assert payload["flow_source"] == "binance_taker_volume"
    assert payload["forming_candle"]["t"] == pd.Timestamp(
        "2026-01-01T02:00:00Z"
    ).isoformat()


@pytest.mark.anyio
async def test_chart_exposes_equal_level_timestamps_for_exact_overlay_anchors(monkeypatch):
    closed = _frame([
        "2026-01-01T00:00:00Z",
        "2026-01-01T01:00:00Z",
    ])
    signal = SMCSignal(symbol="BTC/USDT", timeframe="15m", current_price=101.5)
    signal.equal_high_levels = [{
        "price": 101.0,
        "first_origin_index": 0,
        "first_origin_timestamp": closed.index[0].isoformat(),
        "origin_index": 1,
        "origin_timestamp": closed.index[1].isoformat(),
        "confirmed_index": 1,
        "confirmed_timestamp": closed.index[1].isoformat(),
    }]
    strategy = StrategyResult(approved=False, direction="wait", setup_direction="wait")
    now = datetime.now(timezone.utc)
    execution = ExecutionAnalysis(
        symbol="BTC/USDT",
        timeframe="15m",
        frame=closed,
        signal=signal,
        strategy=strategy,
        config_snapshot={},
        snapshot_id="equal-level-snapshot",
        generated_at=now,
        valid_until=now + timedelta(hours=1),
    )

    async def fake_execution_get(**_kwargs):
        return execution.clone()

    async def fake_chart_tail(**_kwargs):
        return closed

    monkeypatch.setattr(chart.execution_analyses, "get", fake_execution_get)
    monkeypatch.setattr(chart._market, "get_ohlcv", fake_chart_tail)

    payload = await chart.get_smc_overlay(
        symbol="BTC/USDT",
            timeframe="15m",
        market_type="crypto",
        exchange="binance",
        htf_bias="neutral",
        htf_timeframe=None,
        entry_mode="limit",
        _key="test",
    )

    level = payload["equal_high_levels"][0]
    assert level["first_origin_timestamp"] == closed.index[0].isoformat()
    assert level["origin_timestamp"] == closed.index[1].isoformat()
