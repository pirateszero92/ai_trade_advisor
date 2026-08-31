"""
Comprehensive Quant Test Suite
Tests Ablation Testing, Monte Carlo Ruin Simulation, Meta-Labeling (Purged K-Fold),
Gaussian HMM Regime Engine, and VPIN Order Flow Toxicity.
"""

import numpy as np
import pandas as pd
import pytest

from app.engines.ablation_engine import AblationEngine
from app.engines.monte_carlo_engine import MonteCarloEngine
from app.engines.meta_labeling import MetaLabelingEngine
from app.engines.hmm_regime_engine import GaussianHMMRegimeEngine
from app.engines.vpin_engine import VPINEngine
from app.engines.strategy_engine import DEFAULT_STRATEGY


def _generate_synthetic_ohlcv(n_bars: int = 150) -> pd.DataFrame:
    np.random.seed(42)
    returns = np.random.normal(0.0005, 0.01, n_bars)
    prices = 100.0 * np.exp(np.cumsum(returns))
    highs = prices * (1.0 + np.random.uniform(0.001, 0.008, n_bars))
    lows = prices * (1.0 - np.random.uniform(0.001, 0.008, n_bars))
    opens = np.roll(prices, 1)
    opens[0] = prices[0]
    volumes = np.random.uniform(1000, 5000, n_bars)
    buy_vols = volumes * np.random.uniform(0.4, 0.7, n_bars)
    sell_vols = volumes - buy_vols

    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": prices,
        "volume": volumes,
        "buy_volume": buy_vols,
        "sell_volume": sell_vols,
        "volume_delta": buy_vols - sell_vols,
    }, index=pd.date_range("2026-01-01", periods=n_bars, freq="15min", tz="UTC"))


def test_ablation_engine():
    df = _generate_synthetic_ohlcv(150)
    engine = AblationEngine()
    study = engine.run_study(
        market_data=df,
        symbol="BTC/USDT",
        timeframe="15m",
        base_config={"strategy": DEFAULT_STRATEGY},
        oos_fraction=0.60,
    )

    assert study.symbol == "BTC/USDT"
    assert "SMC_ONLY" in study.variants
    assert "SMC_CVD" in study.variants
    assert "SMC_SQZ" in study.variants
    assert "FULL_TRINITY" in study.variants
    assert study.best_variant in study.variants


def test_monte_carlo_simulation():
    # 50 historical trades with positive expectancy
    np.random.seed(42)
    trade_pnls = list(np.random.choice([200.0, 150.0, -100.0, 300.0, -80.0], size=60))
    res = MonteCarloEngine.simulate(
        trade_pnls,
        initial_capital=10_000.0,
        iterations=1000,
        trades_per_path=50,
        ruin_drawdown_pct=40.0,
    )

    assert res.iterations == 1000
    assert 0.0 <= res.probability_of_ruin_pct <= 100.0
    assert res.var_95_max_drawdown_pct >= res.median_max_drawdown_pct
    assert "p50" in res.equity_curve_percentiles
    assert len(res.equity_curve_percentiles["p50"]) == 11


