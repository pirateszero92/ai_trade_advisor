from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd

from app.services.execution_revalidator import ExecutionIntent, ExecutionRevalidator


class Rules:
    def price(self, value, *, upward=False):
        return round(float(value), 2)


def _analysis(*, entry_policy="market_eligible", order_type="market", mitigated=False,
              causal_market_zone=False):
    now = datetime.now(timezone.utc)
    frame = pd.DataFrame(
        {
            "open": [99.0 + i * 0.05 for i in range(20)],
            "high": [100.0 + i * 0.05 for i in range(20)],
            "low": [98.0 + i * 0.05 for i in range(20)],
            "close": [99.5 + i * 0.05 for i in range(20)],
            "volume": [1000.0] * 20,
        },
        index=pd.date_range(end=now, periods=20, freq="15min"),
    )
    zone = SimpleNamespace(zone_id="zone-1", mitigated=mitigated)
    signal = SimpleNamespace(
        entry=100.0,
        stop_loss=98.0,
        take_profit=106.0,
        risk_reward=3.0,
        tri_core_setup={
            "actionable": True,
            "direction": "long",
            "event_id": "event-1",
            "entry_policy": entry_policy,
            "order_type": order_type,
            "limit_zone_id": "zone-1" if (
                entry_policy == "limit_retest_only" or causal_market_zone
            ) else "",
            "risk_reward": 3.0,
        },
        order_blocks=[zone],
        fvgs=[],
        breaker_blocks=[],
    )
    return SimpleNamespace(
        snapshot_id="snap-1",
        valid_until=now + timedelta(minutes=10),
        signal=signal,
        frame=frame,
        config_snapshot={
            "tri_core_policy": {
                "market_entry_max_extension_atr": 0.25,
                "no_chase_max_extension_atr": 0.60,
            }
        },
    )


def _quote(*, bid=100.00, ask=100.02, age_seconds=0.0):
    ts = datetime.now(timezone.utc).timestamp() - age_seconds
    return {
        "bid": bid,
        "ask": ask,
        "timestamp": ts,
        "received_timestamp": ts,
    }


def test_execution_revalidator_passes_fresh_market_candidate():
    result = ExecutionRevalidator().validate(
        analysis=_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(),
        direction="long",
        minimum_executable_rr=2.0,
        instrument_rules=Rules(),
        fee_bps=1.0,
        slippage_bps=1.0,
    )

    assert result.passed is True
    assert result.reason_code == "PASSED"
    assert result.executable_entry == 100.02
    assert result.structural_rr == 3.0
    assert result.executable_rr >= 2.0


def test_execution_revalidator_rejects_expired_snapshot():
    analysis = _analysis()
    analysis.valid_until = datetime.now(timezone.utc) - timedelta(seconds=1)

    result = ExecutionRevalidator().validate(
        analysis=analysis,
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(),
        direction="long",
        minimum_executable_rr=2.0,
    )

    assert result.passed is False
    assert result.reason_code == "SNAPSHOT_EXPIRED"


def test_execution_revalidator_rejects_replaced_event():
    result = ExecutionRevalidator().validate(
        analysis=_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="old-event",
        quote=_quote(),
        direction="long",
        minimum_executable_rr=2.0,
    )

    assert result.passed is False
    assert result.reason_code == "EVENT_REPLACED"


def test_execution_revalidator_rejects_stale_quote():
    result = ExecutionRevalidator().validate(
        analysis=_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(age_seconds=30),
        direction="long",
        minimum_executable_rr=2.0,
    )

    assert result.passed is False
    assert result.reason_code == "QUOTE_STALE"


def test_execution_revalidator_enforces_no_chase():
    result = ExecutionRevalidator().validate(
        analysis=_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(bid=104.9, ask=105.0),
        direction="long",
        minimum_executable_rr=1.0,
    )

    assert result.passed is False
    assert result.reason_code == "NO_CHASE"


def test_execution_revalidator_auto_arms_causal_limit_when_market_is_extended():
    result = ExecutionRevalidator().validate(
        analysis=_analysis(causal_market_zone=True),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(bid=104.9, ask=105.0),
        direction="long",
        minimum_executable_rr=1.0,
    )

    assert result.passed is True
    assert result.reason_code == "PASSED"
    assert result.auto_armed_limit is True
    assert result.entry_policy == "limit_retest_only"
    assert result.order_type == "limit"
    assert result.executable_entry == 100.0
    assert result.limit_zone_id == "zone-1"


