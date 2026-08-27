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
