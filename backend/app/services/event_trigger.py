"""Event Trigger & Proactive Market Monitor."""

import asyncio
from datetime import datetime
from loguru import logger

from app.engines.market_data import MarketDataEngine
from app.engines.smc_engine import SMCEngine
from app.engines.strategy_engine import StrategyEngine
from app.engines.ai_engine import AIEngine
from app.engines.risk_engine import RiskEngine
from app.services.notification import NotificationService
from app.api.ws import broadcast_signal


class MarketMonitor:
    """Proactively monitors watchlist symbols, checks SMC conditions on candle close,
    invokes Apex AI, and broadcasts signals via WebSocket & Push notifications.
    """

    def __init__(
        self,
        watchlist: list[dict],
        poll_interval_seconds: int = 60,
    ):
        self.watchlist = watchlist
        self.interval = poll_interval_seconds
        self.market_data = MarketDataEngine()
        self.smc = SMCEngine()
        self.strategy = StrategyEngine()
        self.ai = AIEngine()
        self.risk = RiskEngine()
        self.notifier = NotificationService()
        self.running = False

    async def start(self):
        self.running = True
        logger.info(f"MarketMonitor started with {len(self.watchlist)} symbols.")
        while self.running:
            try:
                await self._scan_all()
            except Exception as e:
                logger.error(f"Error in scan loop: {e}")
            await asyncio.sleep(self.interval)

    def stop(self):
        self.running = False
        logger.info("MarketMonitor stopped.")

    async def _scan_all(self):
        for item in self.watchlist:
            symbol = item["symbol"]
            tf = item.get("timeframe", "1h")
            htf = item.get("htf_timeframe", "4h")
            m_type = item.get("market_type", "crypto")
            ex = item.get("exchange", "binance")

            try:
                # 1. Fetch OHLCV
                ltf_df = await self.market_data.get_ohlcv(symbol, tf, m_type, ex, limit=200)
                htf_df = await self.market_data.get_ohlcv(symbol, htf, m_type, ex, limit=100)

                # 2. SMC Analysis
                htf_sig = self.smc.analyze(htf_df, symbol, htf)
                ltf_sig = self.smc.analyze(ltf_df, symbol, tf, htf_bias=htf_sig.htf_bias)

                # 3. Strategy evaluation
                strat_res = self.strategy.evaluate(ltf_sig)

                # Only trigger if valid setup is found
                if strat_res.signal != "no_trade":
                    logger.info(f"Setup detected for {symbol} ({tf}): {strat_res.signal.upper()}")
                    ai_res = await self.ai.analyze(ltf_sig)

                    # Compute RR & levels
                    entry = strat_res.suggested_entry
                    sl = strat_res.suggested_sl
                    tp = None
                    if entry and sl:
                        sl_dist = abs(entry - sl)
                        tp = entry + (sl_dist * strat_res.min_rr) if strat_res.signal == "long" else entry - (sl_dist * strat_res.min_rr)

                    signal_payload = {
                        "symbol": symbol,
                        "timeframe": tf,
                        "direction": strat_res.signal,
                        "confluence": ltf_sig.confluence_score,
                        "message": ai_res.message,
                        "entry": entry,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "rr": strat_res.min_rr,
                        "timestamp": datetime.utcnow().isoformat(),
                    }

                    # Broadcast WS
                    await broadcast_signal(signal_payload)

                    # Send notification alerts
                    await self.notifier.send_signal_alert(
                        symbol=symbol,
                        timeframe=tf,
                        direction=strat_res.signal,
                        message=ai_res.message,
                        confluence_score=ltf_sig.confluence_score,
                        entry=entry,
                        sl=sl,
                        tp=tp,
                        rr=strat_res.min_rr,
                    )

            except Exception as e:
                logger.error(f"Error scanning {symbol} ({tf}): {e}")
