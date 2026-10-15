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
        return "not_run"
    intent = str(facts.get("intent") or "").lower()
    reason_code = str(facts.get("reason_code") or "").upper()
    if intent == "wait" or reason_code in {"MARKET_EXTENSION_EXCEEDED", "EXECUTABLE_RR_TOO_LOW"}:
        return "waiting_retest"
    if intent == "place_limit" or facts.get("auto_armed_limit") is True:
        return "limit_armed" if facts.get("passed") is True else "rejected"
    return "validated" if facts.get("passed") is True else "rejected"


def _risk_status(facts: dict[str, Any], actionable: bool = False) -> str:
    if not facts:
        return "not_run"
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
    ai_review: dict[str, Any] | None = None,
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
    ai_event = _latest(events, "AI_REVIEW") or {}
    ai = {**(dict(ai_review) if ai_review else {}), **ai_event}
    if ai_review:
        for k in ("setup_evaluation", "market_context", "execution_advice", "independent_market_read"):
            if k in ai_review and k not in ai_event:
                ai[k] = ai_review[k]
    systemic_risk = _latest(events, "SYSTEMIC_RISK")
    execution = _latest(events, "EXECUTION_REVALIDATION")
    risk = _latest(events, "RISK")
    oms = _latest(events, "OMS_STATE") or _latest(events, "OMS_RESULT")
    shadow_comparison = _latest(events, "SHADOW_COMPARISON")

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
    ai_required = bool(runtime.get("auto_trade_require_ai_approval", risk_cfg.get("auto_trade_require_ai_approval", True)))
    accept_caution = bool(runtime.get("auto_trade_accept_caution", risk_cfg.get("auto_trade_accept_caution", False)))
    ai_policy = str(runtime.get("auto_trade_ai_policy", "strict")).lower()
    if ai_policy not in {"strict", "guarded", "advisory"}:
        ai_policy = "strict"

    actionable = setup.get("actionable") is True

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
    ai_caution = (
        ai_status.lower() == "reviewed"
        and ai_verdict.upper() == "CAUTION"
    )
    ai_timing = str((ai.get("execution_advice") or {}).get("suggested_timing") or "").upper()
    ai_mult = float(ai.get("suggested_risk_multiplier", 1.0) if ai.get("suggested_risk_multiplier") is not None else 1.0)
    ai_decision = str(ai.get("decision") or "").upper()

    ai_passed = bool(
        (ai_coherent or (ai_caution and (accept_caution or ai_policy == "guarded")) or ai_policy == "advisory" or not ai_required)
        and (ai_verdict.upper() not in {"CONFLICT", "UNAVAILABLE"})
        and (ai_decision != "PASS" if ai_status.lower() == "reviewed" else True)
        and (ai_timing != "STAND_ASIDE")
        and (ai_mult > 0.0 or not ai_required)
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
        and ai_passed
        and execution_validated
        and risk_approved
        and not order_already_submitted
    )

    rejection_reasons = list(setup.get("rejection_reasons") or [])

    # Keep one canonical explanation for the UI and operators.  The stage
    # payloads below remain available for detail, but callers should not have
    # to infer the first blocking gate themselves.
    if order_already_submitted:
        final_decision = "SUBMITTED"
        final_block_stage = "OMS"
        final_block_reason_code = f"OMS_{oms_status.upper()}"
        final_block_reason = oms.get("close_reason") or f"OMS status is {oms_status}"
    elif not actionable:
        final_decision = "WAIT"
        final_block_stage = "TRI_CORE"
        final_block_reason_code = (
            str(rejection_reasons[0]) if rejection_reasons else "NO_SETUP"
        )
        final_block_reason = "; ".join(str(r) for r in rejection_reasons) or "No actionable setup"
    elif not systemic_allowed:
        final_decision = "BLOCKED"
        final_block_stage = "SYSTEMIC_RISK"
        final_block_reason_code = str(systemic_risk.get("reason_code") or "SYSTEMIC_RISK_BLOCKED")
        final_block_reason = str(systemic_risk.get("reason") or "Systemic market risk paused execution")
    elif not ai_passed:
        final_decision = "BLOCKED"
        final_block_stage = "AI_REVIEW"
        final_block_reason_code = (
            f"AI_{ai_verdict.upper()}" if ai_verdict else "AI_REVIEW_REQUIRED"
        )
        final_block_reason = str(ai.get("reason") or f"AI verdict is {ai_verdict}")
    elif not execution_validated:
        final_decision = "BLOCKED"
        final_block_stage = "EXECUTION_REVALIDATION"
        final_block_reason_code = str(execution.get("reason_code") or "EXECUTION_REJECTED")
        final_block_reason = "; ".join(str(r) for r in execution.get("reasons", [])) or f"Execution intent: {exec_status}"
    elif not risk_approved:
        final_decision = "BLOCKED"
        final_block_stage = "RISK"
        final_block_reason_code = str(effective_risk_reason or "RISK_REJECTED")
        final_block_reason = str(effective_risk_reason or "Risk limits exceeded")
    else:
        final_decision = "READY"
        final_block_stage = None
        final_block_reason_code = "READY_FOR_OMS"
        final_block_reason = "All pre-trade gates passed"
    if order_ready and lifecycle_state in {"UNKNOWN", "REGISTERED", "AI_COHERENT", "AI_REVIEW_PENDING"}:
        lifecycle_state = "RISK_APPROVED"
        lifecycle_reason_code = "PIPELINE_VALIDATED"
        lifecycle_reason = "Pre-trade execution and risk checks approved"
    elif ai_status.lower() == "reviewed" and ai_verdict.upper() in {"CONFLICT", "CAUTION"}:
        if lifecycle_state in {"UNKNOWN", "REGISTERED", "WAIT", "WAITING_SETUP", "DETECTED", "INVALIDATED"}:
            lifecycle_state = f"AI_{ai_verdict.upper()}"
            lifecycle_reason_code = f"AI_{ai_verdict.upper()}"
            lifecycle_reason = f"AI marked setup as {ai_verdict.upper()}"
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
            "decision": ai.get("decision", "PASS"),
            "review_stage": ai.get("review_stage"),
            "stage_updated_at": ai.get("stage_updated_at"),
            "reason": ai.get("reason"),
            "failure_type": ai.get("failure_type"),
            "conflicts": list(ai.get("conflicts") or []),
            "input_snapshot_id": ai.get("input_snapshot_id"),
            "snapshot_matches": ai_snapshot_matches,
            "required": ai_required,
            "policy": ai_policy,
            "fast_tracked": bool(ai.get("fast_tracked", False)),
            "soft_caution": ai_verdict.upper() == "CAUTION",
            "risk_rating": ai.get("risk_rating", "MODERATE" if ai_verdict.upper() == "CAUTION" else "LOW" if ai_verdict.upper() == "COHERENT" else "HIGH"),
            "risk_score": ai.get("risk_score", 50 if ai_verdict.upper() == "CAUTION" else 20 if ai_verdict.upper() == "COHERENT" else 85),
            "suggested_risk_multiplier": ai.get("suggested_risk_multiplier", 0.5 if ai_verdict.upper() == "CAUTION" else 1.0 if ai_verdict.upper() == "COHERENT" else 0.0),
            "setup_evaluation": ai.get("setup_evaluation", {}),
            "market_context": ai.get("market_context", {}),
            "execution_advice": ai.get("execution_advice", {}),
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
            "shadow_comparison": shadow_comparison,
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
        "decision_id": f"{snapshot_id}:{event_id}",
        "decision_summary": {
            "final_decision": final_decision,
            "blocking_stage": final_block_stage,
            "reason_code": final_block_reason_code,
            "reason": final_block_reason,
        },
        "stages": {
            "tri_core": {
                "stage": "TRI_CORE",
                "passed": actionable,
                "reason_code": "SETUP_ACTIONABLE" if actionable else (setup.get("rejection_reasons", ["NO_SETUP"])[0] if setup.get("rejection_reasons") else "NO_SETUP"),
                "reason": f"Grade {setup.get('grade', 'WAIT')} {setup.get('direction', 'wait')} setup" if actionable else "; ".join(str(r) for r in rejection_reasons) or "No actionable setup",
                "grade": str(setup.get("grade") or "WAIT"),
                "direction": str(setup.get("direction") or "wait"),
            },
            "systemic_risk": {
                "stage": "SYSTEMIC_RISK",
                "passed": systemic_allowed,
                "reason_code": str(systemic_risk.get("reason_code") or ("ALLOWED" if systemic_allowed else "BLOCKED")),
                "reason": systemic_risk.get("reason") or ("Systemic risk passed" if systemic_allowed else "Systemic risk paused execution"),
            },
            "ai_review": {
                "stage": "AI_REVIEW",
                "passed": ai_passed,
                "required": ai_required,
                "policy": ai_policy,
                "reason_code": (
                    f"AI_{ai_verdict}"
                    if ai_status.lower() == "reviewed"
                    else (
                        "AI_REQUIRED"
                        if ai_required and ai_status.lower() == "not_requested"
                        else (
                            f"AI_{ai_status.upper()}"
                            if ai_status.lower() not in {"not_requested", "none"}
                            else "AI_NOT_REQUESTED"
                        )
                    )
                ),
                "reason": ai.get("reason") or f"AI verdict is {ai_verdict}",
                "verdict": ai_verdict,
                "decision": ai.get("decision", "PASS"),
                "status": ai_status,
                "risk_rating": ai.get("risk_rating", "MODERATE" if ai_verdict.upper() == "CAUTION" else "LOW" if ai_verdict.upper() == "COHERENT" else "HIGH"),
                "risk_score": ai.get("risk_score", 50 if ai_verdict.upper() == "CAUTION" else 20 if ai_verdict.upper() == "COHERENT" else 85),
                "suggested_risk_multiplier": ai.get("suggested_risk_multiplier", 0.5 if ai_verdict.upper() == "CAUTION" else 1.0 if ai_verdict.upper() == "COHERENT" else 0.0),
            },
            "execution": {
                "stage": "EXECUTION_REVALIDATION",
                "passed": execution_validated,
                "reason_code": str(execution.get("reason_code") or ("VALIDATED" if execution_validated else "REJECTED")),
                "reason": "; ".join(execution.get("reasons", [])) or f"Execution intent: {exec_status}",
                "status": exec_status,
            },
            "risk": {
                "stage": "RISK",
                "passed": risk_approved,
                "reason_code": "RISK_APPROVED" if risk_approved else str(effective_risk_reason or "RISK_REJECTED"),
                "reason": effective_risk_reason or ("Risk approved" if risk_approved else "Risk limits exceeded"),
                "status": effective_risk_status,
            },
            "oms": {
                "stage": "OMS",
                "passed": order_already_submitted,
                "status": oms_status,
                "reason_code": f"OMS_{oms_status.upper()}",
                "reason": oms.get("close_reason") or f"OMS status is {oms_status}",
            },
        },
        "final_decision": final_decision,
        "final_block_stage": final_block_stage,
        "final_block_reason_code": final_block_reason_code,
        "final_block_reason": final_block_reason,
    }


