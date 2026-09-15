from copy import deepcopy

import pandas as pd
import pytest

from app.engines.indicator_core import DEFAULT_INDICATOR_CORE
from app.engines.smc_engine import SMCSignal, Zone
from app.engines.strategy_engine import DEFAULT_STRATEGY
from app.engines.timeframe_profiles import (
    DEFAULT_TIMEFRAME_PROFILES,
    validate_timeframe_profiles,
)
from app.services.mtf_analysis import (
    MTFAnalysisService,
    analyze_mtf_frames,
    mtf_snapshot_id,
)
from app.services.evidence import build_decision_evidence, replay_decision_payload


def _frame(periods: int, timeframe: str) -> pd.DataFrame:
    end = pd.Timestamp.now(tz="UTC").floor(timeframe) - pd.Timedelta(timeframe)
    index = pd.date_range(end=end, periods=periods, freq=timeframe)
    values = list(range(periods))
    return pd.DataFrame(
        {
            "open": [100 + value * 0.01 for value in values],
            "high": [101 + value * 0.01 for value in values],
            "low": [99 + value * 0.01 for value in values],
            "close": [100.5 + value * 0.01 for value in values],
            "volume": [1000 + value for value in values],
        },
        index=index,
    )


def _config() -> dict:
    config = deepcopy(DEFAULT_STRATEGY)
    config["version"] = "phase5-test"
    config["filters"].update(
        {
            "min_confluence": 65,
            "require_indicator_readiness": True,
            "min_rr": 2.0,
        }
    )
    config["indicator_core"] = deepcopy(DEFAULT_INDICATOR_CORE)
    config["timeframe_profiles"] = deepcopy(DEFAULT_TIMEFRAME_PROFILES)
    config["risk_parameters"] = {"mtf_hierarchy_required": True}
    return config


def _signal(timeframe: str, direction: str, *, trigger: bool = False) -> SMCSignal:
    bullish = direction == "long"
    signal = SMCSignal(symbol="BTC/USDT", timeframe=timeframe)
    signal.current_price = 100.0
    signal.bias = "bullish" if bullish else "bearish"
    signal.direction = direction
    signal.htf_bias = signal.bias
    signal.order_block = Zone(
        "ob",
        signal.bias,
        top=100.0 if bullish else 102.0,
        bottom=98.0 if bullish else 100.0,
    )
    signal.fvg = Zone(
        "fvg",
        signal.bias,
        top=101.0 if bullish else 100.0,
        bottom=99.0 if bullish else 98.0,
    )
    signal.in_discount = bullish
    signal.in_premium = not bullish
    signal.bos = trigger
    signal.entry = 99.0 if bullish else 101.0
    signal.stop_loss = 97.0 if bullish else 103.0
    signal.take_profit = 104.0 if bullish else 96.0
    signal.risk_reward = 2.5
    signal.volume_quality = "exchange_aggressor"
    signal.tri_core_setup = {
        "actionable": True,
        "direction": direction,
        "grade": "A",
        "smc_confirmed": True,
        "flow_confirmed": True,
        "cvd_confirmed": True,
    }
    signal.indicator_decision = {
        "ready": True,
        "coverage": 100.0,
        "score": 80,
        "layers": [],
    }
    signal.confluence = 80
    signal.scenario = {"scenario_id": "TEST_CONFIRMED", "actionable": True}
    return signal


@pytest.mark.parametrize("direction", ["long", "short"])
def test_mtf_hierarchy_reaches_ready_for_long_and_short(monkeypatch, direction):
    signals = {
        "4h": _signal("4h", direction),
        "1h": _signal("1h", direction),
        "15m": _signal("15m", direction, trigger=True),
    }

    def fake_analyze(_self, _df, _symbol, timeframe, *_args, **_kwargs):
        return deepcopy(signals[timeframe])

    monkeypatch.setattr("app.services.mtf_analysis.SMCEngine.analyze", fake_analyze)
    result = analyze_mtf_frames(
        frames={
            "bias": _frame(180, "4h"),
            "setup": _frame(300, "1h"),
            "trigger": _frame(300, "15min"),
        },
        symbol="BTC/USDT",
        entry_mode="limit",
        config_snapshot=_config(),
    )

    assert result.status == "ready"
    assert result.actionable is True
    assert result.direction == direction
    assert result.strategy.direction == direction
    assert all(stage.status == "ready" for stage in result.stages.values())


