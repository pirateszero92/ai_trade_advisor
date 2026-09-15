"""
Edge Cases Unit Tests

Tests the robustness of the RiskEngine and TriCoreSetupEngine against
abnormal inputs, missing data, and extreme market conditions.
"""
import math
import pytest
import pandas as pd

from app.engines.risk_engine import RiskEngine
from app.engines.tri_core_engine import TriCoreSetupEngine, TriCorePolicy
from app.engines.smc_engine import SMCSignal


# ---------------------------------------------------------------------------
# Risk Engine Edge Cases
# ---------------------------------------------------------------------------

class TestRiskEngineEdgeCases:
    """Tests for RiskEngine robustness against invalid or extreme inputs."""

    def test_zero_balance(self):
        """Account balance is zero (blown account) -> must reject."""
        engine = RiskEngine()
        signal = SMCSignal(
            symbol="BTC/USDT", timeframe="15m", bias="bullish", direction="long",
            entry=50000.0, stop_loss=49000.0, take_profit=52000.0, risk_reward=2.0,
        )
        assessment = engine.evaluate(signal=signal, account_balance=0.0)
        assert assessment.approved is False
        assert "positive" in assessment.rejection_reason.lower()

    def test_negative_balance(self):
        """Account balance is negative -> must reject."""
        engine = RiskEngine()
        signal = SMCSignal(
            symbol="BTC/USDT", timeframe="15m", bias="bullish", direction="long",
            entry=50000.0, stop_loss=49000.0, take_profit=52000.0, risk_reward=2.0,
        )
        assessment = engine.evaluate(signal=signal, account_balance=-1000.0)
        assert assessment.approved is False
        assert "positive" in assessment.rejection_reason.lower()

    def test_nan_inputs(self):
        """NaN in risk inputs -> must reject."""
        engine = RiskEngine()
        signal = SMCSignal(
            symbol="BTC/USDT", timeframe="15m", bias="bullish", direction="long",
            entry=50000.0, stop_loss=49000.0, take_profit=52000.0, risk_reward=2.0,
        )
        assessment = engine.evaluate(
            signal=signal,
            account_balance=10000.0,
            daily_pnl_pct=math.nan,
        )
        assert assessment.approved is False
        assert "finite" in assessment.rejection_reason.lower()

    def test_extreme_wide_sl(self):
        """SL is wider than the maximum allowed (5%) -> must reject."""
        engine = RiskEngine()
        # SL is 10% away (50000 -> 45000)
        signal = SMCSignal(
            symbol="BTC/USDT", timeframe="15m", bias="bullish", direction="long",
            entry=50000.0, stop_loss=45000.0, take_profit=60000.0, risk_reward=2.0,
        )
        assessment = engine.evaluate(signal=signal, account_balance=10000.0)
        assert assessment.approved is False
        assert "too wide" in assessment.rejection_reason.lower()

    def test_missing_sl_tp(self):
        """Missing SL or TP levels -> must reject."""
        engine = RiskEngine()
        signal = SMCSignal(
            symbol="BTC/USDT", timeframe="15m", bias="bullish", direction="long",
            entry=50000.0, stop_loss=None, take_profit=52000.0, risk_reward=2.0,
        )
        assessment = engine.evaluate(signal=signal, account_balance=10000.0)
        assert assessment.approved is False
        assert "missing" in assessment.rejection_reason.lower()

    def test_cluster_exposure_limit(self):
        """Max positions in the same asset cluster reached -> must reject."""
        engine = RiskEngine()
        signal = SMCSignal(
            symbol="ETH/USDT", timeframe="15m", bias="bullish", direction="long",
            entry=3000.0, stop_loss=2900.0, take_profit=3200.0, risk_reward=2.0,
        )
        # Simulate 2 existing open positions in the same crypto_l1 cluster
        active_positions = [
            {"symbol": "BTC/USDT", "status": "open", "direction": "long"},
            {"symbol": "SOL/USDT", "status": "open", "direction": "long"},
        ]
        assessment = engine.evaluate(
            signal=signal,
            account_balance=10000.0,
            active_positions=active_positions,
        )
        assert assessment.approved is False
        assert "cluster exposure limit" in assessment.rejection_reason.lower()


# ---------------------------------------------------------------------------
# Tri-Core Engine Edge Cases
# ---------------------------------------------------------------------------

class TestTriCoreEngineEdgeCases:
    """Tests for TriCoreSetupEngine robustness against missing/malformed data."""

    def test_empty_dataframe(self):
        """Empty closed-candle data -> must return WAIT with rejection reason."""
        engine = TriCoreSetupEngine()
        empty_frame = pd.DataFrame()
        
        # Mock signal object
        class MockSignal:
            volume_quality = "exchange_aggressor"
            liquidity_swept = False
            displacement = {}
            
        setup = engine.evaluate(signal=MockSignal(), frame=empty_frame)
        assert setup.actionable is False
        assert setup.direction == "wait"
        assert any("unavailable" in r.lower() for r in setup.rejection_reasons)

    def test_missing_exchange_aggressor_cvd(self):
        """Volume quality is not exchange_aggressor -> must reject."""
        engine = TriCoreSetupEngine()
        frame = pd.DataFrame({
            "open": [100, 101], "high": [102, 103],
            "low": [99, 100], "close": [101, 102]
        })
        
        class MockSignal:
            volume_quality = "synthetic"  # Not exchange_aggressor
            liquidity_swept = False
            displacement = {}
            
        setup = engine.evaluate(signal=MockSignal(), frame=frame)
        assert setup.actionable is False
        assert setup.direction == "wait"
        assert any("trusted exchange-derived" in r.lower() for r in setup.rejection_reasons)

    def test_no_sweep_no_displacement(self):
        """No liquidity sweep and no displacement -> must return WAIT state."""
        engine = TriCoreSetupEngine()
        frame = pd.DataFrame({
            "open": [100, 101], "high": [102, 103],
            "low": [99, 100], "close": [101, 102]
        })
        
        class MockSignal:
            volume_quality = "exchange_aggressor"
            liquidity_swept = False
            displacement = {}
            order_blocks = []
            fvgs = []
            symbol = "BTC/USDT"
            timeframe = "15m"
            
        setup = engine.evaluate(signal=MockSignal(), frame=frame)
        assert setup.actionable is False
        assert setup.direction == "wait"
        assert setup.trigger_state == "wait"
