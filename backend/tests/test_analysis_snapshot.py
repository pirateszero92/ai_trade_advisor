from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.analysis_snapshot import AnalysisSnapshotService


def _frame(periods: int, timeframe: str) -> pd.DataFrame:
    end = pd.Timestamp.now(tz="UTC").floor(timeframe) - pd.Timedelta(timeframe)
    index = pd.date_range(end=end, periods=periods, freq=timeframe)
    values = list(range(periods))
    return pd.DataFrame(
        {
            "open": [100 + value * 0.01 for value in values],
            "high": [101 + value * 0.01 for value in values],
            "low": [99 + value * 0.01 for value in values],
            "close": [100.5 + value * 0.01 for value in values],
            "volume": [1000 + value for value in values],
        },
        index=index,
    )


@pytest.mark.anyio
async def test_chart_and_scanner_snapshot_reuses_canonical_closed_window(monkeypatch):
    service = AnalysisSnapshotService()
    calls = []

    async def fake_get_ohlcv(
        symbol, timeframe, market_type, exchange, limit, *, closed_only=False
    ):
        calls.append((timeframe, limit, closed_only))
        return _frame(limit, timeframe)

    monkeypatch.setattr(service._market, "get_ohlcv", fake_get_ohlcv)
    monkeypatch.setattr(
        service._smc,
        "analyze",
        lambda *_args, **_kwargs: SimpleNamespace(bias="bullish"),
    )

    scanner = await service.get(
        symbol="SOL/USDT",
        timeframe="1h",
        htf_timeframe="4h",
        market_type="crypto",
        exchange="binance",
    )
    chart = await service.get(
        symbol="SOL/USDT",
        timeframe="1h",
        htf_timeframe="4h",
        market_type="crypto",
        exchange="binance",
    )

    assert scanner is chart
    assert scanner.snapshot_id == chart.snapshot_id
    assert calls == [("1h", 600, True), ("4h", 120, True)]
    assert scanner.metadata()["candle_policy"] == "closed_only"
    assert scanner.metadata()["lookback"] == 600


def test_frame_digest_changes_when_flow_data_changes():
    from app.services.analysis_snapshot import _frame_digest

    frame_a = _frame(20, "1h")
    frame_a["volume_delta"] = [10.0] * 20
    frame_a["buy_volume"] = [60.0] * 20
    frame_a["sell_volume"] = [40.0] * 20
    frame_a["cvd"] = [float(i * 10) for i in range(20)]
    frame_a["flow_source"] = "binance_taker_volume"

    frame_b = frame_a.copy(deep=True)
    # Identical OHLCV, but exchange modified aggressor delta
    frame_b["volume_delta"] = [50.0] * 20

    digest_a = _frame_digest(frame_a)
    digest_b = _frame_digest(frame_b)

    assert digest_a != digest_b

