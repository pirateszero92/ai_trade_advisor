"""Real-time Live Market Observer Daemon
Monitors BTC/USDT, ETH/USDT, SOL/USDT, SUI/USDT live on 15M Canonical Timeframe
until 18:00 PM local time (2026-09-12T18:00:00+07:00).
Records all structural price interactions, Tri-Core setups, and discrepancies.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

API_URL = "http://127.0.0.1:8000/api/v1"
API_KEY = os.getenv("OBSERVER_API_KEY", "")
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "SUI/USDT"]
SESSION_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "realtime_monitor_session.json")
SUMMARY_MD_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "realtime_monitor_live.md")

# Target end time: 2 hours from launch time
BKK_TZ = timezone(timedelta(hours=7))
DURATION_HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
END_TIME = datetime.now(BKK_TZ) + timedelta(hours=DURATION_HOURS)


def fetch_overlay(symbol: str) -> dict:
    url = f"{API_URL}/chart/overlay?symbol={urllib.parse.quote(symbol)}&timeframe=15m"
    req = urllib.request.Request(url, headers={"X-API-Key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def evaluate_market_context(overlay: dict) -> dict:
    price = overlay.get("current_price", 0.0)
    sw_high = overlay.get("strong_weak_high", {}) or {}
    sw_low = overlay.get("strong_weak_low", {}) or {}
    high_price = sw_high.get("price")
    low_price = sw_low.get("price")
    order_blocks = overlay.get("order_blocks", []) or []
    strategy = overlay.get("strategy", {}) or {}
    rejections = strategy.get("rejection_reasons", []) or []
    actionable = overlay.get("actionable", False)

    # Find nearest active zones (including S/R Flip Breaker Blocks)
    breakers = overlay.get("breaker_blocks", []) or []
    bearish_obs = [ob for ob in order_blocks if ob.get("direction") == "bearish" and not ob.get("mitigated")]
    bullish_obs = [ob for ob in order_blocks if ob.get("direction") == "bullish" and not ob.get("mitigated")]
    bearish_zones = bearish_obs + [b for b in breakers if b.get("direction") == "bearish" and not b.get("mitigated")]
    bullish_zones = bullish_obs + [b for b in breakers if b.get("direction") == "bullish" and not b.get("mitigated")]

    nearest_resistance = min([ob["bottom"] for ob in bearish_zones if ob.get("bottom", 0) > price], default=high_price)
    nearest_support = max([ob["top"] for ob in bullish_zones if ob.get("top", 0) < price], default=low_price)

    dist_res = (nearest_resistance - price) if nearest_resistance else None
    dist_sup = (price - nearest_support) if nearest_support else None

    # Expert assessment
    if actionable:
        status = "TRADE_TRIGGERED"
        expert_comment = f"Actionable {overlay.get('direction')} setup confirmed! Setup: {strategy.get('setup')}"
    elif nearest_resistance and abs(dist_res or 999999) < (price * 0.0015):
        status = "ZONE_TEST_RESISTANCE"
        expert_comment = f"Testing Resistance / Breaker at {nearest_resistance:.2f}. Watch for breakdown retest rejection."
    elif nearest_support and abs(dist_sup or 999999) < (price * 0.0015):
        status = "ZONE_TEST_SUPPORT"
        expert_comment = f"Testing Support / S/R Flip at {nearest_support:.2f}. Watch for retest confirmation or liquidity sweep."
    else:
        status = "MID_RANGE_OBSERVATION"
        expert_comment = f"Price is in mid-range between support {nearest_support} and resistance {nearest_resistance}. No chase zone."

    return {
        "price": price,
        "nearest_resistance": nearest_resistance,
        "nearest_support": nearest_support,
        "dist_to_res": dist_res,
        "dist_to_sup": dist_sup,
        "actionable": actionable,
        "status": status,
        "rejection_reasons": rejections,
        "expert_comment": expert_comment,
    }


async def main():
    print(f"[{datetime.now(BKK_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}] Starting Real-time Observer until {END_TIME.strftime('%H:%M:%S')}...")
    os.makedirs(os.path.dirname(SESSION_LOG_PATH), exist_ok=True)

    session_history = []
    if os.path.exists(SESSION_LOG_PATH):
        try:
            with open(SESSION_LOG_PATH, "r", encoding="utf-8") as f:
                session_history = json.load(f)
        except Exception:
            session_history = []

    last_summary_write = 0

    while True:
        now = datetime.now(BKK_TZ)
        if now >= END_TIME:
            print(f"[{now.strftime('%H:%M:%S')}] Monitoring session concluded at 6:00 PM.")
            break

        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
        snapshot_records = {}

        for sym in SYMBOLS:
            overlay = fetch_overlay(sym)
            if "error" in overlay:
                continue
            ctx = evaluate_market_context(overlay)
            snapshot_records[sym] = ctx

        # Log snapshot
        log_entry = {
            "timestamp": timestamp_str,
            "data": snapshot_records,
        }
        session_history.append(log_entry)
        if len(session_history) > 1000:
            session_history = session_history[-1000:]

        # Save JSON
        try:
            with open(SESSION_LOG_PATH, "w", encoding="utf-8") as f:
                json.dump(session_history, f, indent=2)
        except Exception as e:
            print(f"Error writing session log: {e}")

        # Console heartbeat
        btc = snapshot_records.get("BTC/USDT", {})
        eth = snapshot_records.get("ETH/USDT", {})
        sol = snapshot_records.get("SOL/USDT", {})
        sui = snapshot_records.get("SUI/USDT", {})

        print(f"[{now.strftime('%H:%M:%S')}] BTC: ${btc.get('price', 0):,.2f} ({btc.get('status')}) | "
              f"ETH: ${eth.get('price', 0):,.2f} | "
              f"SOL: ${sol.get('price', 0):,.2f} | "
              f"SUI: ${sui.get('price', 0):.4f}", flush=True)

        # Update Markdown Summary every 3 minutes
        if time.time() - last_summary_write > 180:
            last_summary_write = time.time()
            md_content = f"""# Real-Time Live Market Monitoring (Session {now.strftime('%Y-%m-%d')})
