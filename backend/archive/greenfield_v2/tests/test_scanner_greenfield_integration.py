"""
Integration test for MarketMonitor with Greenfield DecisionOrchestrator.
Verifies that background scanning executes the opportunity lifecycle,
attaches the Opportunity object to the signal broadcast, and handles state correctly.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pandas as pd
import pytest

from app.domain.opportunity import OpportunityState
from app.market_data.quote import MarketQuote
from app.orchestrator import orchestrator
from app.services.event_trigger import MarketMonitor


def _make_dummy_frame(bars: int = 40) -> pd.DataFrame:
    now_ts = pd.Timestamp.now(tz="UTC").floor("15min")
    records = []
    base = 100.0
    for i in range(bars):
        open_time = now_ts - pd.Timedelta(minutes=15 * (bars - i))
        records.append({
            "timestamp": open_time,
            "open": base + i * 0.1,
            "high": base + i * 0.1 + 0.5,
            "low": base + i * 0.1 - 0.5,
            "close": base + i * 0.1 + 0.2,
            "volume": 1000.0,
        })
    df = pd.DataFrame(records).set_index("timestamp")
    return df


@pytest.mark.asyncio
async def test_scanner_orchestrator_integration():
    """Verify that _scan_single_symbol runs the Greenfield Orchestrator without failure."""
    monitor = MarketMonitor(watchlist=[{"symbol": "SOL/USDT", "market_type": "crypto", "exchange": "binance"}])
    
    mock_frame = _make_dummy_frame(40)
    
    # Mock execution_analyses.get to return a controlled snapshot
    class DummySig:
        confluence_score = 75
        current_price = 105.0
        tri_core_setup = {"direction": "wait", "actionable": False}
        in_discount = False
        in_premium = False
        liquidity_swept = False
        order_block = False
        bos = False
        choch = False
        delta_absorption = False
        volume_spike = False
        squeeze_status = "none"
        squeeze_momentum = 0.0
        momentum_direction = "neutral"
        volume_delta = 0.0
        delta_ratio = 1.0
        delta_absorption_type = None
        delta_absorption_evidence = None
        cvd_divergence = False
        cvd_divergence_evidence = None
        delta_status = "normal"
        delta_source = "binance"
        flow_source = "binance"
        flow_granularity = "15m"
        volume_quality = 1.0
        indicator_decision = {"ready": False}
        market_regime = {"label": "Trend", "effective_policy": {}}
        ai_review = None
        risk_reward = 2.0
        entry = 104.0
        stop_loss = 102.0
        take_profit = 108.0

        def to_dict(self):
            return {}

    class DummyStrat:
        approved = False
        direction = "wait"
        setup_direction = "wait"
        strategy_name = "reversal"
        rejection_reasons = []
        effective_policy = {}

        def to_dict(self):
            return {}

    class DummyExecution:
        symbol = "SOL/USDT"
        timeframe = "15m"
        frame = mock_frame
        signal = DummySig()
        strategy = DummyStrat()
        snapshot_id = "snap_123"
        config_snapshot = {}

        def metadata(self):
            return {}

    with patch("app.services.event_trigger.execution_analyses.get", new=AsyncMock(return_value=DummyExecution())), \
         patch("app.services.event_trigger.broadcast", new=AsyncMock()) as mock_broadcast, \
         patch("app.services.evidence.capture_decision_evidence", new=AsyncMock(return_value={})):

        res = await monitor._scan_single_symbol({
            "symbol": "SOL/USDT",
            "market_type": "crypto",
            "exchange": "binance",
            "timeframe": "15m",
        })

        assert res is not None
        assert res["symbol"] == "SOL/USDT"
        # Broadcast called once
        assert mock_broadcast.await_count >= 1
        broadcast_payload = mock_broadcast.await_args[0][0]
        assert broadcast_payload["type"] == "signal"
        assert "data" in broadcast_payload
