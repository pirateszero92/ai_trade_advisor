import asyncio
import json
from app.services.paper_oms import PaperOMS

async def check():
    oms = PaperOMS()
    await oms.initialize()
    positions = await oms.get_open_positions()
    print("=== OPEN POSITIONS ===")
    for p in positions:
        print(json.dumps(p, indent=2, default=str))
    
    trades = await oms.get_trade_history(limit=10)
    print("\n=== RECENT CLOSED TRADES ===")
    for t in trades:
        print(f"Symbol: {t.get('symbol')} | Direction: {t.get('direction')} | Entry: {t.get('entry_price')} | Exit: {t.get('exit_price')} | Exit Reason: {t.get('exit_reason')} | Realized PnL: {t.get('realized_pnl')} | Max R: {t.get('max_r_achieved')}")

if __name__ == "__main__":
    asyncio.run(check())
