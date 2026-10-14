"""Tests for Phase 5 Bar-by-bar Counterfactual Market Replay Engine."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pandas as pd
import pytest

from app.engines.backtest_engine import ExecutionAssumptions
from app.services.counterfactual_replay import CounterfactualReplayEngine
from app.services.paper_evaluation import (
    build_paper_evaluation,
    build_tri_core_vs_ai_comparison,
)


def _make_bars(rows: list[dict[str, float]], start_iso="2026-10-12T16:45:00Z", freq="15min") -> pd.DataFrame:
    df = pd.DataFrame(rows)
    dates = pd.date_range(start=start_iso, periods=len(rows), freq=freq, tz="UTC")
    df.index = dates
    return df


def test_replay_candidate_avoided_loss():
    """Verify that a candidate that drops to SL is classified as avoided_loss."""
    engine = CounterfactualReplayEngine(
        assumptions=ExecutionAssumptions(
            fee_bps=10.0,
            spread_bps=5.0,
            slippage_bps=3.0,
            latency_bars=1,
            entry_timeout_bars=5,
            max_holding_bars=24,
        )
    )

    candidate = {
        "candidate_id": "snap1:event1",
        "symbol": "SOL/USDT",
        "direction": "long",
        "entry": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "timeframe": "15m",
        "verdict": "CONFLICT",
        "occurred_at": "2026-10-12T16:45:00Z",
    }

    # Bar 0: latency bar
    # Bar 1: touches entry (low 99.0 <= 100.0) -> entered
    # Bar 2: crashes to 94.0 (<= 95.0 SL) -> hit SL
    future_bars = _make_bars([
        {"open": 101.0, "high": 102.0, "low": 100.5, "close": 101.5, "volume": 1000},
        {"open": 101.5, "high": 101.5, "low": 99.0, "close": 99.5, "volume": 1000},
        {"open": 99.5, "high": 99.5, "low": 94.0, "close": 94.5, "volume": 1000},
    ])

    result = engine.replay_candidate(candidate, future_bars)

    assert result["status"] == "closed"
    assert result["outcome_type"] == "avoided_loss"
    assert result["exit_reason"] == "stop_loss"
    assert result["realized_pnl_net"] < 0
    assert result["r_multiple"] < 0
    assert result["holding_bars"] >= 1


def test_replay_candidate_missed_profit():
    """Verify that a candidate that surges to TP is classified as missed_profit."""
    engine = CounterfactualReplayEngine(
        assumptions=ExecutionAssumptions(
            fee_bps=10.0,
            spread_bps=5.0,
            slippage_bps=3.0,
            latency_bars=1,
            entry_timeout_bars=5,
            max_holding_bars=24,
        )
    )

    candidate = {
        "candidate_id": "snap2:event2",
        "symbol": "ETH/USDT",
        "direction": "short",
        "entry": 2500.0,
        "stop_loss": 2550.0,
        "take_profit": 2400.0,
        "timeframe": "15m",
        "verdict": "CONFLICT",
        "occurred_at": "2026-10-12T16:45:00Z",
    }

    # Bar 0: latency
    # Bar 1: touches entry for short (high 2505.0 >= 2500.0)
    # Bar 2: drops to 2390.0 (<= 2400.0 TP)
    future_bars = _make_bars([
        {"open": 2490.0, "high": 2495.0, "low": 2485.0, "close": 2490.0, "volume": 500},
        {"open": 2490.0, "high": 2505.0, "low": 2480.0, "close": 2485.0, "volume": 500},
        {"open": 2485.0, "high": 2485.0, "low": 2390.0, "close": 2395.0, "volume": 500},
    ])

    result = engine.replay_candidate(candidate, future_bars)

    assert result["status"] == "closed"
    assert result["outcome_type"] == "missed_profit"
    assert result["exit_reason"] == "take_profit"
    assert result["realized_pnl_net"] > 0
    assert result["r_multiple"] > 0


def test_replay_candidate_unfilled_timeout():
    """Verify that an order that never touches entry before timeout is neutral_unfilled."""
    engine = CounterfactualReplayEngine(
        assumptions=ExecutionAssumptions(
            latency_bars=1,
            entry_timeout_bars=2,
            max_holding_bars=10,
        )
    )

    candidate = {
        "candidate_id": "snap3:event3",
        "symbol": "BTC/USDT",
        "direction": "long",
        "entry": 60_000.0,
        "stop_loss": 59_000.0,
        "take_profit": 62_000.0,
        "timeframe": "15m",
        "verdict": "CONFLICT",
        "occurred_at": "2026-10-12T16:45:00Z",
    }

    # Price stays above 61_000, never touching 60_000 limit
    future_bars = _make_bars([
        {"open": 61_500.0, "high": 62_000.0, "low": 61_000.0, "close": 61_200.0, "volume": 100},
        {"open": 61_200.0, "high": 61_800.0, "low": 61_100.0, "close": 61_400.0, "volume": 100},
        {"open": 61_400.0, "high": 61_900.0, "low": 61_200.0, "close": 61_500.0, "volume": 100},
    ])

    result = engine.replay_candidate(candidate, future_bars)

    assert result["status"] == "unfilled"
    assert result["outcome_type"] == "neutral_unfilled"
    assert result["realized_pnl_net"] == 0.0


def test_summarize_replay_and_net_ai_filter_alpha():
    replay_results = [
        # AI avoided 2 losses (saved ~$100 each = $200)
        {
            "outcome_type": "avoided_loss",
            "verdict": "CONFLICT",
            "realized_pnl_net": -102.5,
            "r_multiple": -1.025,
        },
        {
            "outcome_type": "avoided_loss",
            "verdict": "CONFLICT",
            "realized_pnl_net": -98.0,
            "r_multiple": -0.98,
        },
        # AI missed 1 profit (cost $150)
        {
            "outcome_type": "missed_profit",
            "verdict": "CONFLICT",
            "realized_pnl_net": 150.0,
            "r_multiple": 1.5,
        },
        # 1 unfilled neutral
        {
            "outcome_type": "neutral_unfilled",
            "verdict": "UNAVAILABLE",
            "realized_pnl_net": 0.0,
            "r_multiple": 0.0,
        },
    ]

    summary = CounterfactualReplayEngine.summarize_replay(replay_results)

    assert summary["total_candidates_replayed"] == 4
    assert summary["executed_in_simulation"] == 3
    assert summary["unfilled_in_simulation"] == 1
    assert summary["avoided_losses"]["count"] == 2
    assert summary["avoided_losses"]["total_dollar_saved"] == 200.5
    assert summary["missed_profits"]["count"] == 1
    assert summary["missed_profits"]["total_dollar_missed"] == 150.0
    # Net alpha = 200.5 - 150.0 = +50.5
    assert summary["net_ai_filter_alpha"]["net_dollar_alpha"] == 50.5
    assert summary["net_ai_filter_alpha"]["is_alpha_positive"] is True
    # Filter precision: 2 / 3 = 0.6667 (66.7% of blocked trades were bad)
    assert summary["filter_precision"] == 0.6667


def test_pure_tri_core_baseline_synthesis():
    executed_trades = [
        # Executed trade with 0.5x caution sizing, net pnl $50 (unscaled = $100)
        {
            "decision_trace_id": "snap1:event1",
            "status": "closed",
            "realized_pnl_net": 50.0,
            "estimated_risk": 50.0,
            "fees_total": 2.0,
            "spread_cost_total": 1.0,
            "slippage_cost_total": 0.5,
            "opened_at": "2026-10-12T10:00:00Z",
            "closed_at": "2026-10-12T11:00:00Z",
            "max_r_multiple": 2.0,
            "ai_risk_multiplier": 0.5,
        }
    ]

    replay_results = [
        # Filtered trade 1 would have lost -$100 in simulation
        {
            "candidate_id": "snap2:event2",
            "status": "closed",
            "realized_pnl_net": -100.0,
            "r_multiple": -1.0,
            "fees": 2.0,
            "spread_cost": 1.0,
            "slippage_cost": 0.5,
            "occurred_at": "2026-10-12T12:00:00Z",
            "replayed_at": "2026-10-12T13:00:00Z",
        }
    ]

    pure_baseline = CounterfactualReplayEngine.build_pure_tri_core_baseline(
        executed_trades, replay_results, initial_equity=10_000.0
    )

    # Pure portfolio has 2 trades: unscaled trade ($100) + replayed trade (-$100) = $0.0 net pnl
    assert pure_baseline["closed_trades"] == 2
    assert pure_baseline["net_pnl"] == 0.0


def test_paper_evaluation_integration_with_counterfactual_replay():
    records = [
        {
            "candidate_id": "snap1:event1",
            "snapshot_id": "snap1",
            "event_id": "event1",
            "stages": [
                {"stage": "SNAPSHOT", "facts": {"entry": 100.0, "stop_loss": 95.0, "take_profit": 110.0}},
                {"stage": "AI_GATE_FILTER", "facts": {
                    "direction": "long", "entry": 100.0, "stop_loss": 95.0, "take_profit": 110.0,
                    "timeframe": "15m", "verdict": "CONFLICT", "action": "BLOCKED", "reason": "SMC conflict"
                }},
            ],
        }
    ]
    trades = [
        {
            "decision_trace_id": "snap0:event0",
            "status": "closed",
            "realized_pnl_net": 150.0,
            "estimated_risk": 100.0,
            "fees_total": 2.0,
            "spread_cost_total": 1.0,
            "slippage_cost_total": 0.5,
            "opened_at": "2026-10-12T08:00:00Z",
            "closed_at": "2026-10-12T09:00:00Z",
            "max_r_multiple": 1.5,
            "ai_risk_multiplier": 1.0,
        }
    ]
    replay_results = [
        {
            "candidate_id": "snap1:event1",
            "status": "closed",
            "outcome_type": "avoided_loss",
            "verdict": "CONFLICT",
            "realized_pnl_net": -100.0,
            "r_multiple": -1.0,
            "fees": 2.0,
            "spread_cost": 1.0,
            "slippage_cost": 0.5,
            "occurred_at": "2026-10-12T10:00:00Z",
            "replayed_at": "2026-10-12T11:00:00Z",
        }
    ]

    evaluation = build_paper_evaluation(records, trades, replay_results=replay_results)
    tri_core_vs_ai = evaluation["tri_core_vs_ai"]

    assert tri_core_vs_ai["metadata"]["counterfactual_replay"] is True
    assert tri_core_vs_ai["metadata"]["confidence"] == "counterfactual_replay_validated"
    assert tri_core_vs_ai["metadata"]["baseline_method"] == "counterfactual_market_replay"
    assert "pure_tri_core_baseline" in tri_core_vs_ai
    assert "counterfactual_replay" in tri_core_vs_ai
    assert tri_core_vs_ai["counterfactual_filtered_candidates"]["replay_status"] == "replay_validated"
    assert tri_core_vs_ai["delta"]["net_ai_filter_alpha_usd"] == 100.0
    assert tri_core_vs_ai["evaluation_readiness"]["comparison_reliability"] == "counterfactual_replay_validated"
    # Safety: live auto pilot recommended is still False
    assert tri_core_vs_ai["evaluation_readiness"]["live_auto_pilot_recommended"] is False


def test_cache_invalidation_on_assumption_hash_mismatch(tmp_path):
    """Verify that cache entries are automatically invalidated when execution assumptions change."""
    cache_file = tmp_path / "replay_cache.json"

    engine_v1 = CounterfactualReplayEngine(
        assumptions=ExecutionAssumptions(fee_bps=10.0, spread_bps=5.0),
        cache_path=cache_file,
    )
    dummy_result = {
        "candidate_id": "c1",
        "status": "closed",
        "outcome_type": "avoided_loss",
        "realized_pnl_net": -50.0,
    }
    engine_v1.save_cache({"c1": dummy_result})

    # Engine v1 loads it successfully
    loaded = engine_v1.load_cache()
    assert "c1" in loaded
    assert loaded["c1"]["realized_pnl_net"] == -50.0

    # Engine v2 with changed fee (20.0 bps) has a different assumption hash
    engine_v2 = CounterfactualReplayEngine(
        assumptions=ExecutionAssumptions(fee_bps=20.0, spread_bps=5.0),
        cache_path=cache_file,
    )
    loaded_v2 = engine_v2.load_cache()
    # Cache must be invalidated (empty) because assumptions do not match
    assert "c1" not in loaded_v2
    assert len(loaded_v2) == 0


@pytest.mark.asyncio
async def test_strict_next_bar_candle_boundary_prevents_look_ahead():
    """Verify that replay starts strictly on the next bar open after occurred_at, eliminating look-ahead."""
    engine = CounterfactualReplayEngine(
        assumptions=ExecutionAssumptions(latency_bars=0, entry_timeout_bars=5, max_holding_bars=10)
    )

    candidate = {
        "candidate_id": "snap_mid:event1",
        "symbol": "BTC/USDT",
        "direction": "long",
        "entry": 60_000.0,
        "stop_loss": 59_000.0,
        "take_profit": 62_000.0,
        "timeframe": "15m",
        "verdict": "CONFLICT",
        # Candidate generated at 16:47:00 (during the 16:45:00 candle)
        "occurred_at": "2026-10-12T16:47:00Z",
    }

    # Bar 1 (16:45:00): Had high 62_500 BEFORE candidate occurred.
    # Bar 2 (17:00:00): Opened at 60_100, low 58_500 (hits SL).
    bars = _make_bars(
        [
            {"open": 59_800.0, "high": 62_500.0, "low": 59_500.0, "close": 60_000.0, "volume": 100},
            {"open": 60_100.0, "high": 60_100.0, "low": 58_500.0, "close": 58_800.0, "volume": 100},
        ],
        start_iso="2026-10-12T16:45:00Z",
        freq="15min",
    )

    # Replay batch with strict next-bar boundary
    results = await engine.replay_candidates_batch(
        [candidate],
        candle_provider_sync=lambda sym, tf: bars,
    )
    assert len(results) == 1
    res = results[0]

    # If look-ahead bias existed, it would have used Bar 1's high (62_500) and claimed TP!
    # Under strict next-bar policy, Bar 1 is excluded; Bar 2 touches entry (low 58_500 <= 60_000) and crashes to SL (58_500 <= 59_000).
    assert res["status"] == "closed"
    assert res["exit_reason"] == "stop_loss"
    assert res["outcome_type"] == "avoided_loss"


def test_tiered_confidence_and_coverage_threshold():
    """Verify that coverage below 95% produces replay_partial and keeps counterfactual_replay False."""
    # 10 filtered candidates
    records = [
        {
            "candidate_id": f"snap{i}:event{i}",
            "snapshot_id": f"snap{i}",
            "event_id": f"event{i}",
            "stages": [
                {"stage": "SNAPSHOT", "facts": {"entry": 100.0, "stop_loss": 95.0, "take_profit": 110.0}},
                {"stage": "AI_GATE_FILTER", "facts": {
                    "direction": "long", "entry": 100.0, "stop_loss": 95.0, "take_profit": 110.0,
                    "timeframe": "15m", "verdict": "CONFLICT", "action": "BLOCKED"
                }},
            ],
        }
        for i in range(10)
    ]
    trades = [
        {"decision_trace_id": "snap0:event0", "status": "closed", "realized_pnl_net": 100.0, "estimated_risk": 100.0, "ai_risk_multiplier": 1.0}
    ]

    # Scenario A: Only 5 replayed (50% coverage < 95% threshold)
    partial_replays = [
        {"candidate_id": f"snap{i}:event{i}", "status": "closed", "outcome_type": "avoided_loss", "realized_pnl_net": -100.0, "r_multiple": -1.0}
        for i in range(5)
    ]

    eval_partial = build_paper_evaluation(records, trades, replay_results=partial_replays)
    tri_partial = eval_partial["tri_core_vs_ai"]

    assert tri_partial["metadata"]["counterfactual_replay"] is False
    assert tri_partial["metadata"]["confidence"] == "counterfactual_replay_partial"
    assert tri_partial["metadata"]["coverage_pct"] == 0.5
    assert tri_partial["metadata"]["replay_status"] == "replay_partial"
    assert tri_partial["evaluation_readiness"]["promotion_evidence_status"] == "replay_partial_accumulating_trades"

    # Scenario B: 10 replayed (100% coverage >= 95% threshold)
    full_replays = [
        {"candidate_id": f"snap{i}:event{i}", "status": "closed", "outcome_type": "avoided_loss", "realized_pnl_net": -100.0, "r_multiple": -1.0}
        for i in range(10)
    ]

    eval_full = build_paper_evaluation(records, trades, replay_results=full_replays)
    tri_full = eval_full["tri_core_vs_ai"]

    assert tri_full["metadata"]["counterfactual_replay"] is True
    assert tri_full["metadata"]["confidence"] == "counterfactual_replay_validated"
    assert tri_full["metadata"]["coverage_pct"] == 1.0
    assert tri_full["metadata"]["replay_status"] == "replay_validated"


def test_multi_dimensional_breakdowns_in_summary():
    """Verify breakdowns by symbol, regime, direction, and timeframe."""
    results = [
        {
            "candidate_id": "c1",
            "symbol": "BTC/USDT",
            "market_regime": "trending_bull",
            "direction": "long",
            "timeframe": "15m",
            "outcome_type": "avoided_loss",
            "realized_pnl_net": -100.0,
            "status": "closed",
        },
        {
            "candidate_id": "c2",
            "symbol": "ETH/USDT",
            "market_regime": "ranging",
            "direction": "short",
            "timeframe": "1h",
            "outcome_type": "missed_profit",
            "realized_pnl_net": 150.0,
            "status": "closed",
        },
    ]

    summary = CounterfactualReplayEngine.summarize_replay(results)

    assert "BTC/USDT" in summary["breakdown_by_symbol"]
    assert summary["breakdown_by_symbol"]["BTC/USDT"]["avoided_losses"] == 1
    assert "ETH/USDT" in summary["breakdown_by_symbol"]
    assert summary["breakdown_by_symbol"]["ETH/USDT"]["missed_profits"] == 1

    assert "trending_bull" in summary["breakdown_by_regime"]
    assert "ranging" in summary["breakdown_by_regime"]

    assert "long" in summary["breakdown_by_direction"]
    assert "short" in summary["breakdown_by_direction"]

    assert "15m" in summary["breakdown_by_timeframe"]
    assert "1h" in summary["breakdown_by_timeframe"]


@pytest.mark.asyncio
async def test_paper_counterfactual_replay_api_endpoints():
    from app.main import app
    from app.core.config import get_settings
    from httpx import ASGITransport, AsyncClient

    cfg = get_settings()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET summary
        resp_get = await client.get(
            "/api/v1/paper/counterfactual/replay",
            headers={"X-API-Key": cfg.app_secret_key},
        )
        assert resp_get.status_code == 200
        data_get = resp_get.json()
        assert data_get["status"] == "ready"
        assert "summary" in data_get


def test_sample_size_assessment_requires_decisive_samples():
    """Verify that sample_size_assessment requires >= 30 decisive samples (avoided_losses + missed_profits)."""
    # 35 total replayed, but 30 are neutral_unfilled and only 5 are decisive
    replays_mixed = [
        {"candidate_id": f"c{i}", "outcome_type": "neutral_unfilled", "status": "unfilled"}
        for i in range(30)
    ] + [
        {"candidate_id": f"d{i}", "outcome_type": "avoided_loss", "status": "closed", "realized_pnl_net": -100.0}
        for i in range(5)
    ]

    summary = CounterfactualReplayEngine.summarize_replay(replays_mixed)
    assert summary["total_candidates_replayed"] == 35
    assert summary["decisive_samples_count"] == 5
    # Even though total_candidates_replayed >= 30, decisive is only 5 < 30
    assert summary["sample_size_assessment"] == "insufficient_decisive_samples"

    # Now add 25 more decisive samples (total 30 decisive)
    replays_30_decisive = replays_mixed + [
        {"candidate_id": f"e{i}", "outcome_type": "missed_profit", "status": "closed", "realized_pnl_net": 100.0}
        for i in range(25)
    ]
    summary_valid = CounterfactualReplayEngine.summarize_replay(replays_30_decisive)
    assert summary_valid["decisive_samples_count"] == 30
    assert summary_valid["sample_size_assessment"] == "statistically_meaningful"


@pytest.mark.asyncio
async def test_replay_timestamp_error_falls_back_to_insufficient_data():
    """Verify that if candle indexing fails, replay marks insufficient_data instead of picking tail bars."""
    engine = CounterfactualReplayEngine()

    candidate = {
        "candidate_id": "snap_err:event1",
        "symbol": "BTC/USDT",
        "direction": "long",
        "entry": 60_000.0,
        "stop_loss": 59_000.0,
        "take_profit": 62_000.0,
        "timeframe": "15m",
        "verdict": "CONFLICT",
        "occurred_at": "2026-10-12T16:47:00Z",
    }

    # Bars with non-datetime index that will raise an error when tz_localize/convert is called
    malformed_bars = pd.DataFrame(
        [{"open": 60_000.0, "high": 61_000.0, "low": 59_000.0, "close": 60_500.0}],
        index=["not_a_timestamp"],
    )

    results = await engine.replay_candidates_batch(
        [candidate],
        candle_provider_sync=lambda sym, tf: malformed_bars,
    )
    assert len(results) == 1
    res = results[0]
    # Invariant: must be marked as insufficient_data, NOT simulated against arbitrary tail bars
    assert res["status"] == "insufficient_data"
    assert res["outcome_type"] == "insufficient_data"


