import pandas as pd
import numpy as np
from app.engines.indicators import AdvancedIndicatorsEngine
from app.engines.smc_engine import SMCEngine


def test_squeeze_momentum_calculation():
    # Create synthetic OHLCV data
    np.random.seed(42)
    n = 60
    base_price = 100.0
    returns = np.random.normal(0, 0.01, n)
    prices = base_price * np.exp(np.cumsum(returns))

    df = pd.DataFrame({
        "open": prices * (1 + np.random.normal(0, 0.002, n)),
        "high": prices * (1 + np.abs(np.random.normal(0, 0.005, n))),
        "low": prices * (1 - np.abs(np.random.normal(0, 0.005, n))),
        "close": prices,
        "volume": np.random.uniform(100, 1000, n),
    })

    res = AdvancedIndicatorsEngine.compute_squeeze_momentum(df)
    assert res.status in ("squeeze_on", "squeeze_fire", "no_squeeze")
    assert isinstance(res.momentum, float)
    assert res.direction in ("accelerating_up", "decelerating_up", "accelerating_down", "decelerating_down")
    assert len(res.histogram) > 0
    print(f"Squeeze Result: status={res.status}, mom={res.momentum}, dir={res.direction}")


def test_vectorized_squeeze_matches_linear_regression_reference():
    np.random.seed(7)
    n = 80
    close = 100 + np.cumsum(np.random.normal(0, 0.8, n))
    high = close + np.random.uniform(0.2, 1.2, n)
    low = close - np.random.uniform(0.2, 1.2, n)
    df = pd.DataFrame(
        {
            "open": close + np.random.normal(0, 0.2, n),
            "high": high,
            "low": low,
            "close": close,
            "volume": np.random.uniform(100, 1000, n),
        }
    )
    length = 20

    result = AdvancedIndicatorsEngine.compute_squeeze_momentum(
        df, kc_length=length
    )

    donchian_mid = (
        df["high"].rolling(length).max() + df["low"].rolling(length).min()
    ) / 2.0
    baseline = (donchian_mid + df["close"].rolling(length).mean()) / 2.0
    window = (df["close"] - baseline).tail(length).to_numpy()
    coefficients = np.polyfit(np.arange(length, dtype=float), window, 1)
    expected = np.polyval(coefficients, length - 1)

    assert result.momentum == round(float(expected), 6)


def test_volume_delta_calculation():
    n = 50
    df = pd.DataFrame({
        "open": [100.0 + i for i in range(n)],
        "high": [102.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [101.5 + i for i in range(n)],
        "volume": [500.0 + (i * 10) for i in range(n)],
    })

    vd = AdvancedIndicatorsEngine.compute_volume_delta(df)
    assert isinstance(vd.delta, float)
    assert isinstance(vd.cvd, float)
    assert -1.0 <= vd.delta_ratio <= 1.0
    print(f"Volume Delta: delta={vd.delta}, ratio={vd.delta_ratio}, cvd={vd.cvd}, desc={vd.description}")


def test_smc_confluence_integration():
    n = 60
    df = pd.DataFrame({
        "open": [100.0 + (i * 0.2) for i in range(n)],
        "high": [101.0 + (i * 0.2) for i in range(n)],
        "low": [99.5 + (i * 0.2) for i in range(n)],
        "close": [100.8 + (i * 0.2) for i in range(n)],
        "volume": [1000.0 for _ in range(n)],
    })

    engine = SMCEngine()
    sig = engine.analyze(df, "BTC/USDT", "1h")
    assert 0 <= sig.confluence <= 100
    assert sig.squeeze_status in ("squeeze_on", "squeeze_fire", "no_squeeze")
    assert isinstance(sig.volume_delta, float)
    print(f"SMC Integrated Signal: Confluence={sig.confluence}/100, Direction={sig.direction}, Squeeze={sig.squeeze_status}")
