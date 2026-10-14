"""Paper-trading funnel and performance evaluation from auditable records."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean, median
from typing import Any

from app.services.decision_trace import decision_trace
from app.services.paper_oms import paper_oms


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_map(record: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    stages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in (record.get("events") or record.get("stages") or []):
        stages[str(event.get("stage") or "")].append(event)
    return stages
def _latest_facts(stages: dict[str, list[dict[str, Any]]], stage: str) -> dict[str, Any]:
    events = stages.get(stage) or []
    return dict((events[-1].get("facts") or {})) if events else {}


def _trace_verdict(record: dict[str, Any]) -> str:
    facts = _latest_facts(_event_map(record), "AI_REVIEW")
    return str(facts.get("verdict") or "NOT_REVIEWED").upper()


def build_paper_funnel(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter()
    rejections = Counter()
    ai_coherent_candidates: set[str] = set()
    execution_validated_after_ai_coherent: set[str] = set()

    for record in records:
        cid = str(record.get("candidate_id") or f"{record.get('snapshot_id')}:{record.get('event_id')}")
        stages = _event_map(record)
        if stages.get("SNAPSHOT"):
            counts["candidates"] += 1
        ai = _latest_facts(stages, "AI_REVIEW")
        is_coherent = False
        if ai:
            counts["ai_reviewed_or_attempted"] += 1
            verdict = str(ai.get("verdict") or "").upper()
            if verdict == "COHERENT":
                counts["ai_coherent"] += 1
                is_coherent = True
                ai_coherent_candidates.add(cid)
            elif verdict == "CONFLICT":
                counts["ai_conflict"] += 1
                rejections["AI_CONFLICT"] += 1
            elif verdict == "UNAVAILABLE":
                counts["ai_unavailable"] += 1
                rejections[str(ai.get("failure_type") or "AI_UNAVAILABLE")] += 1
        revalidation = _latest_facts(stages, "EXECUTION_REVALIDATION")
        if revalidation:
            counts["execution_revalidated"] += 1
            if revalidation.get("passed") is True:
                counts["execution_validated"] += 1
                if is_coherent or cid in ai_coherent_candidates:
                    execution_validated_after_ai_coherent.add(cid)
            else:
                counts["execution_rejected"] += 1
                rejections[str(revalidation.get("reason_code") or "EXECUTION_REJECTED")] += 1

        risk = _latest_facts(stages, "RISK")
        if risk:
            counts["risk_evaluated"] += 1
            if risk.get("approved") is True:
                counts["risk_approved"] += 1
            else:
                counts["risk_rejected"] += 1
                rejections[str(risk.get("rejection_reason") or "RISK_REJECTED")] += 1

        if stages.get("OMS_SUBMIT"):
            counts["orders_submitted"] += 1

        oms_states = stages.get("OMS_STATE") or []
        statuses = [
            str((event.get("facts") or {}).get("status") or "").lower()
            for event in oms_states
        ]
        status_set = set(statuses)
        has_filled = "filled" in status_set
        has_partial = bool(status_set & {"partial", "partially_filled", "partially-filled"})
        has_open = bool(status_set & {"open"})

        # Prioritize terminal fill status: filled > partial > open
        # If an order is explicitly 'filled', or progressed partial -> filled, count as full fill.
        if has_filled:
            counts["orders_filled_full"] += 1
            counts["orders_filled_or_partial"] += 1
        elif has_partial:
            latest_facts = oms_states[-1].get("facts") or {} if oms_states else {}
            latest_status = str(latest_facts.get("status") or "").lower()
            rem_qty = float(latest_facts.get("remaining_quantity") or 0.0)
            if latest_status in {"filled", "open", "closed"} and rem_qty <= 0:
                counts["orders_filled_full"] += 1
                counts["orders_filled_or_partial"] += 1
            else:
                counts["orders_partially_filled"] += 1
                counts["orders_filled_or_partial"] += 1
        elif has_open:
            counts["orders_filled_full"] += 1
            counts["orders_filled_or_partial"] += 1

        if "closed" in status_set:
            counts["trades_closed"] += 1

    candidates = counts["candidates"]
    reviewed = counts["ai_reviewed_or_attempted"]
    conflicts = counts["ai_conflict"]
    coherent = counts["ai_coherent"]
    exec_reval = counts["execution_revalidated"]
    exec_valid = counts["execution_validated"]
    risk_eval = counts["risk_evaluated"]
    risk_appr = counts["risk_approved"]
    submitted = counts["orders_submitted"]
    filled_full = counts["orders_filled_full"]
    partial_fill = counts["orders_partially_filled"]
    filled_or_partial = counts["orders_filled_or_partial"]
    closed = counts["trades_closed"]

    ai_coherent_count = len(ai_coherent_candidates)
    exec_valid_after_coherent_count = len(execution_validated_after_ai_coherent)
    counts["ai_coherent_candidates"] = ai_coherent_count
    counts["execution_validated_after_ai_coherent"] = exec_valid_after_coherent_count

    conversion_rates = {
        "ai_review_rate": round(reviewed / candidates, 4) if candidates > 0 else 0.0,
        "ai_conflict_rate": round(conflicts / reviewed, 4) if reviewed > 0 else 0.0,
        "tri_core_to_ai_approval_rate": round(coherent / candidates, 4) if candidates > 0 else 0.0,
        "ai_approval_rate": round(coherent / reviewed, 4) if reviewed > 0 else 0.0,
        "ai_approved_to_execution_validation_rate": (
            round(exec_valid_after_coherent_count / ai_coherent_count, 4)
            if ai_coherent_count > 0
            else 0.0
        ),
        "execution_revalidation_pass_rate": round(exec_valid / exec_reval, 4) if exec_reval > 0 else 0.0,
        "risk_approval_rate": round(risk_appr / risk_eval, 4) if risk_eval > 0 else 0.0,
        "order_fill_rate": round(filled_full / submitted, 4) if submitted > 0 else 0.0,
        "partial_fill_rate": round(partial_fill / submitted, 4) if submitted > 0 else 0.0,
        "execution_fill_rate": round(filled_or_partial / submitted, 4) if submitted > 0 else 0.0,
        "overall_candidate_to_fill_rate": round(filled_or_partial / candidates, 4) if candidates > 0 else 0.0,
    }

    stage_denominators = {
        "ai_review": {"numerator": reviewed, "denominator": candidates, "formula": "ai_reviewed / candidates"},
        "ai_conflict": {"numerator": conflicts, "denominator": reviewed, "formula": "ai_conflict / ai_reviewed"},
        "ai_approval": {"numerator": coherent, "denominator": reviewed, "formula": "ai_coherent / ai_reviewed"},
        "ai_approved_to_execution_validation": {
            "numerator": exec_valid_after_coherent_count,
            "denominator": ai_coherent_count,
            "formula": "execution_validated_after_ai_coherent / ai_coherent_candidates",
        },
        "execution_validation": {"numerator": exec_valid, "denominator": exec_reval, "formula": "execution_validated / execution_revalidated"},
        "risk_approval": {"numerator": risk_appr, "denominator": risk_eval, "formula": "risk_approved / risk_evaluated"},
        "order_fill": {"numerator": filled_full, "denominator": submitted, "formula": "orders_filled_full / submitted_orders"},
        "partial_fill": {"numerator": partial_fill, "denominator": submitted, "formula": "orders_partially_filled / submitted_orders"},
        "execution_fill": {"numerator": filled_or_partial, "denominator": submitted, "formula": "(filled_full + partially_filled) / submitted_orders"},
    }

    return {
        "counts": dict(counts),
        "conversion_rates": conversion_rates,
        "stage_denominators": stage_denominators,
        "rejection_reasons": dict(rejections.most_common()),
    }
def _trade_r_multiple(trade: dict[str, Any]) -> float | None:
    source_payload = trade.get("source_payload") or {}
    risk_assessment = (
        source_payload.get("risk_assessment")
        if isinstance(source_payload, dict)
        else {}
    ) or {}
    risk = float(
        risk_assessment.get("risk_amount")
        or trade.get("estimated_risk")
        or 0.0
    )
    pnl = float(trade.get("realized_pnl_net") or 0.0)
    return pnl / risk if risk > 0 else None


def _max_drawdown_pct(closed: list[dict[str, Any]], initial_equity: float) -> float:
    equity = float(initial_equity)
    peak = equity
    max_dd = 0.0
    for trade in sorted(closed, key=lambda row: str(row.get("closed_at") or "")):
        equity += float(trade.get("realized_pnl_net") or 0.0)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)
    return max_dd


def calculate_paper_metrics(
    trades: list[dict[str, Any]],
    *,
    initial_equity: float = 10_000.0,
) -> dict[str, Any]:
    closed = [trade for trade in trades if str(trade.get("status")) == "closed"]
    pnls = [float(trade.get("realized_pnl_net") or 0.0) for trade in closed]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    r_values = [value for trade in closed if (value := _trade_r_multiple(trade)) is not None]
    hold_minutes: list[float] = []
    for trade in closed:
        opened = _ts(trade.get("opened_at"))
        closed_at = _ts(trade.get("closed_at"))
        if opened and closed_at and closed_at >= opened:
            hold_minutes.append((closed_at - opened).total_seconds() / 60.0)

    fees = sum(float(trade.get("fees_total") or 0.0) for trade in closed)
    spread = sum(float(trade.get("spread_cost_total") or 0.0) for trade in closed)
    slippage = sum(float(trade.get("slippage_cost_total") or 0.0) for trade in closed)
    max_r = [
        float(trade.get("max_r_multiple"))
        for trade in closed
        if trade.get("max_r_multiple") is not None
    ]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    profit_factor_status = (
        "calculated" if gross_loss > 0
        else "no_losses" if wins
        else "unavailable_no_closed_wins_or_losses"
    )

    return {
        "closed_trades": len(closed),
        "win_rate": (len(wins) / len(closed)) if closed else 0.0,
        "profit_factor": profit_factor,
        "profit_factor_status": profit_factor_status,
        "expectancy_net": mean(pnls) if pnls else 0.0,
        "expectancy_r": mean(r_values) if r_values else None,
        "median_r": median(r_values) if r_values else None,
        "max_drawdown_pct": _max_drawdown_pct(closed, initial_equity),
        "net_pnl": sum(pnls),
        "fees_total": fees,
        "spread_cost_total": spread,
        "slippage_cost_total": slippage,
        "average_hold_minutes": mean(hold_minutes) if hold_minutes else None,
        "median_hold_minutes": median(hold_minutes) if hold_minutes else None,
        "mfe_r_average": mean(max_r) if max_r else None,
        "mae_r_average": None,
        "mae_status": "unavailable_no_excursion_telemetry",
    }


def _extract_trade_verdict(trade: dict[str, Any], verdict_by_trace: dict[str, str]) -> str:
    source_payload = trade.get("source_payload") or {}
    verdict = (
        trade.get("ai_review_verdict")
        or (source_payload.get("ai_review_verdict") if isinstance(source_payload, dict) else None)
        or verdict_by_trace.get(str(trade.get("decision_trace_id") or ""))
        or "NOT_REVIEWED"
    )
    return str(verdict).upper()


def _extract_trade_multiplier_bucket(trade: dict[str, Any]) -> str:
    source_payload = trade.get("source_payload") or {}
    raw_mult = (
        trade.get("ai_risk_multiplier")
        if trade.get("ai_risk_multiplier") is not None
        else (source_payload.get("ai_risk_multiplier") if isinstance(source_payload, dict) else None)
    )
    try:
        mult = float(raw_mult) if raw_mult is not None else 1.0
    except (TypeError, ValueError):
        mult = 1.0

    if mult >= 0.95:
        return "1.0x_full"
    elif mult >= 0.45:
        return "0.5x_caution"
    else:
        return f"{mult:.1f}x_reduced"


def _extract_trade_regime(trade: dict[str, Any]) -> str:
    source_payload = trade.get("source_payload") or {}
    regime = (
        trade.get("market_regime")
        or (source_payload.get("market_regime") if isinstance(source_payload, dict) else None)
        or "unknown"
    )
    return str(regime).lower()


def _extract_ai_timing_advice(trade: dict[str, Any]) -> str:
    source_payload = trade.get("source_payload") or {}
    advice = (
        trade.get("ai_timing_advice")
        or (source_payload.get("ai_timing_advice") if isinstance(source_payload, dict) else None)
        or trade.get("ai_timing")
        or (source_payload.get("ai_timing") if isinstance(source_payload, dict) else None)
        or "IMMEDIATE"
    )
    return str(advice).upper()


def _extract_realized_entry_timing(trade: dict[str, Any]) -> str:
    source_payload = trade.get("source_payload") or {}
    timing = (
        trade.get("realized_entry_timing")
        or (source_payload.get("realized_entry_timing") if isinstance(source_payload, dict) else None)
        or trade.get("order_type")
        or "MARKET"
    )
    return str(timing).upper()


def _extract_trade_timing(trade: dict[str, Any]) -> str:
    """Legacy alias: maps to realized entry timing."""
    return _extract_realized_entry_timing(trade)


def build_review_cohort_metrics(
    records: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    *,
    initial_equity: float = 10_000.0,
) -> dict[str, Any]:
    verdict_by_trace = {
        str(record.get("candidate_id") or ""): _trace_verdict(record)
        for record in records
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        verdict = _extract_trade_verdict(trade, verdict_by_trace)
        grouped[verdict].append(trade)

    return {
        verdict: calculate_paper_metrics(items, initial_equity=initial_equity)
        for verdict, items in sorted(grouped.items())
    }


def build_multiplier_cohort_metrics(
    trades: list[dict[str, Any]],
    *,
    initial_equity: float = 10_000.0,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        bucket = _extract_trade_multiplier_bucket(trade)
        grouped[bucket].append(trade)

    return {
        bucket: calculate_paper_metrics(items, initial_equity=initial_equity)
        for bucket, items in sorted(grouped.items())
    }


def build_regime_cohort_metrics(
    trades: list[dict[str, Any]],
    *,
    initial_equity: float = 10_000.0,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        regime = _extract_trade_regime(trade)
        grouped[regime].append(trade)

    return {
        regime: calculate_paper_metrics(items, initial_equity=initial_equity)
        for regime, items in sorted(grouped.items())
    }


def build_ai_timing_cohort_metrics(
    trades: list[dict[str, Any]],
    *,
    initial_equity: float = 10_000.0,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        timing = _extract_ai_timing_advice(trade)
        grouped[timing].append(trade)

    return {
        timing: calculate_paper_metrics(items, initial_equity=initial_equity)
        for timing, items in sorted(grouped.items())
    }


def build_realized_timing_cohort_metrics(
    trades: list[dict[str, Any]],
    *,
    initial_equity: float = 10_000.0,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        timing = _extract_realized_entry_timing(trade)
        grouped[timing].append(trade)

    return {
        timing: calculate_paper_metrics(items, initial_equity=initial_equity)
        for timing, items in sorted(grouped.items())
    }


def build_timing_cohort_metrics(
    trades: list[dict[str, Any]],
    *,
    initial_equity: float = 10_000.0,
) -> dict[str, Any]:
    """Legacy alias that routes to realized entry timing."""
    return build_realized_timing_cohort_metrics(trades, initial_equity=initial_equity)


def extract_counterfactual_filtered_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract candidate setups where Tri-Core was actionable but AI blocked or altered execution."""
    filtered_list: list[dict[str, Any]] = []
    for record in records:
        stages = _event_map(record)
        filter_events = stages.get("AI_GATE_FILTER") or []
        ai_events = stages.get("AI_REVIEW") or []
        snapshot_events = stages.get("SNAPSHOT") or []

        if filter_events:
            facts = filter_events[-1].get("facts") or {}
            filtered_list.append({
                "candidate_id": record.get("candidate_id"),
                "snapshot_id": record.get("snapshot_id"),
                "event_id": record.get("event_id"),
                "symbol": record.get("symbol") or facts.get("symbol"),
                "direction": facts.get("direction"),
                "entry": facts.get("entry"),
                "stop_loss": facts.get("stop_loss"),
                "take_profit": facts.get("take_profit"),
                "timeframe": facts.get("timeframe"),
                "verdict": facts.get("verdict"),
                "action": facts.get("action"),
                "filter_reason": facts.get("reason"),
                "reason_code": facts.get("reason_code"),
                "occurred_at": filter_events[-1].get("occurred_at"),
            })
        elif ai_events:
            facts = ai_events[-1].get("facts") or {}
            verdict = str(facts.get("verdict") or "").upper()
            if verdict in {"CONFLICT", "UNAVAILABLE"}:
                snap_facts = snapshot_events[-1].get("facts") or {} if snapshot_events else {}
                filtered_list.append({
                    "candidate_id": record.get("candidate_id"),
                    "snapshot_id": record.get("snapshot_id"),
                    "event_id": record.get("event_id"),
                    "symbol": record.get("symbol") or snap_facts.get("symbol"),
                    "direction": snap_facts.get("direction"),
                    "entry": snap_facts.get("entry") or snap_facts.get("close"),
                    "stop_loss": snap_facts.get("stop_loss"),
                    "take_profit": snap_facts.get("take_profit"),
                    "timeframe": snap_facts.get("timeframe"),
                    "verdict": verdict,
                    "action": "BLOCKED",
                    "filter_reason": facts.get("reason") or f"AI verdict {verdict}",
                    "reason_code": facts.get("reason_code") or f"AI_{verdict}",
                    "occurred_at": ai_events[-1].get("occurred_at"),
                })
    return filtered_list


