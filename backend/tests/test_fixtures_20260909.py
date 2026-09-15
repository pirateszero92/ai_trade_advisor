"""Immutable historical test fixtures for BTC and ETH on 2026-09-09.

Contains canonical 15M closed-candle sequences along with exchange-aggressor
volume delta and cumulative volume delta (CVD). Used for deterministic replay
without network calls or look-ahead bias.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def create_btc_15m_fixture() -> pd.DataFrame:
    """Creates a deterministic 15M candle series for BTC/USDT around the $78,325 sweep.

    Timeline (UTC):
    - 2026-09-08 20:00 to 2026-09-09 02:00: Downtrend into consolidation, establishing
      prior swing low at $78,450.
    - 2026-09-09 03:00 to 03:30: Liquidity sweep candle puncturing down to $78,325.39.
      Aggressive selling occurs but absorption / bullish CVD accumulation takes place.
    - 2026-09-09 03:45: Reclaim candle closing at $78,510 (> $78,450 swing low).
      Flow confirmed with positive CVD delta.
    - 2026-09-09 04:00 to 06:00: Continuation rally up through EQ 50% ($78,862) to $79,486.
    """
    n_bars = 120
    timestamps = pd.date_range("2026-09-08 00:00:00", periods=n_bars, freq="15min", tz="UTC")

    # Baseline gentle drift down from 80,000 to 78,500
    base_prices = np.linspace(80000, 78500, 80)
    # Establish swing low at bar 60 (price ~78,450)
    base_prices[60] = 78450.0

    # Bars 80 to 90: Sweep down to 78,325
    sweep_sequence = np.array([
        78480.0, 78420.0, 78380.0, 78325.39,  # bar 83 is sweep extreme
        78420.0, 78510.0, 78620.0, 78862.0,  # bar 85 reclaims above 78,450
        78950.0, 79100.0, 79300.0, 79486.77   # rally to current price
    ])

    prices = np.zeros(n_bars)
    prices[:80] = base_prices
    prices[80:80 + len(sweep_sequence)] = sweep_sequence
    remaining = n_bars - (80 + len(sweep_sequence))
    if remaining > 0:
        prices[80 + len(sweep_sequence):] = np.linspace(79486.77, 79520.0, remaining)

    opens = np.roll(prices, 1)
    opens[0] = prices[0] + 10.0
    closes = prices.copy()

    # Apply explicit candle settings
    opens[83] = 78450.0
    closes[83] = 78390.0

    opens[84] = 78390.0
    closes[84] = 78440.0

    opens[85] = 78440.0
    closes[85] = 78510.0

    highs = np.maximum(opens, closes) + 20.0
    lows = np.minimum(opens, closes) - 20.0

    # Ensure bar 83 low is exact sweep extreme: 78325.39
    lows[83] = 78325.39
    highs[83] = 78470.0

    lows[84] = 78360.0
    highs[84] = 78460.0

    lows[85] = 78420.0
    highs[85] = 78540.0

    # Strict geometry assertion
    highs = np.maximum.reduce([highs, opens, closes])
    lows = np.minimum.reduce([lows, opens, closes])

    # Realistic volume and taker aggressor volume
    volumes = np.full(n_bars, 500.0)
    volumes[83] = 1850.0  # volume spike on sweep
    volumes[85] = 1450.0  # volume expansion on reclaim

    buy_volume = volumes * 0.50
    sell_volume = volumes * 0.50

    # Bullish absorption on bar 83 (sellers aggressive, but buyers absorb)
    buy_volume[83] = 600.0
    sell_volume[83] = 1250.0

    # Bullish CVD push on bar 85
    buy_volume[85] = 1150.0
    sell_volume[85] = 300.0

    # Continuation bars
    buy_volume[86:] = volumes[86:] * 0.65
    sell_volume[86:] = volumes[86:] * 0.35

    volume_delta = buy_volume - sell_volume
    cvd = np.cumsum(volume_delta)

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "volume_delta": volume_delta,
            "cvd": cvd,
            "flow_source": "binance_taker_volume",
        },
        index=timestamps,
    )
    return df


def create_eth_15m_fixture() -> pd.DataFrame:
    """Creates a deterministic 15M candle series for ETH/USDT around the $2,441.68 sweep."""
    n_bars = 120
    timestamps = pd.date_range("2026-09-08 00:00:00", periods=n_bars, freq="15min", tz="UTC")

    prices = np.linspace(2530.0, 2480.0, 80)
    prices[60] = 2478.0  # prior swing low

    sweep_sequence = np.array([
        2470.0, 2455.0, 2441.68,  # bar 82 is sweep extreme
        2455.0, 2472.0, 2482.0, 2497.9,  # bar 85 reclaims above 2478.0
        2505.0, 2512.0, 2517.96   # current testing Weak High 2517.00
    ])

    all_prices = np.zeros(n_bars)
    all_prices[:80] = prices
    all_prices[80:80 + len(sweep_sequence)] = sweep_sequence
    remaining = n_bars - (80 + len(sweep_sequence))
    if remaining > 0:
        all_prices[80 + len(sweep_sequence):] = np.linspace(2517.96, 2520.0, remaining)

    opens = np.roll(all_prices, 1)
    opens[0] = all_prices[0] + 2.0
    closes = all_prices.copy()

    opens[82] = 2455.0
    closes[82] = 2448.0

    opens[83] = 2448.0
    closes[83] = 2460.0

    opens[84] = 2460.0
    closes[84] = 2472.0

    opens[85] = 2472.0
    closes[85] = 2482.0

    highs = np.maximum(opens, closes) + 3.0
    lows = np.minimum(opens, closes) - 3.0

    lows[82] = 2441.68
    highs[82] = 2460.0

    highs = np.maximum.reduce([highs, opens, closes])
    lows = np.minimum.reduce([lows, opens, closes])

    volumes = np.full(n_bars, 2000.0)
    volumes[82] = 7500.0
    volumes[85] = 5500.0

    buy_volume = volumes * 0.50
    sell_volume = volumes * 0.50

    buy_volume[82] = 2500.0
    sell_volume[82] = 5000.0

    buy_volume[85] = 4200.0
    sell_volume[85] = 1300.0

    volume_delta = buy_volume - sell_volume
    cvd = np.cumsum(volume_delta)

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "volume_delta": volume_delta,
            "cvd": cvd,
            "flow_source": "binance_taker_volume",
        },
        index=timestamps,
    )
    return df
