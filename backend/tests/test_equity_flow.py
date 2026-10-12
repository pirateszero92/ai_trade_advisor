from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from app.engines.equity_flow import classify_and_aggregate_equity_flow
from app.engines.indicators import AdvancedIndicatorsEngine
from app.engines.tri_core_engine import TriCoreSetupEngine
from app.services.market_session import us_equity_market_status


def _ticks():
    quotes = []
    trades = []
    base = pd.Timestamp("2026-09-22T13:30:00Z")
    for index in range(20):
        timestamp = base + pd.Timedelta(minutes=index)
        quotes.append({
            "t": timestamp.isoformat(),
            "bp": 99.9 + index * 0.01,
            "ap": 100.1 + index * 0.01,
        })
        trades.append({
            "t": (timestamp + pd.Timedelta(seconds=1)).isoformat(),
            "p": (100.1 + index * 0.01) if index % 2 == 0 else (99.9 + index * 0.01),
            "s": 10,
        })
    return trades, quotes


def test_quote_classified_equity_flow_is_labelled_and_aggregated():
    trades, quotes = _ticks()
    bars, quality = classify_and_aggregate_equity_flow(
        trades,
        quotes,
        timeframe="15m",
        source_complete=True,
        min_quote_coverage=0.8,
        min_trades_per_bar=2,
    )

    assert quality.complete is True
    assert quality.total_trades == 20
    assert quality.quote_classified_trades == 20
    assert len(bars) == 2
    assert set(bars["flow_source"]) == {"alpaca_quote_classified"}
    assert bars["buy_volume"].sum() == 100
    assert bars["sell_volume"].sum() == 100
    assert (bars["quote_coverage"] == 1.0).all()


def test_incomplete_alpaca_pagination_never_becomes_trusted_flow():
    trades, quotes = _ticks()
    bars, quality = classify_and_aggregate_equity_flow(
        trades,
        quotes,
        timeframe="15m",
        source_complete=False,
        min_trades_per_bar=1,
    )

    assert quality.complete is False
    assert quality.trusted_bars == 0
    assert set(bars["flow_source"]) == {"alpaca_flow_incomplete"}


def test_indicator_accepts_only_contiguous_trusted_equity_flow_tail():
    index = pd.date_range("2026-09-22T13:30:00Z", periods=20, freq="15min")
    frame = pd.DataFrame({
        "open": [100 + i * 0.1 for i in range(20)],
        "high": [100.4 + i * 0.1 for i in range(20)],
        "low": [99.7 + i * 0.1 for i in range(20)],
        "close": [100.2 + i * 0.1 for i in range(20)],
        "volume": [1000.0] * 20,
        "buy_volume": [float("nan")] * 12 + [550.0] * 8,
        "sell_volume": [float("nan")] * 12 + [450.0] * 8,
        "flow_source": ["alpaca_bars_only"] * 12
        + ["alpaca_quote_classified"] * 8,
    }, index=index)

    result = AdvancedIndicatorsEngine.compute_volume_delta(frame)

    assert result.source == "exchange_aggressor"
    assert result.flow_source == "alpaca_quote_classified"
    assert result.delta == 100.0


def test_us_equity_session_requires_open_clock_and_fresh_closed_bar():
    status = us_equity_market_status(
        datetime(2026, 9, 22, 14, 30, tzinfo=timezone.utc),
        "15m",
        now=datetime(2026, 9, 22, 14, 50, tzinfo=timezone.utc),
    )
    assert status["session_state"] == "regular_open"
    assert status["candle_fresh"] is True
    assert status["fresh_for_execution"] is True

    closed = us_equity_market_status(
        datetime(2026, 9, 21, 19, 45, tzinfo=timezone.utc),
        "15m",
        now=datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc),
    )
    assert closed["session_state"] == "pre_market"
    assert closed["fresh_for_execution"] is False


def test_stock_tri_core_fails_closed_outside_regular_session():
    frame = pd.DataFrame({
        "open": [100.0] * 20,
        "high": [101.0] * 20,
        "low": [99.0] * 20,
        "close": [100.0] * 20,
        "volume": [1000.0] * 20,
    })
    frame.attrs["market_data_status"] = {
        "market": "US_EQUITY",
        "regular_session_open": False,
        "candle_fresh": False,
    }
    setup = TriCoreSetupEngine.evaluate(
        SimpleNamespace(volume_quality="exchange_aggressor"), frame
    )
    assert setup.actionable is False
    assert setup.rejection_reasons == ["US regular trading session is closed"]
