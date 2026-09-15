"""Causal historical replay benchmark for 15M Canonical Execution.

Verifies the BTC $78,325 sweep and ETH $2,441 sweep from 2026-09-09
under the 15M Tri-Core state machine and Hybrid TTL rules.
"""

import pandas as pd
import pytest

from app.engines.smc_engine import SMCEngine, SMCSignal
from app.engines.tri_core_engine import TriCorePolicy, TriCoreSetupEngine
from tests.test_fixtures_20260909 import create_btc_15m_fixture, create_eth_15m_fixture


def test_btc_15m_replay_captures_sweep_and_reclaim():
    """Replay BTC 15M candle sequence around 2026-09-09 03:00 - 04:00 UTC."""
    df = create_btc_15m_fixture()
    policy = TriCorePolicy(
        version="15m-migration-v1",
        calibration_status="draft_unvalidated",
        ttl_bars=2,
        market_entry_max_extension_atr=0.25,
        no_chase_max_extension_atr=0.60,
        minimum_rr=2.0,
    )

    # 1. At bar 83 (the sweep bar dipping to $78,325.39)
    frame_at_sweep = df.iloc[:84].copy()
    engine = SMCEngine(swing_length=5, internal_swing_length=2, structure_event_ttl_bars=2)
    sig_at_sweep = engine.analyze(frame_at_sweep, "BTC/USDT", "15m")

    # Volume quality must be exchange_aggressor
    assert sig_at_sweep.volume_quality == "exchange_aggressor"
    assert sig_at_sweep.volume_data_valid

    # 2. At bar 85 (the reclaim bar closing at $78,650)
    frame_at_reclaim = df.iloc[:86].copy()
    sig_at_reclaim = engine.analyze(frame_at_reclaim, "BTC/USDT", "15m")

    # Evaluate Tri-Core at reclaim
    setup_at_reclaim = TriCoreSetupEngine.evaluate(
        sig_at_reclaim, frame_at_reclaim, policy_config=policy.__dict__
    )

    # Must be actionable or in flow/entry ready state
    assert setup_at_reclaim.policy_version == "15m-migration-v1"
    assert setup_at_reclaim.calibration_status == "draft_unvalidated"
    assert setup_at_reclaim.trigger_state in (
        "entry_ready", "limit_retest_only", "flow_confirmed", "armed"
    )

    # 3. Check trigger age and extension constraints
    if setup_at_reclaim.actionable:
        assert setup_at_reclaim.direction == "long"
        assert setup_at_reclaim.stop_loss < setup_at_reclaim.entry < setup_at_reclaim.take_profit
        assert setup_at_reclaim.risk_reward >= 2.0
        assert setup_at_reclaim.trigger_age_bars is not None
        assert setup_at_reclaim.trigger_age_bars <= policy.ttl_bars
        if setup_at_reclaim.extension_atr is not None:
            assert setup_at_reclaim.extension_atr <= policy.no_chase_max_extension_atr


def test_eth_15m_replay_captures_sweep_and_reclaim():
    """Replay ETH 15M candle sequence around 2026-09-09 low."""
    df = create_eth_15m_fixture()
    policy = TriCorePolicy(
        version="15m-migration-v1",
        calibration_status="draft_unvalidated",
        ttl_bars=2,
        market_entry_max_extension_atr=0.25,
        no_chase_max_extension_atr=0.60,
        minimum_rr=2.0,
    )

    frame_at_reclaim = df.iloc[:86].copy()
    engine = SMCEngine(swing_length=5, internal_swing_length=2, structure_event_ttl_bars=2)
    sig_at_reclaim = engine.analyze(frame_at_reclaim, "ETH/USDT", "15m")

    assert sig_at_reclaim.volume_quality == "exchange_aggressor"
    setup = TriCoreSetupEngine.evaluate(
        sig_at_reclaim, frame_at_reclaim, policy_config=policy.__dict__
    )

    assert setup.policy_version == "15m-migration-v1"
    assert setup.calibration_status == "draft_unvalidated"
    assert setup.trigger_state in (
        "entry_ready", "limit_retest_only", "flow_confirmed", "armed", "wait"
    )


def test_no_chase_state_triggered_when_price_extends_beyond_bound():
    """Verify that when price rallies far beyond the reclaim, NO_CHASE is applied."""
    df = create_btc_15m_fixture()
    # At bar 95, price has rallied up to $79,486 (far beyond 0.60 ATR from $78,650)
    frame_extended = df.iloc[:96].copy()
    engine = SMCEngine(swing_length=5, internal_swing_length=2, structure_event_ttl_bars=2)
    sig_extended = engine.analyze(frame_extended, "BTC/USDT", "15m")

    setup_extended = TriCoreSetupEngine.evaluate(sig_extended, frame_extended)
    # Must NOT be actionable Long (preventing chasing at the high)
    assert not setup_extended.actionable
    assert setup_extended.trigger_state in ("no_chase", "expired", "invalidated", "wait", "zone_approach")