def test_opposite_one_hour_structure_never_blocks_trigger(monkeypatch):
    signals = {
        "4h": _signal("4h", "long"),
        "1h": _signal("1h", "short"),
        "15m": _signal("15m", "long", trigger=True),
    }

    def fake_analyze(_self, _df, _symbol, timeframe, *_args, **_kwargs):
        return deepcopy(signals[timeframe])

    monkeypatch.setattr("app.services.mtf_analysis.SMCEngine.analyze", fake_analyze)
    result = analyze_mtf_frames(
        frames={
            "bias": _frame(180, "4h"),
            "setup": _frame(300, "1h"),
            "trigger": _frame(300, "15min"),
        },
        symbol="BTC/USDT",
        entry_mode="limit",
        config_snapshot=_config(),
    )

    assert result.status == "ready"
    assert result.actionable is True
    assert result.stages["setup"].status == "blocked"
    assert result.strategy.direction == "long"
    assert result.decision_dict()["decision_influence"] == "none"
    assert any("against 4H" in reason for reason in result.stages["setup"].reasons)


def test_strategy_approval_does_not_depend_on_mtf_stage_event(monkeypatch):
    signals = {
        "4h": _signal("4h", "long"),
        "1h": _signal("1h", "long"),
        "15m": _signal("15m", "long", trigger=False),
    }

    def fake_analyze(_self, _df, _symbol, timeframe, *_args, **_kwargs):
        return deepcopy(signals[timeframe])

    monkeypatch.setattr("app.services.mtf_analysis.SMCEngine.analyze", fake_analyze)
    result = analyze_mtf_frames(
        frames={
            "bias": _frame(180, "4h"),
            "setup": _frame(300, "1h"),
            "trigger": _frame(300, "15min"),
        },
        symbol="BTC/USDT",
        entry_mode="limit",
        config_snapshot=_config(),
    )

    assert result.status == "ready"
    assert result.actionable is True
    assert result.stages["bias"].status == "ready"
    assert result.stages["setup"].status == "ready"
    assert result.stages["trigger"].status == "watch"


def test_timeframe_profiles_reject_inverted_hierarchy_and_new_indicator():
    inverted = deepcopy(DEFAULT_TIMEFRAME_PROFILES)
    inverted["roles"]["bias"]["timeframe"] = "15m"
    with pytest.raises(ValueError, match="bias > setup > trigger"):
        validate_timeframe_profiles(inverted)

    unknown = deepcopy(DEFAULT_TIMEFRAME_PROFILES)
    unknown["roles"]["setup"]["indicator_overrides"]["rsi"] = {"weight": 10}
    with pytest.raises(ValueError, match="Unregistered indicators"):
        validate_timeframe_profiles(unknown)

    non_15m_trigger = deepcopy(DEFAULT_TIMEFRAME_PROFILES)
    non_15m_trigger["roles"]["bias"]["timeframe"] = "1d"
    non_15m_trigger["roles"]["setup"]["timeframe"] = "4h"
    non_15m_trigger["roles"]["trigger"]["timeframe"] = "1h"
    with pytest.raises(ValueError, match="Execution trigger timeframe is strictly locked to '15m'"):
        validate_timeframe_profiles(non_15m_trigger)


@pytest.mark.anyio
async def test_mtf_service_fetches_three_closed_windows_and_reuses_cache(monkeypatch):
    service = MTFAnalysisService()
    calls: list[tuple[str, int, bool]] = []

    async def fake_get_ohlcv(
        _symbol, timeframe, _market_type, _exchange, limit, *, closed_only=False
    ):
        calls.append((timeframe, limit, closed_only))
        pandas_tf = "15min" if timeframe == "15m" else timeframe
        return _frame(limit, pandas_tf)

    monkeypatch.setattr(service._market, "get_ohlcv", fake_get_ohlcv)
    first = await service.get(
        symbol="BTC/USDT",
        market_type="crypto",
        exchange="binance",
        entry_mode="limit",
    )
    second = await service.get(
        symbol="BTC/USDT",
        market_type="crypto",
        exchange="binance",
        entry_mode="limit",
    )

    assert first is second
    assert calls == [("1d", 180, True), ("4h", 300, True), ("15m", 600, True)]
    assert first.metadata()["candle_policy"] == "closed_only"


