"""
AI Market Briefing API.
Provides proactive daily voice briefings and institutional market summaries in Thai.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, Query

from app.core.security import verify_api_key
from app.engines.price_hub import price_hub

router = APIRouter()


@router.get("/morning")
async def get_morning_briefing(
    market: Literal["crypto", "forex", "stock", "all"] = Query("crypto"),
    _: str = Depends(verify_api_key),
):
    """
    Generate proactive AI daily briefing script in Thai with key SMC levels and setups.
    """
    btc = price_hub.get_ticker("BTC/USDT") or {}
    eth = price_hub.get_ticker("ETH/USDT") or {}
    sol = price_hub.get_ticker("SOL/USDT") or {}
    gold = price_hub.get_ticker("XAUUSD") or {}

    btc_price = btc.get("price")
    btc_chg = btc.get("change_24h")
    gold_price = gold.get("price")
    gold_chg = gold.get("change_24h")

    # Analyze BTC structure on 1H
    if btc_chg is None:
        regime_text = "ข้อมูลราคา Bitcoin ยังไม่พร้อม จึงยังไม่สามารถจัดประเภท market regime ได้"
    elif abs(float(btc_chg)) > 2.5:
        regime_text = f"Bitcoin เปลี่ยนแปลงมากกว่า 2.5% ใน 24 ชั่วโมงในฝั่ง {'บวก' if btc_chg > 0 else 'ลบ'}"
    else:
        regime_text = "Bitcoin เปลี่ยนแปลงไม่เกิน 2.5% ในช่วง 24 ชั่วโมง"

    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    focus = [
        signal for signal in monitor.recent_signals
        if signal.get("strategy_approved")
        and (market == "all" or signal.get("market_type") == market)
    ][:5]

    def quote_text(name: str, price, change) -> str:
        if price is None:
            return f"{name} ยังไม่มีราคาที่ตรวจสอบได้"
        change_text = f" ({float(change):+.2f}% ใน 24 ชั่วโมง)" if change is not None else ""
        return f"{name} อยู่ที่ {float(price):,.2f}{change_text}"

    # Build audio speech script in natural spoken Thai
    script_paragraphs = [
        "สวัสดีครับ นี่คือสรุปตลาดจากข้อมูลราคาที่ระบบตรวจสอบได้ล่าสุด",
        f"{quote_text('Bitcoin', btc_price, btc_chg)} และ {quote_text('ทองคำ XAUUSD', gold_price, gold_chg)} {regime_text}",
        (
            "Scanner พบ setup ที่ผ่าน strategy gate จำนวน " + str(len(focus)) + " รายการ: " +
            ", ".join(f"{s['symbol']} {s['direction']} confluence {s['confluence']}/100" for s in focus)
            if focus else
            "ขณะนี้ยังไม่มี setup ที่ผ่าน strategy gate ไม่ควรสร้างข้อสรุปหรือเปิดคำสั่งซื้อจาก briefing นี้เพียงอย่างเดียว"
        ),
        "ก่อนส่งคำสั่งซื้อ ให้ตรวจสอบราคาปัจจุบัน Stop Loss ขนาดสัญญา และข้อจำกัดความเสี่ยงในหน้า Signals ทุกครั้ง",
    ]
    full_script = "\n\n".join(script_paragraphs)

    return {
        "status": "success" if btc_price is not None or gold_price is not None else "degraded",
        "title": "Apex Market Morning Briefing",
        "date": datetime.now(timezone.utc).isoformat(),
        "regime": regime_text,
        "market_stats": {
            "btc": {"price": btc_price, "change_24h": btc_chg},
            "gold": {"price": gold_price, "change_24h": gold_chg},
            "eth": {"price": eth.get("price"), "change_24h": eth.get("change_24h")},
            "sol": {"price": sol.get("price"), "change_24h": sol.get("change_24h")},
        },
        "script": full_script,
        "paragraphs": script_paragraphs,
        "voice_lang": "th-TH",
        "key_focus_setups": focus,
    }