**Active Window**: {session_history[0]['timestamp']} -> {END_TIME.strftime('%H:%M:%S')} (BKK Time)
**Last Update**: {timestamp_str} (Local Time)

## Current Live Market State
| Symbol | Price | Zone Status | Nearest Resistance | Nearest Support | Tri-Core Status | Expert Observation |
|---|---|---|---|---|---|---|
| **BTC/USDT** | ${btc.get('price', 0):,.2f} | `{btc.get('status')}` | {btc.get('nearest_resistance')} | {btc.get('nearest_support')} | {'✅ ACTIONABLE' if btc.get('actionable') else 'WAIT: ' + '; '.join(btc.get('rejection_reasons', []))} | {btc.get('expert_comment')} |
| **ETH/USDT** | ${eth.get('price', 0):,.2f} | `{eth.get('status')}` | {eth.get('nearest_resistance')} | {eth.get('nearest_support')} | {'✅ ACTIONABLE' if eth.get('actionable') else 'WAIT: ' + '; '.join(eth.get('rejection_reasons', []))} | {eth.get('expert_comment')} |
| **SOL/USDT** | ${sol.get('price', 0):,.2f} | `{sol.get('status')}` | {sol.get('nearest_resistance')} | {sol.get('nearest_support')} | {'✅ ACTIONABLE' if sol.get('actionable') else 'WAIT: ' + '; '.join(sol.get('rejection_reasons', []))} | {sol.get('expert_comment')} |
| **SUI/USDT** | ${sui.get('price', 0):.4f} | `{sui.get('status')}` | {sui.get('nearest_resistance')} | {sui.get('nearest_support')} | {'✅ ACTIONABLE' if sui.get('actionable') else 'WAIT: ' + '; '.join(sui.get('rejection_reasons', []))} | {sui.get('expert_comment')} |

---
*Generated by Real-Time Institutional Market Observer*
"""
            try:
                with open(SUMMARY_MD_PATH, "w", encoding="utf-8") as f:
                    f.write(md_content)
            except Exception as e:
                pass

        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
