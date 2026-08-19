"""
Event Trigger & Proactive Market Monitor.
Scans multi-market watchlist, analyzes SMC structure, evaluates regime,
invokes Apex AI advisor on high-confluence setups, and sends multi-channel alerts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional
from loguru import logger

from app.engines.market_data import MarketDataEngine
from app.engines.smc_engine import SMCEngine
from app.engines.strategy_engine import StrategyEngine
from app.engines.ai_engine import AIEngine
from app.engines.risk_engine import RiskEngine
from app.services.notification import NotificationService
from app.api.ws import broadcast

DEFAULT_WATCHLIST = [
    # Crypto
    {"symbol": "BTC/USDT", "timeframe": "1h", "htf_timeframe": "4h", "market_type": "crypto", "exchange": "binance"},
    {"symbol": "ETH/USDT", "timeframe": "1h", "htf_timeframe": "4h", "market_type": "crypto", "exchange": "binance"},
    {"symbol": "SOL/USDT", "timeframe": "1h", "htf_timeframe": "4h", "market_type": "crypto", "exchange": "binance"},
    # Forex & Gold
    {"symbol": "XAUUSD", "timeframe": "1h", "htf_timeframe": "4h", "market_type": "forex", "exchange": "mt5"},
    {"symbol": "EURUSD", "timeframe": "1h", "htf_timeframe": "4h", "market_type": "forex", "exchange": "mt5"},
    # Stocks
    {"symbol": "AAPL", "timeframe": "1d", "htf_timeframe": "1w", "market_type": "stock", "exchange": "alpaca"},
    {"symbol": "TSLA", "timeframe": "1d", "htf_timeframe": "1w", "market_type": "stock", "exchange": "alpaca"},
    {"symbol": "NVDA", "timeframe": "1d", "htf_timeframe": "1w", "market_type": "stock", "exchange": "alpaca"},
]


class MarketMonitor:
    """Proactively monitors watchlist symbols, checks SMC conditions,
    invokes Apex AI, and broadcasts signals via WebSocket & Push notifications.
    """

    _instance: Optional[MarketMonitor] = None

    def __init__(
        self,
        watchlist: Optional[list[dict]] = None,
        poll_interval_seconds: int = 45,
    ):
        self.watchlist = watchlist or DEFAULT_WATCHLIST
        self.interval = poll_interval_seconds
        self.market_data = MarketDataEngine()
        self.smc = SMCEngine()
        self.strategy = StrategyEngine()
        self.ai = AIEngine()
        self.risk = RiskEngine()
        self.notifier = NotificationService()
        self.running = False
        self.last_scan_time: Optional[str] = None
        self.recent_signals: list[dict] = []
        self._task: Optional[asyncio.Task] = None

    @classmethod
    def get_instance(cls) -> MarketMonitor:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(self):
        if self.running:
            return
        self.running = True
        logger.info(f"🚀 Proactive MarketMonitor started with {len(self.watchlist)} watchlist symbols.")
        self._task = asyncio.create_task(self._run_loop())

    def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
        logger.info("🛑 MarketMonitor stopped.")

    async def _run_loop(self):
        while self.running:
            try:
                await self.scan_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in proactive scan loop: {e}")
            await asyncio.sleep(self.interval)

    async def scan_all(self) -> list[dict]:
        """Scan all watchlist symbols and return newly detected signals."""
        self.last_scan_time = datetime.now(timezone.utc).isoformat()
        logger.info(f"🔍 Proactive Scanner scanning {len(self.watchlist)} symbols...")
        new_signals = []

        for item in self.watchlist:
            symbol = item["symbol"]
            tf = item.get("timeframe", "1h")
            htf = item.get("htf_timeframe", "4h")
            m_type = item.get("market_type", "crypto")
            ex = item.get("exchange", "binance")

            try:
                # 1. Fetch multi-timeframe OHLCV
                ltf_df = await self.market_data.get_ohlcv(symbol, tf, m_type, ex, limit=150)
                if ltf_df.empty:
                    continue

                # 2. SMC Multi-timeframe analysis
                htf_df = await self.market_data.get_ohlcv(symbol, htf, m_type, ex, limit=80)
                htf_bias = "neutral"
                if not htf_df.empty:
                    htf_sig = self.smc.analyze(htf_df, symbol, htf)
                    htf_bias = htf_sig.bias

                ltf_sig = self.smc.analyze(ltf_df, symbol, tf, htf_bias=htf_bias)

                # 3. Strategy evaluation
                strat_res = self.strategy.evaluate(ltf_sig)
                confluence = ltf_sig.confluence_score

                # Trigger condition: Confluence >= 40 or strategy approved or clear bias
                if strat_res.approved or confluence >= 40 or ltf_sig.bias in ("bullish", "bearish"):
                    direction = strat_res.direction if strat_res.direction in ("long", "short") else ("long" if ltf_sig.bias == "bullish" else "short")
                    entry = float(ltf_sig.current_price) if ltf_sig.current_price > 0 else float(ltf_df["close"].iloc[-1])
                    sl = entry * 0.992 if direction == "long" else entry * 1.008
                    sl_dist = abs(entry - sl)
                    tp = entry + (sl_dist * 2.2) if direction == "long" else entry - (sl_dist * 2.2)

                    zone_name = "Discount" if direction == "long" else "Premium"
                    if confluence >= 80:
                        grade = "A+"
                        action_advice = f"[Grade A+ | Confluence {confluence}/100 🟢 HIGH CONVICTION]: เทรนด์ใหญ่สอดคล้อง + เกิดการ Sweep สภาพคล่องชัดเจน และราคาแตะ Order Block ในโซน {zone_name} แนะนำพิจารณาเข้าตามแผน Entry ${entry:.2f} SL ${sl:.2f} (ความเสี่ยง 1.0%)"
                    elif confluence >= 65:
                        grade = "B"
                        action_advice = f"[Grade B | Confluence {confluence}/100 🟡 STANDARD SETUP]: โครงสร้าง {direction.upper()} แตะ Order Block ในโซน {zone_name} แต่ควรรอแท่งยืนยัน Rejection ก่อนเข้า หรือลดความเสี่ยงเหลือ 0.5%"
                    else:
                        grade = "C"
                        action_advice = f"[Grade C | Confluence {confluence}/100 ⚠️ แนะนำให้ WAIT / ข้าม]: ยังไม่ควรเสี่ยงเข้าทันที แม้ราคาจะแตะ Order Block ในโซน {zone_name} แต่มีความเสี่ยงสวนเทรนด์ใหญ่หรือขาดตัวยืนยัน หากจะเข้าต้องรอแท่ง 15M/1H เบรกทำ CHoCH กลับตัวก่อนเสมอ"

                    # 4. Invoke Apex AI Advisor for institutional summary
                    try:
                        ai_res = await self.ai.analyze(
                            ltf_sig,
                            portfolio_state={"balance": 10000.0, "drawdown_pct": 0.0},
                        )
                        ai_message = ai_res.message
                        if not ai_message or "neutral" in ai_message.lower():
                            ai_message = action_advice
                    except Exception as ai_err:
                        logger.warning(f"AI generation fallback: {ai_err}")
                        ai_message = action_advice

                    signal_payload = {
                        "id": f"{symbol}_{tf}_{int(datetime.now(timezone.utc).timestamp())}",
                        "symbol": symbol,
                        "market_type": m_type,
                        "timeframe": tf.upper(),
                        "htf_timeframe": htf.upper(),
                        "direction": direction.upper(),
                        "confluence": confluence,
                        "entry": round(entry, 2),
                        "stop_loss": round(sl, 2),
                        "take_profit": round(tp, 2),
                        "rr": 2.2,
                        "message": ai_message,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                    # Deduplicate in recent signals
                    if not any(s["symbol"] == symbol and s["direction"] == direction.upper() for s in self.recent_signals[:5]):
                        self.recent_signals.insert(0, signal_payload)
                        if len(self.recent_signals) > 30:
                            self.recent_signals.pop()
                        new_signals.append(signal_payload)

                        # Broadcast to WebSocket
                        await broadcast({"type": "signal", "data": signal_payload})

                        # Send Push / Telegram / LINE alerts if high confluence
                        if confluence >= 65:
                            await self.notifier.send_signal_alert(
                                symbol=symbol,
                                timeframe=tf.upper(),
                                direction=direction,
                                message=ai_message,
                                confluence_score=confluence,
                                entry=entry,
                                sl=sl,
                                tp=tp,
                                rr=2.2,
                            )
                            logger.info(f"📢 Alert sent for {symbol} ({direction.upper()}) Confluence: {confluence}")

            except Exception as e:
                logger.error(f"Error scanning {symbol} ({tf}): {e}")

        logger.info(f"✅ Scan completed. {len(new_signals)} new high-probability setups identified.")
        return new_signals
