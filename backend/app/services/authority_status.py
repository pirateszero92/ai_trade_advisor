"""Read-only UI projection of candidate authority stages."""

from __future__ import annotations

from typing import Any

from app.services.candidate_lifecycle import candidate_lifecycle
from app.services.decision_trace import decision_trace
from app.core.runtime_config import load_runtime_config


def _latest(events: list[dict[str, Any]], stage: str) -> dict[str, Any]:
    matches = [event for event in events if str(event.get("stage") or "") == stage]
    return dict((matches[-1].get("facts") or {})) if matches else {}


def _execution_status(facts: dict[str, Any], actionable: bool = False) -> str:
    if not facts:
        return "validated" if actionable else "not_run"
    intent = str(facts.get("intent") or "").lower()
    reason_code = str(facts.get("reason_code") or "").upper()
    if intent == "wait" or reason_code in {"MARKET_EXTENSION_EXCEEDED", "EXECUTABLE_RR_TOO_LOW"}:
        return "waiting_retest"
    if intent == "place_limit" or facts.get("auto_armed_limit") is True:
        return "limit_armed" if facts.get("passed") is True else "rejected"
    return "validated" if facts.get("passed") is True else "rejected"


def _risk_status(facts: dict[str, Any], actionable: bool = False) -> str:
    if not facts:
        return "approved" if actionable else "not_run"
    return "approved" if facts.get("approved") is True else "rejected"


def _quote_freshness(facts: dict[str, Any]) -> str:
    if not facts:
        return "unavailable"
    if str(facts.get("reason_code") or "").upper() == "QUOTE_STALE":
        return "stale"
    if facts.get("quote_freshness"):
        return str(facts.get("quote_freshness"))
    return "fresh" if facts.get("quote_age_seconds") is not None else "fresh"


def _project_lifecycle_from_oms(
    lifecycle: dict[str, Any] | None,
    oms_status: str,
) -> tuple[str, str, str]:
    """Make the immutable OMS outcome authoritative over a stale candidate row.

    Existing deployments can contain candidates that reached OPEN in the
    event-trigger path but were later closed by a market tick.  The OMS trace
    is the newest fact in that case and must win in the read model.
    """
    recorded_state = str((lifecycle or {}).get("state") or "UNKNOWN").upper()
    recorded_code = str((lifecycle or {}).get("reason_code") or "")
    recorded_reason = str((lifecycle or {}).get("reason") or "")
    normalized = str(oms_status or "").lower()
    projected = {
        "pending": "ORDER_PENDING",
        "new": "ORDER_PENDING",
        "accepted": "ORDER_PENDING",
        "submitted": "ORDER_PENDING",
        "open": "OPEN",
        "partial": "PARTIALLY_FILLED",
        "partially_filled": "PARTIALLY_FILLED",
        "closed": "CLOSED",
        "cancelled": "INVALIDATED",
        "canceled": "INVALIDATED",
    }.get(normalized)
    if projected is None:
        return recorded_state, recorded_code, recorded_reason

    close_reason = str((lifecycle or {}).get("reason") or "")
    if projected in {"CLOSED", "INVALIDATED"}:
        close_reason = str((lifecycle or {}).get("oms_close_reason") or close_reason)
    return projected, f"OMS_{normalized.upper()}", close_reason


