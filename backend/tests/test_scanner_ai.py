import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.engines.indicator_core import IndicatorDecisionCore, DEFAULT_INDICATOR_CORE
from app.engines.smc_engine import SMCSignal
from app.engines.strategy_engine import StrategyResult
from app.services.execution_analysis import ExecutionAnalysis, ExecutionAnalysisService
from app.services.scanner_ai import ScannerAI, Proposal, build_context, validate_advisory


def snapshot():
    now = datetime.now(timezone.utc)
    frame = pd.DataFrame({"open": [100.0]*30, "high": [110.0]*30,
                          "low": [99.0]*30, "close": [100.0]*30, "volume": [1000.0]*30},
                         index=pd.date_range(end=pd.Timestamp(now).floor("h")-pd.Timedelta(hours=1), periods=30, freq="h"))
    signal = SMCSignal(symbol="BTC/USDT", timeframe="1h", volume_data_valid=True,
                       volume_quality="exchange_aggressor", direction="long", confluence=60,
                       indicator_decision={"ready": True}, scenario={"actionable": False},
                       tri_core_setup={"actionable": True, "direction": "long"})
    return ExecutionAnalysis("BTC/USDT", "1h", frame, signal,
        StrategyResult(approved=False, rejection_reasons=["legacy observation"]),
        {"filters": {"min_rr": 2}}, "input-id", now, now+timedelta(minutes=20))


def proposal(**changes):
    return {"verdict": "COHERENT", "reason": "Closed candle evidence is coherent",
            "conflicts": [], "management_note": "Use deterministic risk plan",
            "evidence": ["SMC and CVD agree"], **changes}


def test_squeeze_is_optional_bonus_and_never_core_or_readiness():
    sig = snapshot().signal
    cfg = deepcopy(DEFAULT_INDICATOR_CORE)
    cfg["indicators"]["squeeze_momentum"]["required"] = True
    without = IndicatorDecisionCore().evaluate(sig, cfg)
    sig.squeeze_data_valid = True
    sig.squeeze_status = "squeeze_fire"
    sig.squeeze_momentum = 10
    sig.momentum_direction = "accelerating_up"
    with_bonus = IndicatorDecisionCore().evaluate(sig, cfg)
    assert without["score"] == with_bonus["score"]
    assert without["coverage"] == with_bonus["coverage"]
    assert without["ready"] == with_bonus["ready"]
    assert without["squeeze_bonus"] == 0 < with_bonus["squeeze_bonus"]


def test_advisory_requires_deterministic_setup():
    analysis = snapshot()
    analysis.signal.tri_core_setup["actionable"] = False
    with pytest.raises(ValueError):
        validate_advisory(Proposal(**proposal()), build_context(analysis), analysis)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["shadow", "paper"])
async def test_ai_is_advisory_only_in_every_mode(monkeypatch, mode):
    seen = []
    async def dispatch(_self, _provider, messages):
        seen.append(messages)
        return json.dumps(proposal())
    monkeypatch.setattr("app.services.scanner_ai.AIEngine._dispatch", dispatch)
    monkeypatch.setattr("app.services.scanner_ai.write_json", lambda *args: None)
    analysis = snapshot()
    result = await ScannerAI().review(analysis, mode)
    assert len(seen) == 1
    assert result.signal.ai_review["verdict"] == "COHERENT"
    assert result.signal.ai_review["advisory_only"] is True
    assert result.strategy.approved is False
    assert analysis.strategy.approved is False
    assert analysis.signal.ai_review == {}


@pytest.mark.anyio
@pytest.mark.parametrize("raw", ['{"action":"LONG"}', '```json\n{}\n```', json.dumps(proposal(reason="4h confirms"))])
async def test_bad_or_cross_tf_response_never_approves(monkeypatch, raw):
    async def dispatch(*args):
        return raw
    monkeypatch.setattr("app.services.scanner_ai.AIEngine._dispatch", dispatch)
    monkeypatch.setattr("app.services.scanner_ai.write_json", lambda *args: None)
    result = await ScannerAI().review(snapshot(), "paper")
    assert not result.strategy.approved
    assert result.signal.ai_review["status"] == "error"


def test_missing_cvd_blocks_advisory():
    analysis = snapshot()
    context = build_context(analysis)
    analysis.signal.volume_data_valid = False
    analysis.signal.volume_quality = "unavailable"
    with pytest.raises(ValueError, match="unavailable"):
        validate_advisory(Proposal(**proposal()), build_context(analysis), analysis)


@pytest.mark.anyio
async def test_old_async_review_cannot_replace_new_snapshot(monkeypatch):
    service = ExecutionAnalysisService()
    old = snapshot()
    current = snapshot()
    current.snapshot_id = "new-id"
    service._cache[("key",)] = current
    async def review(*args):
        return old.clone()
    monkeypatch.setattr("app.services.scanner_ai.scanner_ai.review", review)
    await service._review(("key",), old, "shadow")
    assert service._cache[("key",)].snapshot_id == "new-id"


def test_proposal_accepts_optional_management_note():
    p = Proposal.model_validate({
        "verdict": "COHERENT",
        "reason": "Closed candle confirms recovery",
        "conflicts": [],
        "management_note": None,
        "evidence": ["recovery"],
    })
    assert p.verdict == "COHERENT"
    assert p.management_note is None


def test_proposal_ai_scalper_execution_fields():
    analysis = snapshot()
    prop = Proposal.model_validate({
        "verdict": "COHERENT",
        "reason": "Closed candle confirms recovery",
        "conflicts": [],
        "evidence": ["recovery"],
        "execution_action": "ENTER_NOW",
        "scalper_bias": "BULLISH_SCALP",
    })
    review = validate_advisory(prop, build_context(analysis), analysis)
    assert review["ai_scalper_approved"] is True
    assert review["execution_action"] == "ENTER_NOW"
    assert review["scalper_bias"] == "BULLISH_SCALP"
