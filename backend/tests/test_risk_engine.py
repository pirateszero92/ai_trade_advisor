from app.engines.risk_engine import RiskEngine
from app.engines.smc_engine import SMCSignal


def test_risk_engine_uses_atr_envelope_for_structural_stop():
    engine = RiskEngine()
    signal = SMCSignal(
        symbol="NEAR/USDT",
        timeframe="15m",
        direction="long",
        entry=100.0,
        stop_loss=98.4,
        take_profit=104.0,
        risk_reward=2.5,
    )

    fixed = engine.evaluate(
        signal,
        account_balance=10_000.0,
        max_stop_distance_pct=1.0,
    )
    adaptive = engine.evaluate(
        signal,
        account_balance=10_000.0,
        max_stop_distance_pct=1.0,
        atr_pct=1.62,
    )

    assert fixed.approved is False
    assert "SL is too wide" in (fixed.rejection_reason or "")
    assert adaptive.approved is True
    assert adaptive.stop_policy == "atr_aware_candidate_seed"
    assert adaptive.stop_distance_atr is not None
    assert adaptive.position_size > 0


def test_risk_engine_approval():
    engine = RiskEngine()
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="1h",
        bias="bullish",
        direction="long",
        entry=50000.0,
        stop_loss=49500.0,
        take_profit=51500.0,
        risk_reward=3.0,
    )

    assessment = engine.evaluate(
        signal=signal,
        account_balance=10000.0,
        open_positions=1,
        daily_pnl_pct=0.5,
        drawdown_pct=0.0,
    )

    assert assessment.approved is True
    assert assessment.risk_reward == 3.0
    assert assessment.position_size > 0
    assert assessment.risk_amount > 0


def test_risk_engine_daily_loss_rejection(monkeypatch):
    engine = RiskEngine()
    monkeypatch.setattr(engine.cfg, "max_daily_loss", 3.0)
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="1h",
        bias="bullish",
        direction="long",
        entry=50000.0,
        stop_loss=49500.0,
        take_profit=51500.0,
        risk_reward=3.0,
    )

    assessment = engine.evaluate(
        signal=signal,
        account_balance=10000.0,
        open_positions=1,
        daily_pnl_pct=-5.0,  # exceeds default 3.0% max loss
    )

    assert assessment.approved is False
    assert "Daily loss limit reached" in (assessment.rejection_reason or "")


def test_risk_engine_max_positions_rejection():
    engine = RiskEngine()
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="1h",
        bias="bullish",
        direction="long",
        entry=50000.0,
        stop_loss=49500.0,
        take_profit=51500.0,
        risk_reward=3.0,
    )

    assessment = engine.evaluate(
        signal=signal,
        account_balance=10000.0,
        open_positions=10,  # exceeds max open positions
    )

    assert assessment.approved is False
    assert "Max open positions reached" in (assessment.rejection_reason or "")


def test_risk_engine_cluster_exposure_control():
    engine = RiskEngine()
    signal = SMCSignal(
        symbol="ETH/USDT",
        timeframe="15m",
        bias="bullish",
        direction="long",
        entry=2500.0,
        stop_loss=2475.0,
        take_profit=2575.0,
        risk_reward=3.0,
    )

    # 1. First position in crypto_l1 cluster -> Full size (cluster_risk_multiplier = 1.0)
    res1 = engine.evaluate(
        signal=signal,
        account_balance=10000.0,
        active_positions=[],
    )
    assert res1.approved is True
    assert res1.cluster_risk_multiplier == 1.0
    assert res1.asset_cluster == "crypto_l1"

    # 2. Second position in crypto_l1 cluster (e.g. BTC already open) -> 50% scale
    res2 = engine.evaluate(
        signal=signal,
        account_balance=10000.0,
        active_positions=[{"symbol": "BTC/USDT", "direction": "long", "status": "open"}],
    )
    assert res2.approved is True
    assert res2.cluster_risk_multiplier == 0.50
    assert any("Cluster concentration" in w for w in res2.warnings)

    # 3. Third position in crypto_l1 cluster (BTC and SOL already open) -> Rejected
    res3 = engine.evaluate(
        signal=signal,
        account_balance=10000.0,
        active_positions=[
            {"symbol": "BTC/USDT", "direction": "long", "status": "open"},
            {"symbol": "SOL/USDT", "direction": "long", "status": "open"},
        ],
    )
    assert res3.approved is False
    assert "Cluster exposure limit reached" in (res3.rejection_reason or "")


