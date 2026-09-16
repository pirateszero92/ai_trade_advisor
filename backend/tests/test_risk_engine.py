from app.engines.risk_engine import RiskEngine
from app.engines.smc_engine import SMCSignal


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


def test_risk_engine_fail_closed_regime_gate():
    """Verify that when market regime is provided but policy is missing or not ready, it fails closed."""
    engine = RiskEngine()

    # 1. Regime data provided but policy is missing/invalid -> fail closed
    sig_missing_policy = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        bias="bullish",
        direction="long",
        entry=50000.0,
        stop_loss=49500.0,
        take_profit=51500.0,
        risk_reward=3.0,
        market_regime={"regime": "trending", "ready": True},  # missing policy dict
    )
    res = engine.evaluate(sig_missing_policy, account_balance=10000.0)
    assert res.approved is False
    assert "Market regime policy missing or incomplete" in (res.rejection_reason or "")

    # 2. Regime data provided but ready is False -> fail closed
    sig_not_ready = SMCSignal(
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
            "policy": {"entry_allowed": True, "risk_multiplier": 1.0},
        },
    )
    res2 = engine.evaluate(sig_not_ready, account_balance=10000.0)
    assert res2.approved is False
    assert "New risk is blocked" in (res2.rejection_reason or "")


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
