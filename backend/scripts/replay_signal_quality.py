"""Reproducible offline signal-quality & SL/ATR15m replay tool.

Evaluates every PaperOMS signal since `--since` by simulating immediate market
entry at `created_at` against 1-minute (or 5-minute) Binance Futures candles,
independent of limit-fill survivorship bias.

Usage inside `ai_trade_backend`:
    python -m scripts.replay_signal_quality --since 2026-11-03 --fee-bps 14
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from typing import Any

import ccxt.async_support as ccxt
import numpy as np
import pandas as pd
from sqlalchemy import select

from app.models.base import async_session_factory
from app.models.paper_oms import PaperOMSPosition


async def _fetch_ohlcv_window(
    exchange: Any,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    tf_ms: int,
) -> pd.DataFrame:
    rows: list[list[Any]] = []
    cursor = start_ms
    while cursor <= end_ms:
        batch = await exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + tf_ms
        if next_cursor <= cursor or len(batch) < 1000:
            break
        cursor = next_cursor
        await asyncio.sleep(0.12)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    frame["ts"] = pd.to_datetime(frame["ts"], unit="ms", utc=True)
    return frame.drop_duplicates("ts").set_index("ts").sort_index()


def simulate_immediate_market(
    bars: pd.DataFrame,
    direction: str,
    entry: float,
    sl_pct: float,
    tp_pct: float,
    fee_rt: float,
) -> tuple[float, float, str] | None:
    """Return `(gross_r, net_r, outcome)` with pessimistic SL-first tie-break."""
    if bars.empty or entry <= 0 or sl_pct <= 0 or tp_pct <= 0:
        return None
    sl = entry * (1.0 - sl_pct) if direction == "long" else entry * (1.0 + sl_pct)
    tp = entry * (1.0 + tp_pct) if direction == "long" else entry * (1.0 - tp_pct)
    cost_r = fee_rt / sl_pct
    for _, row in bars.iterrows():
        high, low = float(row["high"]), float(row["low"])
        if direction == "long":
            if low <= sl:
                return -1.0, -1.0 - cost_r, "sl"
            if high >= tp:
                gross = tp_pct / sl_pct
                return gross, gross - cost_r, "tp"
        else:
            if high >= sl:
                return -1.0, -1.0 - cost_r, "sl"
            if low <= tp:
                gross = tp_pct / sl_pct
                return gross, gross - cost_r, "tp"
    last_close = float(bars["close"].iloc[-1])
    raw_ret = ((last_close - entry) / entry) if direction == "long" else ((entry - last_close) / entry)
    gross = raw_ret / sl_pct
    return gross, gross - cost_r, "timeout"


def _ci95(values: list[float]) -> tuple[float, float]:
    if len(values) < 2:
        mean = float(np.mean(values)) if values else 0.0
        return mean, mean
    arr = np.asarray(values, dtype=float)
    se = float(np.std(arr, ddof=1) / np.sqrt(len(arr)))
    mean = float(np.mean(arr))
    return mean - 1.96 * se, mean + 1.96 * se


async def run_replay(since: str, timeframe: str, hold_hours: int, fee_bps: float) -> None:
    fee_rt = fee_bps / 10_000.0
    tf_ms = 60_000 if timeframe == "1m" else 5 * 60_000

    async with async_session_factory() as db:
        rows = (
            await db.execute(select(PaperOMSPosition).order_by(PaperOMSPosition.created_at))
        ).scalars().all()
    signals = [p for p in rows if p.created_at and p.created_at.isoformat() >= since]
    print(
        f"Signals >= {since}: total={len(signals)} "
        f"(closed={sum(1 for p in signals if p.status == 'closed')}, "
        f"other={sum(1 for p in signals if p.status != 'closed')})"
    )
    if not signals:
        return

    exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
    symbols = sorted({p.symbol for p in signals})
    cache_15m: dict[str, pd.DataFrame] = {}
    start_15m = int((pd.Timestamp(since, tz="UTC") - pd.Timedelta(hours=12)).timestamp() * 1000)
    end_15m = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)

    records: list[dict[str, Any]] = []
    try:
        for sym in symbols:
            df15 = await _fetch_ohlcv_window(exchange, sym, "15m", start_15m, end_15m, 15 * 60_000)
            if not df15.empty:
                tr = pd.concat(
                    [
                        (df15["high"] - df15["low"]).abs(),
                        (df15["high"] - df15["close"].shift(1)).abs(),
                        (df15["low"] - df15["close"].shift(1)).abs(),
                    ],
                    axis=1,
                ).max(axis=1)
                df15["atr16"] = tr.rolling(16).mean()
                cache_15m[sym] = df15

        for p in signals:
            df15 = cache_15m.get(p.symbol)
            if df15 is None or df15.empty:
                continue
            ts = pd.Timestamp(p.created_at)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            past15 = df15[df15.index <= ts - pd.Timedelta(minutes=15)]
            if len(past15) < 17:
                continue
            atr15 = float(past15["atr16"].iloc[-1])

            t0 = int(ts.timestamp() * 1000)
            t1 = t0 + hold_hours * 3600_000
            bars = await _fetch_ohlcv_window(exchange, p.symbol, timeframe, t0, t1, tf_ms)
            if bars.empty:
                continue

            entry = float(bars["open"].iloc[0])
            req_entry = float(p.requested_entry_price or entry)
            req_sl = float(p.initial_stop_loss or p.stop_loss or 0.0)
            sp = p.source_payload if isinstance(p.source_payload, dict) else {}
            tp1 = sp.get("take_profit_1")
            req_tp = float(tp1 if tp1 is not None else (p.take_profit or 0.0))
            if req_entry <= 0 or req_sl <= 0 or req_tp <= 0:
                continue

            sl_pct = abs(req_entry - req_sl) / req_entry
            tp_pct = abs(req_tp - req_entry) / req_entry
            sim = simulate_immediate_market(bars, p.direction, entry, sl_pct, tp_pct, fee_rt)
            if sim is None:
                continue
            gross_r, net_r, outcome = sim
            sl_atr = (sl_pct * entry) / atr15 if atr15 > 0 else 0.0

            latency_sec = None
            bar_time_raw = sp.get("signal_bar_time")
            if bar_time_raw:
                try:
                    bar_ts = pd.Timestamp(bar_time_raw)
                    if bar_ts.tzinfo is None:
                        bar_ts = bar_ts.tz_localize("UTC")
                    latency_sec = (ts - (bar_ts + pd.Timedelta(minutes=15))).total_seconds()
                except Exception:
                    latency_sec = None

            records.append(
                {
                    "created_at": ts,
                    "setup_type": str(sp.get("setup_type", "unknown")),
                    "gross_r": gross_r,
                    "net_r": net_r,
                    "win": outcome == "tp" or gross_r > 0,
                    "sl_atr": sl_atr,
                    "latency_sec": latency_sec,
                }
            )
    finally:
        await exchange.close()

    if not records:
        print("No evaluable signals found.")
        return

    gross_all = [r["gross_r"] for r in records]
    net_all = [r["net_r"] for r in records]
    ci_lo, ci_hi = _ci95(net_all)
    wr_all = sum(1 for r in records if r["win"]) / len(records) * 100.0
    print(
        f"\nALL ({len(records)}): WR={wr_all:.1f}% | Gross={np.mean(gross_all):+.3f}R | "
        f"Net={np.mean(net_all):+.3f}R (95% CI [{ci_lo:+.3f}, {ci_hi:+.3f}])"
    )

    print("\n--- BY SETUP_TYPE ---")
    by_setup: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_setup[r["setup_type"]].append(r)
    for st, items in sorted(by_setup.items(), key=lambda kv: -len(kv[1])):
        nets = [x["net_r"] for x in items]
        lo, hi = _ci95(nets)
        wr = sum(1 for x in items if x["win"]) / len(items) * 100.0
        print(
            f"  {st:24s}: n={len(items):3d} | WR={wr:5.1f}% | "
            f"Gross={np.mean([x['gross_r'] for x in items]):+.3f}R | "
            f"Net={np.mean(nets):+.3f}R [{lo:+.2f}, {hi:+.2f}]"
        )

    print("\n--- BY SL / ATR15m (Split H1 vs H2) ---")
    mid = len(records) // 2
    buckets = [
        ("<0.8x ATR15", lambda v: v < 0.8),
        ("0.8-1.2x ATR15", lambda v: 0.8 <= v < 1.2),
        ("1.2-2.0x ATR15", lambda v: 1.2 <= v <= 2.0),
        (">2.0x ATR15", lambda v: v > 2.0),
    ]
    for label, pred in buckets:
        sub = [x for x in records if pred(x["sl_atr"])]
        if not sub:
            continue
        h1 = [x["net_r"] for x in records[:mid] if pred(x["sl_atr"])]
        h2 = [x["net_r"] for x in records[mid:] if pred(x["sl_atr"])]
        nets = [x["net_r"] for x in sub]
        wr = sum(1 for x in sub if x["win"]) / len(sub) * 100.0
        print(
            f"  {label:16s}: n={len(sub):3d} | WR={wr:5.1f}% | Net={np.mean(nets):+.3f}R | "
            f"H1={np.mean(h1) if h1 else 0.0:+.3f}R({len(h1)}) | "
            f"H2={np.mean(h2) if h2 else 0.0:+.3f}R({len(h2)})"
        )

    latencies = [r["latency_sec"] for r in records if r["latency_sec"] is not None]
    if latencies:
        print(
            f"\nSignal-bar latency (n={len(latencies)}): "
            f"median={np.median(latencies):.1f}s | p90={np.percentile(latencies, 90):.1f}s"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay PaperOMS signal quality against Binance bars.")
    parser.add_argument("--since", default="2026-11-03", help="ISO start date (UTC)")
    parser.add_argument("--timeframe", choices=["1m", "5m"], default="1m")
    parser.add_argument("--hold-hours", type=int, default=24)
    parser.add_argument("--fee-bps", type=float, default=14.0, help="Round-trip cost in bps")
    args = parser.parse_args()
    asyncio.run(run_replay(args.since, args.timeframe, args.hold_hours, args.fee_bps))


if __name__ == "__main__":
    main()