async def get_authority_status_for_symbol(
    symbol: str,
    exchange: str | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    """Retrieve full authority pipeline status and stage breakdown for a specific symbol."""
    from app.services.event_trigger import MarketMonitor, _compact_symbol
    monitor = MarketMonitor.get_instance()
    norm_sym = _compact_symbol(symbol)
    
    # Check if there is an active signal in recent_signals
    # Check if there is an active signal in recent_signals with matching criteria
    matching_sigs = []
    for s in monitor.recent_signals:
        if _compact_symbol(s.get("symbol", "")) != norm_sym:
            continue
        if exchange and str(s.get("exchange", "")).lower() != exchange.lower():
            continue
        if timeframe and str(s.get("timeframe", "")).lower() != timeframe.lower():
            continue
        matching_sigs.append(s)

    matching_sig = None
    if matching_sigs:
        matching_sigs.sort(
            key=lambda x: str(x.get("timestamp") or (x.get("analysis_snapshot") or {}).get("generated_at") or ""),
            reverse=True,
        )
        matching_sig = matching_sigs[0]
            
    if matching_sig:
        analysis_snapshot = matching_sig.get("analysis_snapshot") or {}
        tri_core = matching_sig.get("tri_core_setup") or {}
        snapshot_id = str(analysis_snapshot.get("snapshot_id") or "")
        event_id = str(tri_core.get("event_id") or "")
        effective_snapshot_id = snapshot_id or f"snap-{symbol}"
        effective_event_id = event_id or f"wait-{symbol}"
        return await build_authority_status(
            symbol=symbol,
            snapshot_id=effective_snapshot_id,
            event_id=effective_event_id,
            tri_core_setup=tri_core,
            structural_rr=matching_sig.get("risk_reward"),
            ai_review=matching_sig.get("ai_review"),
        )

    # Fallback to execution_analyses cache (filter by symbol, exchange, timeframe and pick newest)
    from app.services.execution_analysis import execution_analyses
    matching_candidates = []
    for an in list(execution_analyses._cache.values()):
        if _compact_symbol(an.symbol) != norm_sym:
            continue
        if exchange and str(getattr(an, "exchange", "")).lower() != exchange.lower():
            continue
        if timeframe and str(getattr(an, "timeframe", "")).lower() != timeframe.lower():
            continue
        matching_candidates.append(an)

    # Sort matching candidates by created_at / generated_at descending if available
    cached = None
    if matching_candidates:
        matching_candidates.sort(
            key=lambda x: str(getattr(x, "generated_at", "") or getattr(x, "created_at", "") or ""),
            reverse=True,
        )
        cached = matching_candidates[0]

    if cached:
        tri_core = getattr(cached.signal, "tri_core_setup", {}) or {}
        ai_rev = getattr(cached.signal, "ai_review", {}) or {}
        return await build_authority_status(
            symbol=symbol,
            snapshot_id=cached.snapshot_id,
            event_id=str(tri_core.get("event_id") or f"wait-{symbol}"),
            tri_core_setup=tri_core,
            structural_rr=getattr(cached.signal, "risk_reward", None),
            ai_review=ai_rev,
        )

    # Default/Empty projection
    return await build_authority_status(
        symbol=symbol,
        snapshot_id=f"snap-{symbol}",
        event_id=f"wait-{symbol}",
        tri_core_setup={
            "actionable": False,
            "grade": "WAIT",
            "direction": "wait",
            "reasons": ["No active setup detected for symbol"],
            "rejection_reasons": ["No active setup detected for symbol"],
        },
    )


