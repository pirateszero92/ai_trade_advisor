import asyncio
from sqlalchemy import select
from app.models.base import async_session_factory
from app.models.paper_oms import PaperOMSPosition, PaperOMSEvent

async def check():
    async with async_session_factory() as session:
        result = await session.execute(select(PaperOMSPosition))
        positions = result.scalars().all()
        print(f"Total positions in DB: {len(positions)}")
        for p in positions:
            print(f"Symbol: {p.symbol} | Status: {p.status} | Direction: {p.direction} | Entry: {p.average_entry_price} | SL: {p.stop_loss} | Initial SL: {p.initial_stop_loss} | TP: {p.take_profit} | Auto-BE: {p.auto_be} | Trailing: {p.trailing_stop} | Protection Stage: {p.protection_stage} | Favorable Extreme: {p.favorable_extreme}")

        # Check events
        ev_result = await session.execute(select(PaperOMSEvent).order_by(PaperOMSEvent.created_at.desc()).limit(15))
        events = ev_result.scalars().all()
        print("\n=== RECENT OMS EVENTS ===")
        for e in events:
            print(f"Event: {e.event_type} | Position ID: {e.position_id} | Created: {e.created_at} | Details: {e.details}")

if __name__ == "__main__":
    asyncio.run(check())
