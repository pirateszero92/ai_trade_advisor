"""Tests for Phase 4 Paper Cohort Analytics and Tri-Core vs AI Comparison."""

from unittest.mock import AsyncMock, patch
import pytest
from app.services.paper_evaluation import (
    build_multiplier_cohort_metrics,
    build_paper_evaluation,
    build_regime_cohort_metrics,
    build_review_cohort_metrics,
    build_timing_cohort_metrics,
    build_tri_core_vs_ai_comparison,
    calculate_paper_metrics,
)


def _event(stage, facts):
    return {"stage": stage, "facts": facts}


def _record(candidate_id, events):
    snapshot_id, event_id = candidate_id.split(":", 1)
    return {
        "candidate_id": candidate_id,
        "snapshot_id": snapshot_id,
        "event_id": event_id,
        "events": events,
    }


def _make_trade(
    *,
    trace_id="snap1:event1",
    pnl=100.0,
    status="closed",
    risk=100.0,
    ai_verdict="COHERENT",
    ai_risk_multiplier=1.0,
    market_regime="trending_bull",
    ai_timing_advice="IMMEDIATE",
    realized_entry_timing="MARKET",
    entry_timing=None,
    opened="2026-10-14T10:00:00Z",
    closed="2026-10-14T10:30:00Z",
):
    actual_entry_timing = entry_timing or realized_entry_timing
    return {
        "decision_trace_id": trace_id,
        "status": status,
        "realized_pnl_net": pnl,
        "estimated_risk": risk,
        "fees_total": 2.0,
        "spread_cost_total": 1.0,
        "slippage_cost_total": 0.5,
        "opened_at": opened,
        "closed_at": closed,
        "max_r_multiple": 2.0,
        "ai_review_verdict": ai_verdict,
        "ai_risk_multiplier": ai_risk_multiplier,
        "market_regime": market_regime,
        "ai_timing": ai_timing_advice,
        "ai_timing_advice": ai_timing_advice,
        "realized_entry_timing": realized_entry_timing,
        "entry_timing": realized_entry_timing,
        "order_type": realized_entry_timing.lower(),
        "source_payload": {
            "ai_review_verdict": ai_verdict,
            "ai_risk_multiplier": ai_risk_multiplier,
            "market_regime": market_regime,
            "ai_timing": ai_timing_advice,
            "ai_timing_advice": ai_timing_advice,
            "realized_entry_timing": realized_entry_timing,
            "entry_timing": realized_entry_timing,
        },
    }


def test_multiplier_cohort_metrics():
    trades = [
        _make_trade(pnl=100.0, ai_risk_multiplier=1.0),
        _make_trade(pnl=50.0, ai_risk_multiplier=0.5),
        _make_trade(pnl=-30.0, ai_risk_multiplier=0.3),
    ]
    cohorts = build_multiplier_cohort_metrics(trades)

    assert "1.0x_full" in cohorts
    assert "0.5x_caution" in cohorts
    assert "0.3x_reduced" in cohorts
    assert cohorts["1.0x_full"]["closed_trades"] == 1
    assert cohorts["1.0x_full"]["net_pnl"] == 100.0
    assert cohorts["0.5x_caution"]["closed_trades"] == 1
    assert cohorts["0.5x_caution"]["net_pnl"] == 50.0
    assert cohorts["0.3x_reduced"]["closed_trades"] == 1
    assert cohorts["0.3x_reduced"]["net_pnl"] == -30.0


def test_regime_cohort_metrics():
    trades = [
        _make_trade(pnl=120.0, market_regime="trending_bull"),
        _make_trade(pnl=-50.0, market_regime="ranging"),
        _make_trade(pnl=80.0, market_regime="trending_bear"),
    ]
    cohorts = build_regime_cohort_metrics(trades)

    assert "trending_bull" in cohorts
    assert "ranging" in cohorts
    assert "trending_bear" in cohorts
    assert cohorts["trending_bull"]["win_rate"] == 1.0
    assert cohorts["ranging"]["win_rate"] == 0.0


def test_timing_cohort_metrics():
    from app.services.paper_evaluation import (
        build_ai_timing_cohort_metrics,
        build_realized_timing_cohort_metrics,
    )

    trades = [
        _make_trade(pnl=100.0, ai_timing_advice="IMMEDIATE", realized_entry_timing="MARKET"),
        _make_trade(pnl=70.0, ai_timing_advice="LIMIT_RETEST_ONLY", realized_entry_timing="LIMIT"),
    ]
    ai_cohorts = build_ai_timing_cohort_metrics(trades)
    realized_cohorts = build_realized_timing_cohort_metrics(trades)

    assert "IMMEDIATE" in ai_cohorts
    assert "LIMIT_RETEST_ONLY" in ai_cohorts
    assert ai_cohorts["IMMEDIATE"]["closed_trades"] == 1
    assert ai_cohorts["LIMIT_RETEST_ONLY"]["closed_trades"] == 1

    assert "MARKET" in realized_cohorts
    assert "LIMIT" in realized_cohorts
    assert realized_cohorts["MARKET"]["closed_trades"] == 1
    assert realized_cohorts["LIMIT"]["closed_trades"] == 1