def test_mtf_evidence_replays_all_three_market_windows(monkeypatch):
    signals = {
        "4h": _signal("4h", "long"),
        "1h": _signal("1h", "long"),
        "15m": _signal("15m", "long", trigger=True),
    }

    def fake_analyze(_self, _df, _symbol, timeframe, *_args, **_kwargs):
        return deepcopy(signals[timeframe])

    monkeypatch.setattr("app.services.mtf_analysis.SMCEngine.analyze", fake_analyze)
    frames = {
        "bias": _frame(180, "4h"),
        "setup": _frame(300, "1h"),
        "trigger": _frame(300, "15min"),
    }
    config = _config()
    deterministic_id = mtf_snapshot_id(
        frames=frames,
        symbol="BTC/USDT",
        market_type="crypto",
        exchange="binance",
        entry_mode="limit",
        config_snapshot=config,
    )
    result = analyze_mtf_frames(
        frames=frames,
        symbol="BTC/USDT",
        entry_mode="limit",
        config_snapshot=config,
        snapshot_id=deterministic_id,
    )
    envelope = build_decision_evidence(
        source="proactive_scanner",
        symbol="BTC/USDT",
        timeframe="15m",
        market_type="crypto",
        exchange="binance",
        market_data=frames["trigger"],
        htf_bias="bullish",
        entry_mode="limit",
        signal=result.trigger_signal.to_dict(),
        strategy=result.strategy.to_dict(),
        config_snapshot=config,
        mtf_market_data=frames,
        mtf_decision=result.decision_dict(),
    )

    replay = replay_decision_payload(envelope.payload)

    assert replay["recorded"] == replay["replayed"]
    assert replay["match"] is True
    assert set(envelope.payload["mtf_market_data"]) == {"bias", "setup", "trigger"}


def test_agile_15m_quant_mode_not_blocked_by_neutral_4h(monkeypatch):
    """When mtf_hierarchy_required is False, neutral 4H bias must not block a valid 15M trigger."""
    # 4H is neutral, 1H is neutral, but 15M has a valid S1 breakout
    bias_sig = _signal("4h", "wait")
    bias_sig.bias = "neutral"
    setup_sig = _signal("1h", "wait")
    setup_sig.bias = "neutral"
    trigger_sig = _signal("15m", "short", trigger=True)
    trigger_sig.confluence = 85
    trigger_sig.scenario = {"actionable": True, "name": "S1_BREAKDOWN"}

    signals = {
        "4h": bias_sig,
        "1h": setup_sig,
        "15m": trigger_sig,
    }

    def fake_analyze(_self, _df, _symbol, timeframe, *_args, **_kwargs):
        return deepcopy(signals[timeframe])

    monkeypatch.setattr("app.services.mtf_analysis.SMCEngine.analyze", fake_analyze)
    config = _config()
    config["risk_parameters"] = {"mtf_hierarchy_required": False}

    result = analyze_mtf_frames(
        frames={
            "bias": _frame(180, "4h"),
            "setup": _frame(300, "1h"),
            "trigger": _frame(300, "15min"),
        },
        symbol="XRP/USDT",
        entry_mode="limit",
        config_snapshot=config,
    )

    assert result.actionable is True
    assert result.strategy.approved is True
    assert result.strategy.direction == "short"
    assert result.decision_dict()["grade_badge"] == "EXECUTION TF · READY"
    assert result.decision_dict()["decision_influence"] == "none"


def test_mtf_flag_true_is_ignored_for_decision(monkeypatch):
    signals = {
        "4h": _signal("4h", "short"),
        "1h": _signal("1h", "short"),
        "15m": _signal("15m", "long", trigger=True),
    }

    def fake_analyze(_self, _df, _symbol, timeframe, *_args, **_kwargs):
        return deepcopy(signals[timeframe])

    monkeypatch.setattr("app.services.mtf_analysis.SMCEngine.analyze", fake_analyze)
    config = _config()
    config["risk_parameters"]["mtf_hierarchy_required"] = True
    result = analyze_mtf_frames(
        frames={
            "bias": _frame(180, "4h"),
            "setup": _frame(300, "1h"),
            "trigger": _frame(300, "15min"),
        },
        symbol="BTC/USDT",
        entry_mode="limit",
        config_snapshot=config,
    )

    assert result.actionable is True
    assert result.direction == "long"
    assert result.strategy.direction == "long"