def test_execution_revalidator_does_not_treat_better_quote_as_adverse_extension():
    result = ExecutionRevalidator().validate(
        analysis=_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(bid=94.9, ask=95.0),
        direction="long",
        minimum_executable_rr=1.0,
    )

    assert result.auto_armed_limit is False
    assert result.extension_atr == 0.0


def test_execution_revalidator_rejects_low_executable_rr():
    result = ExecutionRevalidator().validate(
        analysis=_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(bid=100.0, ask=100.02),
        direction="long",
        minimum_executable_rr=5.0,
        fee_bps=2.0,
        slippage_bps=2.0,
    )

    assert result.passed is False
    assert result.reason_code == "EXECUTABLE_RR_TOO_LOW"


def test_execution_revalidator_requires_active_causal_limit_zone():
    analysis = _analysis(
        entry_policy="limit_retest_only",
        order_type="limit",
        mitigated=True,
    )
    result = ExecutionRevalidator().validate(
        analysis=analysis,
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(),
        direction="long",
        minimum_executable_rr=2.0,
    )

    assert result.passed is False
    assert result.reason_code == "LIMIT_ZONE_INVALID"


def test_hrd05_execution_revalidator_allows_small_future_clock_skew():
    quote = _quote()
    future_ts = datetime.now(timezone.utc).timestamp() + 0.5
    quote["timestamp"] = future_ts
    quote["received_timestamp"] = future_ts

    result = ExecutionRevalidator().validate(
        analysis=_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=quote,
        direction="long",
        minimum_executable_rr=2.0,
        instrument_rules=Rules(),
    )

    assert result.passed is True
    assert result.reason_code == "PASSED"
    assert result.quote_age_seconds == 0.0


def test_hrd05_execution_revalidator_rejects_materially_future_quote():
    quote = _quote()
    future_ts = datetime.now(timezone.utc).timestamp() + 5.0
    quote["timestamp"] = future_ts
    quote["received_timestamp"] = future_ts

    result = ExecutionRevalidator().validate(
        analysis=_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=quote,
        direction="long",
        minimum_executable_rr=2.0,
    )

    assert result.passed is False
    assert result.reason_code == "QUOTE_TIMESTAMP_FUTURE"
    assert "in the future" in result.reasons[0]


def _short_analysis():
    analysis = _analysis()
    analysis.signal.entry = 100.0
    analysis.signal.stop_loss = 102.0
    analysis.signal.take_profit = 94.0
    analysis.signal.risk_reward = 3.0
    analysis.signal.tri_core_setup.update({
        "direction": "short",
        "risk_reward": 3.0,
    })
    return analysis


def test_hrd03_execution_revalidator_preserves_short_geometry():
    result = ExecutionRevalidator().validate(
        analysis=_short_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(bid=99.98, ask=100.00),
        direction="short",
        minimum_executable_rr=2.0,
        instrument_rules=Rules(),
    )

    assert result.passed is True
    assert result.reason_code == "PASSED"
    assert result.executable_entry == 99.98
    assert result.take_profit < result.executable_entry < result.stop_loss


def test_hrd03_execution_revalidator_enforces_no_chase_for_short():
    result = ExecutionRevalidator().validate(
        analysis=_short_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(bid=95.0, ask=95.02),
        direction="short",
        minimum_executable_rr=1.0,
    )

    assert result.passed is False
    assert result.reason_code == "NO_CHASE"


def test_low_market_rr_auto_arms_active_causal_limit_for_long():
    result = ExecutionRevalidator().validate(
        analysis=_analysis(causal_market_zone=True),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(bid=100.38, ask=100.40),
        direction="long",
        minimum_executable_rr=2.8,
        instrument_rules=Rules(),
    )

    assert result.passed is True
    assert result.reason_code == "PASSED"
    assert result.auto_armed_limit is True
    assert result.entry_policy == "limit_retest_only"
    assert result.order_type == "limit"
    assert result.executable_entry == 100.0
    assert result.executable_rr >= 2.8
    assert any("Market R:R" in reason for reason in result.reasons)