def test_tri_core_vs_ai_comparison_unscales_risk_multiplier():
    """Verify that Tri-Core Baseline unscales scaled trades and compares drawdowns and PnL."""
    records = [
        _record("snap1:event1", [_event("AI_REVIEW", {"verdict": "CONFLICT"})]),
        _record("snap2:event2", [_event("AI_REVIEW", {"verdict": "COHERENT"})]),
    ]
    # Trade 1 was full size (1.0x) and won $100
    # Trade 2 had caution sizing (0.5x) and lost -$50 (unscaled would be -$100)
    trades = [
        _make_trade(pnl=100.0, ai_risk_multiplier=1.0, risk=100.0),
        _make_trade(pnl=-50.0, ai_risk_multiplier=0.5, risk=50.0),
    ]

    comp = build_tri_core_vs_ai_comparison(records, trades, initial_equity=10_000.0)

    # Actual (AI scaled) net pnl = 100 - 50 = 50
    assert comp["tri_core_with_ai"]["net_pnl"] == 50.0
    # Baseline (unscaled) net pnl = 100 - (50 / 0.5) = 100 - 100 = 0.0
    assert comp["tri_core_baseline"]["net_pnl"] == 0.0
    # Net pnl difference = 50 - 0 = +50
    assert comp["delta"]["net_pnl_diff"] == 50.0
    # Filtered conflict candidates from records = 1
    assert comp["delta"]["filtered_conflict_candidates"] == 1
    # Safeguard: live auto-pilot recommendation is strictly False
    assert comp["evaluation_readiness"]["live_auto_pilot_recommended"] is False


def test_build_paper_evaluation_complete_structure():
    records = [
        _record("snap1:event1", [
            _event("SNAPSHOT", {}),
            _event("AI_REVIEW", {"verdict": "COHERENT"}),
            _event("EXECUTION_REVALIDATION", {"passed": True}),
            _event("RISK", {"approved": True}),
            _event("OMS_SUBMIT", {}),
            _event("OMS_STATE", {"status": "open"}),
            _event("OMS_STATE", {"status": "closed"}),
        ]),
    ]
    trades = [
        _make_trade(pnl=150.0, ai_verdict="COHERENT", ai_risk_multiplier=1.0, market_regime="trending_bull", entry_timing="IMMEDIATE"),
    ]

    result = build_paper_evaluation(records, trades)

    assert "funnel" in result
    assert "conversion_rates" in result["funnel"]
    assert "stage_denominators" in result["funnel"]
    assert "ai_review_rate" in result["funnel"]["conversion_rates"]
    assert "performance" in result
    assert "cohorts" in result
    assert "by_ai_verdict" in result["cohorts"]
    assert "by_risk_multiplier" in result["cohorts"]
    assert "by_market_regime" in result["cohorts"]
    assert "by_ai_timing_advice" in result["cohorts"]
    assert "by_realized_entry_timing" in result["cohorts"]
    assert "by_entry_timing" in result["cohorts"]
    assert "tri_core_vs_ai" in result
    comparison = result["tri_core_vs_ai"]
    assert comparison["metadata"]["baseline_method"] == "unscaled_observed_trades"
    assert comparison["metadata"]["confidence"] == "approximate"
    assert comparison["metadata"]["counterfactual_replay"] is False
    assert "counterfactual_filtered_candidates" in comparison
    assert comparison["tri_core_with_ai"]["net_pnl"] == 150.0


@pytest.mark.asyncio
async def test_paper_evaluation_api_endpoint():
    from app.main import app
    from app.core.config import get_settings
    from httpx import ASGITransport, AsyncClient

    cfg = get_settings()
    dummy_eval = {
        "funnel": {"counts": {"candidates": 1}},
        "performance": {"closed_trades": 1, "net_pnl": 100.0},
        "cohorts": {"by_ai_verdict": {}},
        "tri_core_vs_ai": {"tri_core_baseline": {}, "tri_core_with_ai": {}},
    }
    with patch("app.services.paper_evaluation.evaluate_live_paper", AsyncMock(return_value=dummy_eval)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/paper/evaluation",
                headers={"X-API-Key": cfg.app_secret_key},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "funnel" in data
            assert "performance" in data
            assert "cohorts" in data
            assert "tri_core_vs_ai" in data


def test_funnel_partial_then_filled_counts_as_full_fill():
    from app.services.paper_evaluation import build_paper_funnel

    records = [
        # Order 1: partial fill then fully filled
        _record("snap1:event1", [
            _event("SNAPSHOT", {}),
            _event("OMS_SUBMIT", {}),
            _event("OMS_STATE", {"status": "partially_filled", "remaining_quantity": 0.5}),
            _event("OMS_STATE", {"status": "filled", "remaining_quantity": 0.0}),
        ]),
        # Order 2: partial fill only (remaining_quantity > 0)
        _record("snap2:event2", [
            _event("SNAPSHOT", {}),
            _event("OMS_SUBMIT", {}),
            _event("OMS_STATE", {"status": "partially_filled", "remaining_quantity": 0.5}),
        ]),
        # Order 3: immediate full fill
        _record("snap3:event3", [
            _event("SNAPSHOT", {}),
            _event("OMS_SUBMIT", {}),
            _event("OMS_STATE", {"status": "filled", "remaining_quantity": 0.0}),
        ]),
    ]

    funnel = build_paper_funnel(records)
    counts = funnel["counts"]

    # Order 1 (partial->filled) and Order 3 (filled) are full fills = 2
    assert counts["orders_filled_full"] == 2
    # Order 2 (partial only) is partial fill = 1
    assert counts["orders_partially_filled"] == 1
    # Both are counted in filled_or_partial = 3
    assert counts["orders_filled_or_partial"] == 3
    assert funnel["conversion_rates"]["order_fill_rate"] == round(2 / 3, 4)
    assert funnel["conversion_rates"]["partial_fill_rate"] == round(1 / 3, 4)
    assert funnel["conversion_rates"]["execution_fill_rate"] == 1.0