def test_risk_engine_rejects_too_tight_sl():
    """Ensure micro-stops like DOGE 0.067% are rejected to avoid fee blowouts and 1-tick stop-outs."""
    engine = RiskEngine()
    signal = SMCSignal(
        symbol="DOGE/USDT",
        timeframe="15m",
        bias="bearish",
        direction="short",
        entry=0.08028,
        stop_loss=0.08034,  # distance = 0.00006 -> 0.074% < 0.25% min
        take_profit=0.07939,
        risk_reward=2.4,
    )

    res = engine.evaluate(
        signal=signal,
        account_balance=10000.0,
        open_positions=0,
    )
    assert res.approved is False
    assert "SL is too tight" in (res.rejection_reason or "")


def test_risk_engine_rejects_excessive_fee_to_risk_ratio():
    """Ensure trades where transaction costs exceed 50% of the stop distance are rejected."""
    engine = RiskEngine()
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        bias="bullish",
        direction="long",
        entry=100.0,
        stop_loss=99.70,  # 0.30% distance (above 0.25% min)
        take_profit=101.0,
        risk_reward=3.0,
    )

    # Cost of 0.20 per unit on 0.30 sl_dist = 66.7% > 50% max
    res = engine.evaluate(
        signal=signal,
        account_balance=10000.0,
        open_positions=0,
        execution_cost_per_unit=0.20,
    )
    assert res.approved is False
    assert "Execution cost too high relative to SL distance" in (res.rejection_reason or "")


def test_risk_engine_cluster_forex_usdtry_not_crypto():
    """Verify operator precedence fix: USDTRY (contains 'USDT') is forex, NOT crypto_l1."""
    from app.engines.risk_engine import get_asset_cluster

    # Forex pair containing 'USDT' substring must NOT be misclassified as crypto
    assert get_asset_cluster("USDTRY") == "forex_majors"
    assert get_asset_cluster("USD/TRY") == "forex_majors"
    assert get_asset_cluster("EURUSD") == "forex_majors"
    assert get_asset_cluster("GBPUSD") == "forex_majors"

    # Crypto L1 with USDT or USD
    assert get_asset_cluster("BTCUSDT") == "crypto_l1"
    assert get_asset_cluster("ETHUSDT") == "crypto_l1"
    assert get_asset_cluster("SOL/USDT") == "crypto_l1"


def test_risk_engine_regime_is_sizing_context_not_entry_gate():
    """Regime may scale risk, but may not independently create/veto entry eligibility."""
    engine = RiskEngine()

    sig_missing_policy = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        bias="bullish",
        direction="long",
        entry=50000.0,
        stop_loss=49500.0,
        take_profit=51500.0,
        risk_reward=3.0,
        market_regime={"regime": "trending", "ready": False},
    )
    res = engine.evaluate(sig_missing_policy, account_balance=10000.0)
    assert res.approved is True
    assert res.regime_risk_multiplier == 1.0
    assert any("Regime policy unavailable" in warning for warning in res.warnings)

    sig_entry_blocked = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        bias="bullish",
        direction="long",
        entry=50000.0,
        stop_loss=49500.0,
        take_profit=51500.0,
        risk_reward=3.0,
        market_regime={
            "regime": "trending",
            "ready": False,
            "policy": {"entry_allowed": False, "risk_multiplier": 0.5},
        },
    )
    res2 = engine.evaluate(sig_entry_blocked, account_balance=10000.0)
    assert res2.approved is True
    assert res2.regime_risk_multiplier == 0.5


