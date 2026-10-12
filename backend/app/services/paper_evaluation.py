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
    for event in record.get("events", []) or []:
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

    for record in records:
        stages = _event_map(record)
        if stages.get("SNAPSHOT"):
            counts["candidates"] += 1
        ai = _latest_facts(stages, "AI_REVIEW")
        if ai:
            counts["ai_reviewed_or_attempted"] += 1
            verdict = str(ai.get("verdict") or "").upper()
            if verdict == "COHERENT":
                counts["ai_coherent"] += 1
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
        statuses = {
            str((event.get("facts") or {}).get("status") or "").lower()
            for event in oms_states
        }
        if statuses & {"open", "partial", "partially_filled"}:
            counts["orders_filled_or_partial"] += 1
        if "closed" in statuses:
            counts["trades_closed"] += 1

    return {
        "counts": dict(counts),
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
        trace_id = str(trade.get("decision_trace_id") or "")
        verdict = verdict_by_trace.get(trace_id, "NOT_REVIEWED")
        grouped[verdict].append(trade)

    return {
        verdict: calculate_paper_metrics(items, initial_equity=initial_equity)
        for verdict, items in sorted(grouped.items())
    }
def build_paper_evaluation(
    records: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    *,
    initial_equity: float = 10_000.0,
) -> dict[str, Any]:
    return {
        "funnel": build_paper_funnel(records),
        "performance": calculate_paper_metrics(trades, initial_equity=initial_equity),
        "review_cohorts": build_review_cohort_metrics(
            records,
            trades,
            initial_equity=initial_equity,
        ),
        "telemetry": {
            "mfe": "available_from_paper_oms.max_r_multiple",
            "mae": "unavailable_no_excursion_telemetry",
            "unknown_values_are_not_estimated": True,
        },
    }


async def evaluate_live_paper() -> dict[str, Any]:
    records = await decision_trace.list_records()
    positions = await paper_oms.list_positions(include_live=False)
    account = await paper_oms.account_snapshot()
    initial_equity = float(account.get("initial_capital") or 10_000.0)
    return build_paper_evaluation(
        records,
        list(positions.get("trades") or []),
        initial_equity=initial_equity,
    )