def test_meta_labeling_triple_barrier_and_purged_cv():
    df = _generate_synthetic_ohlcv(100)
    engine = MetaLabelingEngine()

    events = [
        {
            "index": i * 3,
            "entry_price": float(df["close"].iloc[i * 3]),
            "direction": "long" if i % 2 == 0 else "short",
            "atr": float((df["high"] - df["low"]).iloc[i * 3]),
            "confluence_score": 75 + (i % 20),
            "smc_score": 30,
            "cvd_score": 20,
            "sqz_score": 20,
            "body_ratio": 0.60,
            "vol_ratio": 1.4,
            "path_efficiency": 0.65,
            "atr_pct": 1.2,
            "delta_ratio": 0.15,
            "hour": 14,
        }
        for i in range(25)
    ]

    labels = engine.apply_triple_barrier(df, events, pt_atr_mult=2.0, sl_atr_mult=1.0, holding_bars=12)
    assert len(labels) == len(events)
    assert all(lbl.barrier_hit in ("upper", "lower", "vertical") for lbl in labels)
    assert all(lbl.label in (0, 1) for lbl in labels)

    eval_res = engine.evaluate_meta_model(events, labels, threshold=0.50, n_splits=3)
    assert 0 < eval_res.train_samples <= len(events)
    assert 0 < eval_res.test_samples <= len(events)
    assert 0.0 <= eval_res.filtered_win_rate_pct <= 100.0
    assert "Confluence" in eval_res.feature_importances

    rejects_all = engine.evaluate_meta_model(events, labels, threshold=1.1, n_splits=3)
    assert rejects_all.retained_trades_pct == 0.0
    assert rejects_all.filtered_win_rate_pct == 0.0

    with pytest.raises(ValueError, match="same number"):
        engine.evaluate_meta_model(events, labels[:-1])
    with pytest.raises(ValueError, match="n_splits"):
        engine.purged_kfold_cv(len(labels), labels, n_splits=len(labels) + 1)


def test_meta_labeling_same_bar_ambiguity_is_stop_first():
    df = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0],
            "high": [101.0, 115.0, 101.0],
            "low": [99.0, 85.0, 99.0],
            "close": [100.0, 100.0, 100.0],
        },
        index=pd.date_range("2026-01-01", periods=3, freq="15min", tz="UTC"),
    )
    labels = MetaLabelingEngine().apply_triple_barrier(
        df,
        [{"index": 0, "entry_price": 100.0, "direction": "long", "atr": 10.0}],
        pt_atr_mult=1.0,
        sl_atr_mult=1.0,
        holding_bars=2,
    )
    assert labels[0].barrier_hit == "lower"
    assert labels[0].label == 0


def test_gaussian_hmm_regime_engine():
    df = _generate_synthetic_ohlcv(100)
    hmm = GaussianHMMRegimeEngine(n_states=3, n_iter=10)
    state = hmm.fit_predict(df)

    assert state.dominant_state in GaussianHMMRegimeEngine.STATES
    assert len(state.state_probabilities) == 3
    assert abs(sum(state.state_probabilities.values()) - 1.0) < 0.05
    assert state.volatility_state in ("low", "medium", "high")
    assert state.trend_state in ("bullish", "bearish", "neutral")
    assert np.isfinite(state.log_likelihood)
    assert state.log_likelihood != pytest.approx(0.0)

    with pytest.raises(ValueError, match="exactly 3 states"):
        GaussianHMMRegimeEngine(n_states=2)


def test_hmm_flat_market_returns_normalized_ranging_state():
    df = pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0},
        index=pd.date_range("2026-01-01", periods=60, freq="15min", tz="UTC"),
    )
    state = GaussianHMMRegimeEngine().fit_predict(df)
    assert state.dominant_state == "ranging_chop"
    assert sum(state.state_probabilities.values()) == pytest.approx(1.0)


def test_vpin_order_flow_toxicity():
    df = _generate_synthetic_ohlcv(100)
    vpin_engine = VPINEngine(bucket_count=30, window=10, toxicity_threshold=0.40)
    res = vpin_engine.calculate(df)

    assert 0.0 <= res.vpin <= 1.0
    assert isinstance(res.toxic_flow_detected, bool)
    assert 0.0 <= res.percentile_toxicity <= 100.0
    assert res.buckets_processed > 0


def test_vpin_and_monte_carlo_report_insufficient_data():
    vpin = VPINEngine().calculate(_generate_synthetic_ohlcv(10))
    assert vpin.status == "insufficient_data"
    assert vpin.vpin is None

    monte = MonteCarloEngine.simulate([1.0, -1.0])
    assert monte.status == "insufficient_data"
    assert monte.probability_of_ruin_pct is None