def test_risk_engine_drawdown_constants_and_min_rr():
    """Verify named drawdown constants and direct minimum_rr validation."""
    engine = RiskEngine()
    assert engine.DD_10_MULTIPLIER == 0.50
    assert engine.DD_10_MAX_RISK_CAP_PCT == 0.50
    assert engine.DD_5_MULTIPLIER == 0.75
    assert engine.DD_5_MAX_RISK_CAP_PCT == 0.75

    sig = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        bias="bullish",
        direction="long",
        entry=50000.0,
        stop_loss=49500.0,
        take_profit=51500.0,
        risk_reward=3.0,
    )
    # Invalid minimum_rr out of bounds -> rejected directly
    res_bad_rr = engine.evaluate(sig, minimum_rr=25.0)
    assert res_bad_rr.approved is False
    assert "Minimum R:R must be between 1 and 20" in (res_bad_rr.rejection_reason or "")


def test_risk_engine_separates_structural_and_executable_rr():
    engine = RiskEngine()
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        direction="long",
        entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        risk_reward=3.0,
    )

    res = engine.evaluate(
        signal,
        account_balance=10000.0,
        execution_cost_per_unit=0.10,
        minimum_rr=2.0,
    )

    assert res.approved is True
    assert res.structural_rr == 3.0
    assert res.executable_rr == res.risk_reward
    assert res.executable_rr < res.structural_rr
    assert res.minimum_executable_rr == 2.0


def test_regime_min_rr_does_not_create_second_execution_rr_gate():
    engine = RiskEngine()
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        direction="long",
        entry=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        market_regime={
            "regime": "volatile",
            "ready": True,
            "policy": {
                "entry_allowed": True,
                "risk_multiplier": 0.4,
                "min_rr": 10.0,
            },
        },
    )

    res = engine.evaluate(
        signal,
        account_balance=10000.0,
        minimum_rr=1.5,
    )

    assert res.approved is True
    assert res.executable_rr == 2.0
    assert res.minimum_executable_rr == 1.5
    assert res.regime_risk_multiplier == 0.4


def test_sqz_risk_scaling_is_disabled_by_default(monkeypatch):
    from app.engines.risk_engine import squeeze_adjusted_risk_pct

    engine = RiskEngine()
    monkeypatch.setattr("app.core.runtime_config.load_runtime_config", lambda: {})
    monkeypatch.setattr(engine.cfg, "sqz_risk_scaling_enabled", False)
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        direction="long",
        entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        risk_reward=3.0,
        indicator_decision={"squeeze_bonus": 10, "squeeze_bonus_max": 10},
    )

    assert squeeze_adjusted_risk_pct(signal) == min(
        float(engine.cfg.tri_core_base_risk_pct),
        float(engine.cfg.default_risk_per_trade),
        1.0,
    )


def test_sqz_risk_scaling_requires_explicit_enable(monkeypatch):
    from app.engines.risk_engine import squeeze_adjusted_risk_pct

    engine = RiskEngine()
    monkeypatch.setattr("app.core.runtime_config.load_runtime_config", lambda: {})
    monkeypatch.setattr(engine.cfg, "sqz_risk_scaling_enabled", True)
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        direction="long",
        entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        risk_reward=3.0,
        indicator_decision={"squeeze_bonus": 10, "squeeze_bonus_max": 10},
    )

    assert squeeze_adjusted_risk_pct(signal) == min(
        max(
            min(float(engine.cfg.tri_core_base_risk_pct), float(engine.cfg.default_risk_per_trade), 1.0),
            float(engine.cfg.tri_core_max_risk_pct),
        ),
        1.0,
    )


