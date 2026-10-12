"""US-equity trade/quote classification and bar aggregation.

Equities do not publish Binance-style taker-buy volume in OHLCV bars.  This
module classifies consolidated/venue trades against the latest quote and uses
the tick rule only as a labelled fallback.  The resulting delta is therefore
``quote-classified equity flow`` and is never presented as exchange-native
taker CVD.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class EquityFlowQuality:
    complete: bool
    bars: int
    trusted_bars: int
    total_trades: int
    quote_classified_trades: int


def _ticks_frame(rows: list[dict[str, Any]], *, kind: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    if "t" not in frame.columns:
        return pd.DataFrame()
    frame["timestamp"] = pd.to_datetime(frame["t"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp")
    if kind == "trade":
        aliases = {"p": "price", "s": "size"}
        required = {"p", "s"}
    else:
        aliases = {"bp": "bid_price", "ap": "ask_price"}
        required = {"bp", "ap"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    frame = frame.rename(columns=aliases)
    numeric_columns = list(aliases.values())
    frame[numeric_columns] = frame[numeric_columns].apply(
        pd.to_numeric, errors="coerce"
    )
    frame = frame.dropna(subset=numeric_columns)
    return frame[["timestamp", *numeric_columns]]


def classify_and_aggregate_equity_flow(
    trades: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    *,
    timeframe: str,
    source_complete: bool,
    min_quote_coverage: float = 0.60,
    min_trades_per_bar: int = 20,
) -> tuple[pd.DataFrame, EquityFlowQuality]:
    """Return quote-classified buy/sell volume aggregated to candle opens.

    A trade at/above ask is buyer initiated; a trade at/below bid is seller
    initiated.  Inside-spread trades use the causal tick rule.  Bars are trusted
    only when the source pagination completed and enough trades were directly
    classified against quotes.
    """
    if not 0.0 <= min_quote_coverage <= 1.0:
        raise ValueError("min_quote_coverage must be between 0 and 1")
    if min_trades_per_bar < 1:
        raise ValueError("min_trades_per_bar must be positive")

    trade_frame = _ticks_frame(trades, kind="trade")
    quote_frame = _ticks_frame(quotes, kind="quote")
    if trade_frame.empty:
        return pd.DataFrame(), EquityFlowQuality(
            complete=source_complete,
            bars=0,
            trusted_bars=0,
            total_trades=0,
            quote_classified_trades=0,
        )

    if quote_frame.empty:
        merged = trade_frame.copy()
        merged["bid_price"] = np.nan
        merged["ask_price"] = np.nan
    else:
        merged = pd.merge_asof(
            trade_frame,
            quote_frame,
            on="timestamp",
            direction="backward",
            tolerance=pd.Timedelta(seconds=30),
        )

    price = merged["price"].astype(float)
    size = merged["size"].astype(float).clip(lower=0.0)
    valid_quote = (
        merged["bid_price"].notna()
        & merged["ask_price"].notna()
        & (merged["bid_price"] > 0)
        & (merged["ask_price"] >= merged["bid_price"])
    )
    at_ask = valid_quote & (price >= merged["ask_price"])
    at_bid = valid_quote & (price <= merged["bid_price"])
    side = pd.Series(0, index=merged.index, dtype="int8")
    side.loc[at_ask] = 1
    side.loc[at_bid & ~at_ask] = -1
    quote_classified = side != 0

    tick_direction = np.sign(price.diff()).replace(0, np.nan).ffill().fillna(0)
    side.loc[~quote_classified] = tick_direction.loc[~quote_classified].astype("int8")
    classified = side != 0
    merged["buy_volume"] = np.where(side > 0, size, 0.0)
    merged["sell_volume"] = np.where(side < 0, size, 0.0)
    merged["classified_volume"] = np.where(classified, size, 0.0)
    merged["quote_classified_volume"] = np.where(quote_classified, size, 0.0)
    merged["trade_count"] = 1

    rule = {
        "1m": "1min",
        "2m": "2min",
        "3m": "3min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "2h": "2h",
        "4h": "4h",
        "1d": "1D",
    }.get(str(timeframe).lower())
    if rule is None:
        raise ValueError(f"Unsupported equity-flow timeframe: {timeframe}")
    merged = merged.set_index("timestamp")
    bars = merged.resample(rule, label="left", closed="left").agg({
        "buy_volume": "sum",
        "sell_volume": "sum",
        "classified_volume": "sum",
        "quote_classified_volume": "sum",
        "size": "sum",
        "trade_count": "sum",
    })
    bars = bars[bars["trade_count"] > 0].copy()
    bars["flow_coverage"] = np.where(
        bars["size"] > 0,
        bars["classified_volume"] / bars["size"],
        0.0,
    )
    bars["quote_coverage"] = np.where(
        bars["size"] > 0,
        bars["quote_classified_volume"] / bars["size"],
        0.0,
    )
    trusted = (
        source_complete
        & (bars["trade_count"] >= min_trades_per_bar)
        & (bars["quote_coverage"] >= min_quote_coverage)
    )
    bars["flow_source"] = np.where(
        trusted, "alpaca_quote_classified", "alpaca_flow_incomplete"
    )
    bars["flow_granularity"] = "trade_quote_derived"
    bars = bars.drop(columns=["size"])
    return bars, EquityFlowQuality(
        complete=source_complete,
        bars=len(bars),
        trusted_bars=int(trusted.sum()),
        total_trades=int(len(merged)),
        quote_classified_trades=int(quote_classified.sum()),
    )