async def build_authority_status(
    *,
    symbol: str,
    snapshot_id: str,
    event_id: str,
    tri_core_setup: dict[str, Any] | None = None,
    structural_rr: float | None = None,
) -> dict[str, Any]:
    setup = tri_core_setup or {}
    if structural_rr is None and setup.get("risk_reward") is not None:
        structural_rr = setup.get("risk_reward")
    record = await decision_trace.get(snapshot_id=snapshot_id, event_id=event_id)
    lifecycle = await candidate_lifecycle.get(
        snapshot_id=snapshot_id,
        event_id=event_id,
    )
    events = list((record or {}).get("events") or [])
    ai = _latest(events, "AI_REVIEW")
    systemic_risk = _latest(events, "SYSTEMIC_RISK")
    execution = _latest(events, "EXECUTION_REVALIDATION")
    risk = _latest(events, "RISK")
    oms = _latest(events, "OMS_STATE") or _latest(events, "OMS_RESULT")

    if not oms:
        try:
            from app.services.paper_oms import paper_oms
            active_snapshot = await paper_oms.list_positions(include_live=False)
            norm_sym = symbol.upper().replace(" ", "")
            matching = [
                p for p in (active_snapshot.get("trades") or [])
                if str(p.get("symbol", "")).upper().replace(" ", "") == norm_sym
                and str(p.get("status", "")).lower() in {"open", "pending", "partially_filled"}
            ]
            if matching:
                latest = matching[0]
                oms = {
                    "status": latest.get("status"),
                    "order_type": latest.get("order_type"),
                    "fill_price": latest.get("fill_price") or latest.get("entry"),
                    "filled_quantity": latest.get("filled_quantity") or latest.get("requested_quantity"),
                    "close_reason": latest.get("close_reason"),
                }
        except Exception:
            pass

    runtime = load_runtime_config()
    risk_cfg = runtime.get("risk_management", {}) if isinstance(runtime, dict) else {}
    ai_required = bool(risk_cfg.get("auto_trade_require_ai_approval", True))

    actionable = setup.get("actionable") is True
    if not execution and actionable:
        execution = {
            "passed": True,
            "reason_code": "SETUP_ACTIONABLE",
            "reasons": ["TriCore setup confirmed actionable"],
            "quote_freshness": "fresh",
            "structural_rr": structural_rr,
            "executable_rr": structural_rr,
            "order_type": setup.get("entry_policy") or "limit",
            "entry_policy": setup.get("entry_policy"),
            "original_entry": setup.get("entry"),
            "executable_entry": setup.get("entry"),
        }
    if not risk and actionable:
        risk = {
            "approved": True,
            "risk_pct": float(risk_cfg.get("risk_per_trade", 1.0)),
            "sizing_authority": "default_risk_budget",
        }

    ai_snapshot_matches = not ai or str(ai.get("input_snapshot_id") or snapshot_id) == snapshot_id
    if ai and not ai_snapshot_matches:
        ai = {
            "status": "discarded",
            "verdict": "UNAVAILABLE",
            "failure_type": "STALE_REVIEW",
            "reason": "AI review belongs to a different execution snapshot",
        }
    ai_status = str(ai.get("status") or "not_requested")
    ai_verdict = (
        "NOT_REQUESTED"
        if ai_status.lower() == "not_requested"
        else str(ai.get("verdict") or "UNAVAILABLE")
    )
    ai_coherent = (
        ai_status.lower() == "reviewed"
        and ai_verdict.upper() == "COHERENT"
    )
    exec_status = _execution_status(execution, actionable=actionable)
    execution_validated = exec_status in {"validated", "limit_armed"}
    risk_approved = _risk_status(risk, actionable=actionable) == "approved"
    systemic_allowed = systemic_risk.get("execution_allowed", True) is True
    effective_risk_status = _risk_status(risk, actionable=actionable)
    effective_risk_reason = risk.get("rejection_reason")
    if actionable and not systemic_allowed:
        effective_risk_status = "blocked"
        effective_risk_reason = (
            systemic_risk.get("reason")
            or systemic_risk.get("reason_code")
            or "Systemic market risk paused execution"
        )
    oms_status = str(oms.get("status") or "not_submitted").lower()
    lifecycle_projection = dict(lifecycle or {})
    lifecycle_projection["oms_close_reason"] = oms.get("close_reason")
    lifecycle_state, lifecycle_reason_code, lifecycle_reason = (
        _project_lifecycle_from_oms(lifecycle_projection, oms_status)
    )
    # A terminal/cancelled order still consumed this exact snapshot/event.
    # Only a new causal event may create another order; never re-arm the same
    # idempotency identity after an SL or cancellation.
    order_already_submitted = oms_status not in {
        "",
        "not_submitted",
        "rejected",
    }
    order_ready = bool(
        actionable
        and systemic_allowed
        and (ai_coherent or not ai_required)
        and execution_validated
        and risk_approved
        and not order_already_submitted
    )
    rejection_reasons = list(setup.get("rejection_reasons") or [])
    if order_ready and lifecycle_state in {"UNKNOWN", "REGISTERED", "AI_COHERENT", "AI_REVIEW_PENDING"}:
        lifecycle_state = "RISK_APPROVED"
        lifecycle_reason_code = "PIPELINE_VALIDATED"
        lifecycle_reason = "Pre-trade execution and risk checks approved"
    elif not lifecycle and not actionable:
        if any("session is closed" in str(r).lower() for r in rejection_reasons):
            lifecycle_state = "SESSION_CLOSED"
            lifecycle_reason_code = "SESSION_CLOSED"
            lifecycle_reason = rejection_reasons[0]
        elif rejection_reasons:
            lifecycle_state = "WAIT"
            lifecycle_reason_code = "NO_SETUP"
            lifecycle_reason = "; ".join(str(r) for r in rejection_reasons)
        else:
            lifecycle_state = "WAIT"
            lifecycle_reason_code = "WAITING_SETUP"
            lifecycle_reason = "Waiting for setup confirmation"

    return {
        "candidate_id": f"{snapshot_id}:{event_id}",
        "symbol": symbol,
        "lifecycle_state": lifecycle_state,
        "lifecycle_reason_code": lifecycle_reason_code,
        "lifecycle_reason": lifecycle_reason,
        "tri_core": {
            "status": "ready" if actionable else "wait",
            "actionable": actionable,
            "direction": str(setup.get("direction") or "wait"),
            "grade": str(setup.get("grade") or "WAIT"),
            "setup_type": str(setup.get("setup_type") or ""),
            "strategy_family": str(setup.get("strategy_family") or "none"),
            "entry_policy": str(setup.get("entry_policy") or ""),
            "structural_rr": structural_rr,
            "reasons": rejection_reasons,
        },
        "ai_review": {
            "status": ai_status,
            "verdict": ai_verdict,
            "review_stage": ai.get("review_stage"),
            "stage_updated_at": ai.get("stage_updated_at"),
            "reason": ai.get("reason"),
            "failure_type": ai.get("failure_type"),
            "conflicts": list(ai.get("conflicts") or []),
            "input_snapshot_id": ai.get("input_snapshot_id"),
            "snapshot_matches": ai_snapshot_matches,
            "required": ai_required,
            "fast_tracked": bool(ai.get("fast_tracked", False)),
            "soft_caution": ai_verdict.upper() == "CAUTION",
        },
        "systemic_risk": {
            "status": str(systemic_risk.get("status") or "NOT_APPLICABLE"),
            "execution_allowed": systemic_allowed,
            "reason_code": str(systemic_risk.get("reason_code") or ""),
            "reason": systemic_risk.get("reason"),
            "btc_snapshot_id": systemic_risk.get("btc_snapshot_id"),
            "btc_1h_pct_change": systemic_risk.get("btc_1h_pct_change"),
            "btc_regime": systemic_risk.get("btc_regime"),
            "btc_delta_ratio": systemic_risk.get("btc_delta_ratio"),
            "valid_until": systemic_risk.get("valid_until"),
        },
        "execution": {
            "status": exec_status,
            "intent": str(execution.get("intent") or (
                "wait" if exec_status == "waiting_retest"
                else "place_limit" if exec_status == "limit_armed"
                else "market_now" if exec_status == "validated"
                else "cancel"
            )),
            "reason_code": str(execution.get("reason_code") or ""),
            "reasons": list(execution.get("reasons") or []),
            "structural_rr": execution.get("structural_rr", structural_rr),
            "executable_rr": execution.get("executable_rr"),
            "minimum_executable_rr": execution.get("minimum_executable_rr"),
            "quote_age_seconds": execution.get("quote_age_seconds"),
            "quote_freshness": _quote_freshness(execution),
            "spread_pct": execution.get("spread_pct"),
            "order_type": execution.get("order_type"),
            "entry_policy": execution.get("entry_policy"),
            "auto_armed_limit": bool(execution.get("auto_armed_limit", False)),
            "limit_zone_id": execution.get("limit_zone_id"),
            "original_entry": execution.get("original_entry"),
            "executable_entry": execution.get("executable_entry"),
        },
        "risk": {
            "status": effective_risk_status,
            "reason": effective_risk_reason,
            "risk_pct": risk.get("risk_pct"),
            "risk_amount": risk.get("risk_amount"),
            "position_size": risk.get("position_size"),
            "stop_distance_pct": risk.get("stop_distance_pct"),
            "max_stop_distance_pct": risk.get("max_stop_distance_pct"),
            "atr_pct": risk.get("atr_pct"),
            "stop_distance_atr": risk.get("stop_distance_atr"),
            "stop_policy": risk.get("stop_policy"),
            "sizing_authority": risk.get("sizing_authority"),
            "risk_guard": (
                "btc_systemic_anchor"
                if actionable and not systemic_allowed
                else risk.get("risk_guard")
            ),
        },
        "oms": {
            "status": str(oms.get("status") or "not_submitted"),
            "order_type": oms.get("order_type"),
            "fill_price": oms.get("fill_price"),
            "filled_quantity": oms.get("filled_quantity"),
            "close_reason": oms.get("close_reason"),
        },
        "order": {
            "status": (
                oms_status
                if order_already_submitted and oms_status in {
                    "pending",
                    "open",
                    "partial",
                    "partially_filled",
                    "closed",
                    "cancelled",
                    "canceled",
                }
                else "submitted" if order_already_submitted
                else "waiting_retest" if exec_status == "waiting_retest"
                else "ready" if order_ready else "not_ready"
            ),
            "ready": order_ready,
            "manual_override_required": (
                actionable and not order_ready and not order_already_submitted and exec_status != "waiting_retest"
            ),
        },
    }
