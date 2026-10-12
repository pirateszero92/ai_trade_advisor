import pytest

from app.services.candidate_lifecycle import CandidateLifecycleService, CandidateState
from app.services.observability import (
    ObservabilityRegistry,
    observability,
    risk_rejection_category,
)


def test_phase22_registry_renders_prometheus_counter_gauge_histogram():
    registry = ObservabilityRegistry()
    registry.inc("ai_trade_test_events", outcome="ok")
    registry.set_gauge("ai_trade_test_active", 3)
    registry.observe("ai_trade_test_latency_seconds", 0.2, provider="local")

    rendered = registry.render_prometheus()

    assert 'ai_trade_test_events_total{outcome="ok"} 1' in rendered
    assert "ai_trade_test_active 3" in rendered
    assert 'ai_trade_test_latency_seconds_count{provider="local"} 1' in rendered
    assert 'ai_trade_test_latency_seconds_sum{provider="local"} 0.2' in rendered
    assert 'ai_trade_test_latency_seconds_bucket{le="+Inf",provider="local"} 1' in rendered


def test_phase22_risk_rejection_categories_are_low_cardinality():
    assert risk_rejection_category("Daily loss limit reached: -3.2%") == "daily_loss"
    assert risk_rejection_category("Maximum open positions reached") == "max_positions"
    assert risk_rejection_category("Executable R/R below minimum") == "executable_rr"
    assert risk_rejection_category("Execution cost consumes too much risk") == "execution_cost"
    assert risk_rejection_category("Cluster concentration limit") == "concentration"
    assert risk_rejection_category("some dynamic message 123.45") == "other"
@pytest.mark.anyio
async def test_phase22_candidate_lifecycle_emits_state_transition_metrics():
    observability.reset()
    service = CandidateLifecycleService()

    await service.register(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        ready=True,
    )
    await service.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        new_state=CandidateState.AI_REVIEW_PENDING,
        reason_code="AI_REVIEW_QUEUED",
    )

    snapshot = observability.snapshot()["counters"]

    assert snapshot['ai_trade_candidate_state_entries{state="DETECTED"}'] == 1
    assert snapshot['ai_trade_candidate_state_entries{state="TRI_CORE_READY"}'] == 1
    assert snapshot['ai_trade_candidate_state_entries{state="AI_REVIEW_PENDING"}'] == 1
    key = (
        'ai_trade_candidate_transitions'
        '{from_state="TRI_CORE_READY",reason_code="AI_REVIEW_QUEUED",'
        'to_state="AI_REVIEW_PENDING"}'
    )
    assert snapshot[key] == 1


@pytest.mark.anyio
async def test_phase22_metrics_endpoint_returns_prometheus_text():
    from app.main import create_app

    observability.reset()
    observability.inc("ai_trade_ai_reviews", outcome="coherent", reason="none")
    app = create_app()
    route = next(route for route in app.routes if getattr(route, "path", None) == "/metrics")
    response = await route.endpoint()

    body = response.body.decode("utf-8")
    assert response.media_type.startswith("text/plain")
    assert 'ai_trade_ai_reviews_total{outcome="coherent",reason="none"} 1' in body


def test_phase22_registry_ignores_invalid_numeric_observations():
    registry = ObservabilityRegistry()

    registry.inc("ai_trade_invalid", value=-1)
    registry.set_gauge("ai_trade_gauge", float("nan"))
    registry.observe("ai_trade_hist", float("inf"))

    snapshot = registry.snapshot()
    assert snapshot["counters"] == {}
    assert snapshot["gauges"] == {}
    assert snapshot["histograms"] == {}
