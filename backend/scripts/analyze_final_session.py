import json

with open("backend/data/realtime_monitor_session.json", "r", encoding="utf-8") as f:
    data = json.load(f)

session_17_18 = [d for d in data if "2026-09-14 17:00:00" <= d.get("timestamp", "") <= "2026-09-14 18:05:00"]
print(f"Total observations between 17:00 and 18:00: {len(session_17_18)}")

if session_17_18:
    start_d = session_17_18[0]
    end_d = session_17_18[-1]
    print(f"Time range: {start_d['timestamp']} -> {end_d['timestamp']}")
    for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "SUI/USDT"]:
        if sym in start_d["data"] and sym in end_d["data"]:
            p1 = start_d["data"][sym].get("price")
            p2 = end_d["data"][sym].get("price")
            st = end_d["data"][sym].get("status")
            sup = end_d["data"][sym].get("nearest_support")
            res = end_d["data"][sym].get("nearest_resistance")
            act = any(d["data"].get(sym, {}).get("actionable", False) for d in session_17_18)
            print(f"{sym}: {p1} -> {p2} | Status: {st} | Sup: {sup} | Res: {res} | Actionable: {act}")

# Check any zone tests
zone_tests = []
for d in session_17_18:
    for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "SUI/USDT"]:
        entry = d.get("data", {}).get(sym, {})
        st = entry.get("status", "")
        if "ZONE_TEST" in st:
            zone_tests.append((d["timestamp"], sym, st, entry.get("expert_comment")))

print(f"\nZone test events between 17:00 and 18:00: {len(zone_tests)}")
for z in zone_tests[:10]:
    print(f"  [{z[0]}] {z[1]}: {z[2]} | {z[3]}")
