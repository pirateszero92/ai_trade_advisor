import json

with open("backend/data/realtime_monitor_session.json", "r", encoding="utf-8") as f:
    data = json.load(f)

print("=== BTC Sequence (16:40 -> 17:00) ===")
for d in data[-25:]:
    btc = d.get("data", {}).get("BTC/USDT", {})
    ts = d["timestamp"]
    p = btc.get("price")
    sup = btc.get("nearest_support")
    res = btc.get("nearest_resistance")
    st = btc.get("status")
    rej = btc.get("rejection_reasons", [])
    print(f"[{ts}] Price: {p} | Sup: {sup} | Res: {res} | Status: {st}")
    if st != "MID_RANGE_OBSERVATION":
        print(f"    Reasons: {rej}")

print("\n=== SUI Test at Resistance ===")
for d in data[-10:]:
    sui = d.get("data", {}).get("SUI/USDT", {})
    ts = d["timestamp"]
    p = sui.get("price")
    sup = sui.get("nearest_support")
    res = sui.get("nearest_resistance")
    st = sui.get("status")
    print(f"[{ts}] SUI: {p} | Sup: {sup} | Res: {res} | Status: {st}")