def test_low_market_rr_without_causal_zone_remains_rejected():
    result = ExecutionRevalidator().validate(
        analysis=_analysis(causal_market_zone=False),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(bid=100.38, ask=100.40),
        direction="long",
        minimum_executable_rr=2.8,
        instrument_rules=Rules(),
    )

    assert result.passed is False
    assert result.reason_code == "EXECUTABLE_RR_TOO_LOW"
    assert result.auto_armed_limit is False


def test_low_market_rr_auto_arms_active_causal_limit_for_short():
    analysis = _analysis(causal_market_zone=True)
    analysis.signal.entry = 100.0
    analysis.signal.stop_loss = 102.0
    analysis.signal.take_profit = 94.0
    analysis.signal.risk_reward = 3.0
    analysis.signal.tri_core_setup.update({
        "direction": "short",
        "risk_reward": 3.0,
    })

    result = ExecutionRevalidator().validate(
        analysis=analysis,
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(bid=99.60, ask=99.62),
        direction="short",
        minimum_executable_rr=2.8,
        instrument_rules=Rules(),
    )

    assert result.passed is True
    assert result.reason_code == "PASSED"
    assert result.intent == ExecutionIntent.PLACE_LIMIT
    assert result.auto_armed_limit is True
    assert result.entry_policy == "limit_retest_only"
    assert result.order_type == "limit"
    assert result.executable_entry == 100.0
    assert result.executable_rr >= 2.8


def test_execution_intents_are_classified_correctly():
    rev = ExecutionRevalidator()

    # 1. Market Now intent
    res_market = rev.validate(
        analysis=_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(bid=100.01, ask=100.02),
        direction="long",
        minimum_executable_rr=2.0,
        instrument_rules=Rules(),
    )
    assert res_market.passed is True
    assert res_market.intent == ExecutionIntent.MARKET_NOW

    # 2. Wait intent on low instantaneous R:R (unmitigated setup waiting for pullback)
    res_wait_rr = rev.validate(
        analysis=_analysis(causal_market_zone=False),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(bid=100.38, ask=100.40),
        direction="long",
        minimum_executable_rr=2.8,
        instrument_rules=Rules(),
    )
    assert res_wait_rr.passed is False
    assert res_wait_rr.intent == ExecutionIntent.WAIT
    assert res_wait_rr.reason_code == "EXECUTABLE_RR_TOO_LOW"

    # 3. Cancel intent on hard invalidation (geometry broken)
    bad_geom = _analysis()
    bad_geom.signal.entry = 100.0
    bad_geom.signal.stop_loss = 105.0  # Invalid LONG: stop above entry
    bad_geom.signal.take_profit = 110.0
    res_cancel = rev.validate(
        analysis=bad_geom,
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(bid=100.01, ask=100.02),
        direction="long",
        minimum_executable_rr=2.0,
        instrument_rules=Rules(),
    )
    assert res_cancel.passed is False
    assert res_cancel.intent == ExecutionIntent.CANCEL
    assert res_cancel.reason_code == "GEOMETRY_INVALID"

    # 4. Transient data-quality issues yield WAIT intent (never kill candidate permanently)
    stale_quote = _quote(bid=100.01, ask=100.02)
    stale_quote["received_timestamp"] = 1.0  # Very old quote -> QUOTE_STALE
    res_stale = rev.validate(
        analysis=_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=stale_quote,
        direction="long",
        minimum_executable_rr=2.0,
        instrument_rules=Rules(),
    )
    assert res_stale.passed is False
    assert res_stale.intent == ExecutionIntent.WAIT
    assert res_stale.reason_code == "QUOTE_STALE"

    wide_quote = _quote(bid=100.0, ask=105.0)  # 5% spread -> SPREAD_TOO_WIDE
    res_wide = rev.validate(
        analysis=_analysis(),
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=wide_quote,
        direction="long",
        minimum_executable_rr=2.0,
        instrument_rules=Rules(),
    )
    assert res_wide.passed is False
    assert res_wide.intent == ExecutionIntent.WAIT
    assert res_wide.reason_code == "SPREAD_TOO_WIDE"

