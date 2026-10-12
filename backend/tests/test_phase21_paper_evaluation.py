from app.services.paper_evaluation import (
    build_paper_evaluation,
    build_paper_funnel,
    build_review_cohort_metrics,
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


def _trade(
    *,
    trace_id,
    pnl,
    opened,
    closed,
    risk=100.0,
    fees=2.0,
    spread=1.0,
    slippage=0.5,
    max_r=1.5,
):
    return {
        "decision_trace_id": trace_id,
        "status": "closed",
        "realized_pnl_net": pnl,
        "estimated_risk": risk,
        "fees_total": fees,
        "spread_cost_total": spread,
        "slippage_cost_total": slippage,
        "opened_at": opened,
        "closed_at": closed,
        "max_r_multiple": max_r,
    }


def test_phase21_funnel_counts_pipeline_and_rejections():
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
        _record("snap2:event2", [
            _event("SNAPSHOT", {}),
            _event("AI_REVIEW", {"verdict": "CONFLICT"}),
        ]),
        _record("snap3:event3", [
            _event("SNAPSHOT", {}),
            _event("AI_REVIEW", {"verdict": "COHERENT"}),
            _event("EXECUTION_REVALIDATION", {
                "passed": False,
                "reason_code": "NO_CHASE",
            }),
        ]),
        _record("snap4:event4", [
            _event("SNAPSHOT", {}),
            _event("AI_REVIEW", {"verdict": "COHERENT"}),
            _event("EXECUTION_REVALIDATION", {"passed": True}),
            _event("RISK", {
                "approved": False,
                "rejection_reason": "Daily loss limit reached",
            }),
        ]),
    ]

    funnel = build_paper_funnel(records)

    assert funnel["counts"]["candidates"] == 4
    assert funnel["counts"]["ai_coherent"] == 3
    assert funnel["counts"]["ai_conflict"] == 1
    assert funnel["counts"]["execution_validated"] == 2
    assert funnel["counts"]["execution_rejected"] == 1
    assert funnel["counts"]["risk_approved"] == 1
    assert funnel["counts"]["risk_rejected"] == 1
    assert funnel["counts"]["orders_submitted"] == 1
    assert funnel["counts"]["trades_closed"] == 1
    assert funnel["rejection_reasons"]["AI_CONFLICT"] == 1
    assert funnel["rejection_reasons"]["NO_CHASE"] == 1


def test_phase21_paper_metrics_use_net_pnl_r_hold_costs_and_drawdown():
    trades = [
        _trade(
            trace_id="snap1:event1",
            pnl=150.0,
            risk=100.0,
            opened="2026-09-20T10:00:00+00:00",
            closed="2026-09-20T11:00:00+00:00",
            max_r=2.0,
        ),
        _trade(
            trace_id="snap2:event2",
            pnl=-100.0,
            risk=100.0,
            opened="2026-09-20T12:00:00+00:00",
            closed="2026-09-20T14:00:00+00:00",
            max_r=0.5,
        ),
    ]

    metrics = calculate_paper_metrics(trades, initial_equity=1000.0)

    assert metrics["closed_trades"] == 2
    assert metrics["win_rate"] == 0.5
    assert metrics["profit_factor"] == 1.5
    assert metrics["expectancy_net"] == 25.0
    assert metrics["expectancy_r"] == 0.25
    assert metrics["net_pnl"] == 50.0
    assert metrics["average_hold_minutes"] == 90.0
    assert metrics["mfe_r_average"] == 1.25
    assert metrics["mae_r_average"] is None
    assert metrics["mae_status"] == "unavailable_no_excursion_telemetry"
    assert metrics["fees_total"] == 4.0
    assert metrics["spread_cost_total"] == 2.0
    assert metrics["slippage_cost_total"] == 1.0
    assert metrics["max_drawdown_pct"] > 0


def test_phase21_profit_factor_without_losses_is_not_infinite():
    metrics = calculate_paper_metrics([
        _trade(
            trace_id="snap1:event1",
            pnl=100.0,
            opened="2026-09-20T10:00:00+00:00",
            closed="2026-09-20T11:00:00+00:00",
        )
    ])

    assert metrics["profit_factor"] is None
    assert metrics["profit_factor_status"] == "no_losses"


def test_phase21_review_cohorts_join_by_decision_trace_id():
    records = [
        _record("snap1:event1", [
            _event("AI_REVIEW", {"verdict": "COHERENT"}),
        ]),
        _record("snap2:event2", [
            _event("AI_REVIEW", {"verdict": "CONFLICT"}),
        ]),
    ]
    trades = [
        _trade(
            trace_id="snap1:event1",
            pnl=100.0,
            opened="2026-09-20T10:00:00+00:00",
            closed="2026-09-20T11:00:00+00:00",
        ),
        _trade(
            trace_id="snap2:event2",
            pnl=-50.0,
            opened="2026-09-20T12:00:00+00:00",
            closed="2026-09-20T13:00:00+00:00",
        ),
    ]

    cohorts = build_review_cohort_metrics(records, trades)

    assert cohorts["COHERENT"]["closed_trades"] == 1
    assert cohorts["COHERENT"]["net_pnl"] == 100.0
    assert cohorts["CONFLICT"]["closed_trades"] == 1
    assert cohorts["CONFLICT"]["net_pnl"] == -50.0


def test_phase21_evaluation_reports_unknown_excursion_without_estimation():
    evaluation = build_paper_evaluation([], [])

    assert evaluation["telemetry"]["mae"] == "unavailable_no_excursion_telemetry"
    assert evaluation["telemetry"]["unknown_values_are_not_estimated"] is True


def test_phase21_r_multiple_prefers_risk_engine_approved_risk_amount():
    trade = _trade(
        trace_id="snap1:event1",
        pnl=50.0,
        risk=100.0,
        opened="2026-09-20T10:00:00+00:00",
        closed="2026-09-20T11:00:00+00:00",
    )
    trade["source_payload"] = {
        "risk_assessment": {
            "risk_amount": 50.0,
            "sizing_authority": "risk_engine",
        }
    }

    metrics = calculate_paper_metrics([trade])

    assert metrics["expectancy_r"] == 1.0