def test_risk_engine_never_mutates_trade_plan_or_direction():
    engine = RiskEngine()
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        bias="bullish",
        direction="long",
        entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        risk_reward=3.0,
        market_regime={
            "regime": "volatile",
            "ready": False,
            "policy": {"entry_allowed": False, "risk_multiplier": 0.5},
        },
    )
    before = (
        signal.direction,
        signal.entry,
        signal.stop_loss,
        signal.take_profit,
        signal.risk_reward,
    )

    result = engine.evaluate(
        signal,
        account_balance=10000.0,
        minimum_rr=1.5,
    )

    assert result.approved is True
    assert (
        signal.direction,
        signal.entry,
        signal.stop_loss,
        signal.take_profit,
        signal.risk_reward,
    ) == before


def test_ai_confidence_and_score_cannot_change_risk_sizing():
    engine = RiskEngine()
    base = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        direction="long",
        entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        risk_reward=3.0,
        indicator_decision={"squeeze_bonus": 0, "squeeze_bonus_max": 10},
    )
    with_ai = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        direction="long",
        entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        risk_reward=3.0,
        indicator_decision={"squeeze_bonus": 0, "squeeze_bonus_max": 10},
        ai_review={
            "status": "reviewed",
            "verdict": "COHERENT",
            "confidence": 0.99,
            "score": 100,
            "risk_pct": 5.0,
            "position_size": 999999,
            "leverage": 50,
        },
    )

    a = engine.evaluate(
        base,
        account_balance=10000.0,
        risk_per_trade_pct=0.75,
        minimum_rr=1.5,
    )
    b = engine.evaluate(
        with_ai,
        account_balance=10000.0,
        risk_per_trade_pct=0.75,
        minimum_rr=1.5,
    )

    assert a.approved is True and b.approved is True
    assert b.position_size == a.position_size
    assert b.risk_pct == a.risk_pct
    assert b.risk_amount == a.risk_amount


def test_ai_conflict_or_unavailable_does_not_rescale_risk_engine_math():
    engine = RiskEngine()
    results = []
    for review in (
        {"status": "reviewed", "verdict": "COHERENT", "confidence": 0.95},
        {"status": "reviewed", "verdict": "CONFLICT", "confidence": 0.95},
        {"status": "error", "verdict": "UNAVAILABLE", "confidence": 0.0},
    ):
        signal = SMCSignal(
            symbol="BTC/USDT",
            timeframe="15m",
            direction="long",
            entry=100.0,
            stop_loss=99.0,
            take_profit=103.0,
            risk_reward=3.0,
            ai_review=review,
        )
        results.append(
            engine.evaluate(
                signal,
                account_balance=10000.0,
                risk_per_trade_pct=0.75,
                minimum_rr=1.5,
            )
        )

    assert len({r.position_size for r in results}) == 1
    assert len({r.risk_pct for r in results}) == 1
    assert len({r.risk_amount for r in results}) == 1


def test_risk_engine_enforces_configurable_min_stop_distance_pct():
    """Ensure trades with SL distance below configured min_stop_distance_pct are rejected."""
    engine = RiskEngine()
    signal = SMCSignal(
        symbol="NEAR/USDT",
        timeframe="15m",
        direction="long",
        entry=5.6447,
        stop_loss=5.5810,  # 1.13% distance
        take_profit=5.8060,
        risk_reward=2.5,
    )

    # When min_stop_distance_pct is 1.5%, a 1.13% stop should be rejected
    res = engine.evaluate(
        signal=signal,
        account_balance=10000.0,
        min_stop_distance_pct=1.5,
    )
    assert res.approved is False
    assert "SL is too tight" in (res.rejection_reason or "")
    assert "min 1.50%" in (res.rejection_reason or "")

    # When min_stop_distance_pct is 1.0%, a 1.13% stop passes
    res_ok = engine.evaluate(
        signal=signal,
        account_balance=10000.0,
        min_stop_distance_pct=1.0,
    )
    assert res_ok.approved is True

