import requests, pandas as pd, sys
sys.stdout.reconfigure(encoding="utf-8")

r = requests.get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=150", timeout=10)
raw = r.json()
cols = ["ts","open","high","low","close","volume","ct","qav","nt","tbbav","tbqav","ig"]
df = pd.DataFrame(raw, columns=cols)
for c in ["open","high","low","close","volume"]:
    df[c] = pd.to_numeric(df[c])
df = df[["open","high","low","close","volume"]]
print(f"Got {len(df)} bars. Latest close: {df['close'].iloc[-1]:.2f}")

from app.engines.indicators import AdvancedIndicatorsEngine
from app.engines.smc_engine import SMCEngine
from app.engines.strategy_engine import StrategyEngine

sq = AdvancedIndicatorsEngine.compute_squeeze_momentum(df)
vd = AdvancedIndicatorsEngine.compute_volume_delta(df)
print(f"Squeeze : status={sq.status}, momentum={sq.momentum:.4f}, direction={sq.direction}")
print(f"VolDelta: delta={vd.delta:.2f}, ratio={vd.delta_ratio:.3f}, cvd={vd.cvd:.4f}, absorption={vd.is_absorption}")

engine = SMCEngine()
sig = engine.analyze(df, "BTC/USDT", "1h", htf_bias="neutral")
ob_str = f"top={sig.order_block.top:.2f}, bottom={sig.order_block.bottom:.2f}, mid={sig.order_block.mid:.2f}" if sig.order_block else "None"
fvg_str = f"top={sig.fvg.top:.2f}, bottom={sig.fvg.bottom:.2f}" if sig.fvg else "None"
print(f"Structure: bias={sig.bias}, BOS={sig.bos}, CHoCH={sig.choch}")
print(f"Zone     : premium={sig.in_premium}, discount={sig.in_discount}, eq={sig.equilibrium:.2f}")
print(f"OB       : {ob_str}")
print(f"FVG      : {fvg_str}")
print(f"Liquidity: equal_highs={sig.equal_highs}, equal_lows={sig.equal_lows}")
print(f"Trade    : dir={sig.direction}, entry={sig.entry}, sl={sig.stop_loss}, tp={sig.take_profit}, rr={sig.risk_reward}R")
print(f"Confluence: {sig.confluence}/100")

strategy = StrategyEngine()
res = strategy.evaluate(sig)
print(f"Strategy: approved={res.approved}, score={res.score}")
if res.rejection_reasons:
    for r2 in res.rejection_reasons:
        print(f"  Rejected: {r2}")
if res.passed_checks:
    for c2 in res.passed_checks:
        print(f"  Passed  : {c2}")

# Verify fix #12 - prompt loading
from app.engines.ai_engine import AIEngine
ai = AIEngine()
prompt_preview = ai.system_prompt[:120].replace("\n", " ")
print(f"\nPrompt loaded (first 120 chars): {prompt_preview}")
print("\nALL REAL DATA CHECKS DONE!")