def build_tri_core_vs_ai_comparison(
    records: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    *,
    initial_equity: float = 10_000.0,
    replay_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare executed trades under AI management vs unscaled rough baseline and counterfactual replay."""
    actual_metrics = calculate_paper_metrics(trades, initial_equity=initial_equity)

    # Reconstruct unscaled Tri-Core trades (as if multiplier was 1.0x on every trade)
    simulated_baseline_trades = []
    for trade in trades:
        sim = dict(trade)
        source_payload = trade.get("source_payload") or {}
        raw_mult = (
            trade.get("ai_risk_multiplier")
            if trade.get("ai_risk_multiplier") is not None
            else (source_payload.get("ai_risk_multiplier") if isinstance(source_payload, dict) else None)
        )
        try:
            mult = float(raw_mult) if raw_mult is not None else 1.0
        except (TypeError, ValueError):
            mult = 1.0

        if 0.0 < mult < 0.99:
            # Unscale net pnl and estimated risk
            pnl = float(trade.get("realized_pnl_net") or 0.0)
            sim["realized_pnl_net"] = pnl / mult
            risk = float(trade.get("estimated_risk") or 0.0)
            if risk > 0:
                sim["estimated_risk"] = risk / mult
        simulated_baseline_trades.append(sim)

    baseline_metrics = calculate_paper_metrics(simulated_baseline_trades, initial_equity=initial_equity)
    funnel = build_paper_funnel(records)
    rejections = funnel.get("rejection_reasons", {})
    filtered_candidates = extract_counterfactual_filtered_candidates(records)

    # Check for replay results
    if replay_results is None and filtered_candidates:
        try:
            from app.services.counterfactual_replay import CounterfactualReplayEngine
            engine = CounterfactualReplayEngine()
            cached = engine.load_cache()
            if cached:
                filtered_cids = {
                    str(c.get("candidate_id") or f"{c.get('snapshot_id')}:{c.get('event_id')}")
                    for c in filtered_candidates
                }
                matching = [res for cid, res in cached.items() if cid in filtered_cids]
                if matching:
                    replay_results = matching
        except Exception:
            replay_results = None

    has_replay = bool(replay_results and filtered_candidates)
    replay_summary: dict[str, Any] | None = None
    pure_baseline_metrics: dict[str, Any] | None = None

    if has_replay and replay_results:
        from app.services.counterfactual_replay import (
            COVERAGE_THRESHOLD_FOR_VALIDATION,
            CounterfactualReplayEngine,
        )
        replay_summary = CounterfactualReplayEngine.summarize_replay(
            replay_results,
            total_filtered_candidates=filtered_candidates,
            initial_equity=initial_equity,
        )
        pure_baseline_metrics = CounterfactualReplayEngine.build_pure_tri_core_baseline(
            trades, replay_results, initial_equity=initial_equity
        )

    actual_dd = actual_metrics.get("max_drawdown_pct", 0.0)
    base_dd = (
        pure_baseline_metrics.get("max_drawdown_pct", 0.0)
        if (has_replay and pure_baseline_metrics)
        else baseline_metrics.get("max_drawdown_pct", 0.0)
    )
    dd_reduction = max(0.0, base_dd - actual_dd) if base_dd > 0 else 0.0

    coverage_pct = float(replay_summary.get("coverage_pct", 0.0)) if replay_summary else 0.0
    replay_status = str(replay_summary.get("replay_status", "pending_market_replay_implementation")) if replay_summary else "pending_market_replay_implementation"
    is_fully_validated = bool(has_replay and coverage_pct >= 0.95 and replay_status == "replay_validated")
    confidence_tier = (
        "counterfactual_replay_validated"
        if is_fully_validated
        else ("counterfactual_replay_partial" if has_replay else "preliminary_approximate")
    )

    metadata: dict[str, Any]
    if has_replay and replay_summary:
        metadata = {
            "baseline_method": "counterfactual_market_replay",
            "confidence": confidence_tier,
            "counterfactual_replay": is_fully_validated,
            "replay_status": replay_status,
            "coverage_pct": coverage_pct,
            "coverage_threshold_for_validation": 0.95,
            "candle_boundary_policy": "strict_next_bar_open",
            "sample_size_assessment": replay_summary.get("sample_size_assessment", "insufficient_decisive_samples"),
            "disclaimer": (
                "Replay-validated baseline: incorporates deterministic bar-by-bar market replay "
                "of actionable Tri-Core candidates that were blocked or filtered by AI."
                if is_fully_validated
                else f"Partial replay baseline (coverage {round(coverage_pct * 100, 1)}% < 95% threshold). "
                "Requires >= 95% coverage across eligible filtered candidates for full validation."
            ),
        }
    else:
        metadata = {
            "baseline_method": "unscaled_observed_trades",
            "confidence": "approximate",
            "counterfactual_replay": False,
            "non_linear_factors_ignored": [
                "non_linear_fees",
                "spread_slippage_quantity_scaling",
                "partial_fills",
                "tp1_scale_out",
                "trailing_stop",
                "breakeven",
                "portfolio_drawdown_compounding",
            ],
            "disclaimer": (
                "Preliminary rough estimate based on unscaling executed trades. "
                "Does NOT represent true counterfactual Tri-Core performance because "
                "candidates rejected or filtered by AI are omitted pending counterfactual market replay."
            ),
        }

    res: dict[str, Any] = {
        "metadata": metadata,
        "executed_trade_unscaled_baseline": baseline_metrics,
        "tri_core_with_ai": actual_metrics,
        "tri_core_baseline": pure_baseline_metrics if (has_replay and pure_baseline_metrics) else baseline_metrics,
        "counterfactual_filtered_candidates": {
            "count": len(filtered_candidates),
            "by_verdict": dict(Counter(str(c.get("verdict") or "UNKNOWN") for c in filtered_candidates)),
            "sample": filtered_candidates[:20],
            "replay_status": replay_status,
            "coverage_pct": coverage_pct,
            "replay_summary": replay_summary if has_replay else None,
        },
        "delta": {
            "net_pnl_diff": round(
                actual_metrics.get("net_pnl", 0.0)
                - (pure_baseline_metrics.get("net_pnl", 0.0) if (has_replay and pure_baseline_metrics) else baseline_metrics.get("net_pnl", 0.0)),
                2,
            ),
            "win_rate_diff": round(
                actual_metrics.get("win_rate", 0.0)
                - (pure_baseline_metrics.get("win_rate", 0.0) if (has_replay and pure_baseline_metrics) else baseline_metrics.get("win_rate", 0.0)),
                4,
            ),
            "max_drawdown_reduction_pct": round(dd_reduction, 2),
            "drawdown_reduced": actual_dd < base_dd,
            "filtered_conflict_candidates": rejections.get("AI_CONFLICT", 0),
            "filtered_unavailable_candidates": (
                rejections.get("AI_UNAVAILABLE", 0)
                + rejections.get("STALE_REVIEW", 0)
                + rejections.get("MODEL_TIMEOUT", 0)
            ),
        },
        "evaluation_readiness": {
            "closed_trades": actual_metrics.get("closed_trades", 0),
            "required_trades_for_live": 30,
            "status": "ready_for_review" if actual_metrics.get("closed_trades", 0) >= 30 else "accumulating_paper_data",
            "live_auto_pilot_recommended": False,  # Strict safeguard: always requires manual promotion
            "promotion_evidence_status": (
                (
                    "ready_for_operator_review"
                    if actual_metrics.get("closed_trades", 0) >= 30 and is_fully_validated
                    else (
                        "replay_partial_accumulating_trades"
                        if not is_fully_validated
                        else "replay_validated_accumulating_trades"
                    )
                )
                if has_replay
                else "not_ready_counterfactual_replay_required"
            ),
            "cohort_reliability": "reliable",
            "comparison_reliability": confidence_tier if has_replay else "preliminary_approximate",
        },
    }

    if has_replay and pure_baseline_metrics:
        res["pure_tri_core_baseline"] = pure_baseline_metrics
    if has_replay and replay_summary:
        res["counterfactual_replay"] = replay_summary
        res["delta"]["net_ai_filter_alpha_usd"] = replay_summary["net_ai_filter_alpha"]["net_dollar_alpha"]
        res["delta"]["net_ai_filter_alpha_r"] = replay_summary["net_ai_filter_alpha"]["net_r_alpha"]
        res["delta"]["avoided_losses_count"] = replay_summary["avoided_losses"]["count"]
        res["delta"]["missed_profits_count"] = replay_summary["missed_profits"]["count"]
        res["delta"]["filter_precision"] = replay_summary["filter_precision"]

    return res


def build_paper_evaluation(
    records: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    *,
    initial_equity: float = 10_000.0,
    replay_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    funnel = build_paper_funnel(records)
    perf = calculate_paper_metrics(trades, initial_equity=initial_equity)
    verdict_cohorts = build_review_cohort_metrics(records, trades, initial_equity=initial_equity)
    multiplier_cohorts = build_multiplier_cohort_metrics(trades, initial_equity=initial_equity)
    regime_cohorts = build_regime_cohort_metrics(trades, initial_equity=initial_equity)
    ai_timing_cohorts = build_ai_timing_cohort_metrics(trades, initial_equity=initial_equity)
    realized_timing_cohorts = build_realized_timing_cohort_metrics(trades, initial_equity=initial_equity)
    tri_core_vs_ai = build_tri_core_vs_ai_comparison(
        records, trades, initial_equity=initial_equity, replay_results=replay_results
    )

    return {
        "funnel": funnel,
        "performance": perf,
        "cohorts": {
            "by_ai_verdict": verdict_cohorts,
            "by_risk_multiplier": multiplier_cohorts,
            "by_market_regime": regime_cohorts,
            "by_ai_timing_advice": ai_timing_cohorts,
            "by_realized_entry_timing": realized_timing_cohorts,
            "by_entry_timing": realized_timing_cohorts,  # Legacy alias
        },
        "review_cohorts": verdict_cohorts,
        "tri_core_vs_ai": tri_core_vs_ai,
        "telemetry": {
            "mfe": "available_from_paper_oms.max_r_multiple",
            "mae": "unavailable_no_excursion_telemetry",
            "unknown_values_are_not_estimated": True,
        },
    }


async def evaluate_live_paper(*, replay_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    records = await decision_trace.list_records()
    positions = await paper_oms.list_positions(include_live=False)
    account = await paper_oms.account_snapshot()
    initial_equity = float(account.get("initial_capital") or 10_000.0)
    return build_paper_evaluation(
        records,
        list(positions.get("trades") or []),
        initial_equity=initial_equity,
        replay_results=replay_results,
    )
