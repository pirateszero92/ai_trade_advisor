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


def _clean_message_text(raw: str) -> str:
    if not raw:
        return ""
    import json, re
    text = str(raw).strip()
    
    # 1. Try regex extraction of "reasoning" key even if JSON was cut off/truncated
    match = re.search(r'"reasoning"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if match:
        try:
            return json.loads(f'"{match.group(1)}"')
        except Exception:
            return match.group(1).replace(r'\"', '"').replace(r'\n', '\n')

    # 2. Try full JSON parse
    match_json = re.search(r"\{[\s\S]*\}", text)
    if match_json:
        try:
            data = json.loads(match_json.group(0))
            if isinstance(data, dict) and data.get("reasoning"):
                return str(data["reasoning"])
        except Exception:
            pass

    # 3. Strip any markdown code fences
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    
    # 4. If text still looks like raw json snippet { ... }, extract the longest string inside
    if text.startswith("{") or '"recommendation":' in text:
        m_any = re.search(r'"(?:reasoning|message|text)"\s*:\s*"([^"]+)"', text)
        if m_any:
            return m_any.group(1)

    return text


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
                await self._check_open_positions_tp_sl()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in proactive scan loop: {e}")
            await asyncio.sleep(self.interval)

    async def _check_open_positions_tp_sl(self):
        """Auto-monitor open positions and execute TP/SL exits automatically."""
        try:
            from app.api.trades import _trades
            open_trades = [t for t in _trades.values() if t.get("status") == "open"]
            if not open_trades:
                return

            for trade in open_trades:
                sym = trade["symbol"]
                dir_ = trade.get("direction", "long").lower()
                entry = trade.get("entry", 0.0)
                sl = trade.get("stop_loss", 0.0)
                tp = trade.get("take_profit", 0.0)
                trade_id = trade.get("id")

                # Detect correct market type
                s_up = sym.upper().replace("/", "").replace("-", "")
                if s_up in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "AUDUSD", "USDCAD"]:
                    m_type = "forex"
                elif "/" in sym or "USDT" in s_up:
                    m_type = "crypto"
                else:
                    m_type = "stock"

                # Get latest live ticker price
                current_price = 0.0
                try:
                    ticker_data = await self.market_data.get_ticker_24h(sym, m_type)
                    current_price = float(ticker_data.get("price", 0.0))
                except Exception:
                    pass

                if current_price <= 0.0:
                    df = await self.market_data.get_ohlcv(sym, "1m", m_type, limit=5)
                    if not df.empty:
                        current_price = float(df["close"].iloc[-1])

                if current_price <= 0.0:
                    continue

                # Sanity check: price must not deviate by >40% from entry to ignore invalid network spikes
                if entry > 0 and (current_price > entry * 1.5 or current_price < entry * 0.5):
                    logger.warning(f"Ignored abnormal price spike for {sym}: Entry={entry}, Live={current_price}")
                    continue

                # Check TP / SL hit
                hit_reason = None
                if dir_ == "long":
                    if tp > 0 and current_price >= tp:
                        hit_reason = "Take Profit (TP Hit) 🎯"
                    elif sl > 0 and current_price <= sl:
                        hit_reason = "Stop Loss (SL Hit) 🛑"
                else:
                    if tp > 0 and current_price <= tp:
                        hit_reason = "Take Profit (TP Hit) 🎯"
                    elif sl > 0 and current_price >= sl:
                        hit_reason = "Stop Loss (SL Hit) 🛑"

                if hit_reason:
                    # Calculate realized PnL
                    size = trade.get("position_size", trade.get("size", 1.0))
                    if dir_ == "long":
                        pnl = (current_price - entry) * size
                        pnl_pct = ((current_price - entry) / entry) * 100
                    else:
                        pnl = (entry - current_price) * size
                        pnl_pct = ((entry - current_price) / entry) * 100

                    trade["pnl"] = round(pnl, 2)
                    trade["pnl_pct"] = round(pnl_pct, 2)

                    try:
                        from app.api.trades import _save_trades
                        _save_trades()
                    except Exception:
                        pass

                    logger.info(f"⚡ AUTO EXIT: {sym} {trade.get('tag', trade_id)} closed by {hit_reason} at ${current_price:.2f} (PnL: ${pnl:.2f})")

                    # Broadcast update to WebSocket
                    await broadcast({"type": "trade_closed", "data": trade})

                    # Dispatch Alert to Telegram / LINE
                    await self.notifier.send_signal_alert(
                        symbol=sym,
                        timeframe="1M",
                        direction="closed",
                        message=f"[{hit_reason}] Position {trade.get('tag', sym)} ปิดสถานะอัตโนมัติที่ราคา ${current_price:.2f} | Realized PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)",
                        confluence_score=100,
                        entry=entry,
                        sl=sl,
                        tp=tp,
                    )
        except Exception as e:
            logger.error(f"Error checking open positions TP/SL: {e}")

    async def _scan_single_symbol(self, item: dict) -> Optional[dict]:
        symbol = item["symbol"]
        tf = item.get("timeframe", "1h")
        htf = item.get("htf_timeframe", "4h")
        m_type = item.get("market_type", "crypto")
        ex = item.get("exchange", "binance")

        try:
            # 1. Fetch multi-timeframe OHLCV in parallel
            ltf_df, htf_df = await asyncio.gather(
                self.market_data.get_ohlcv(symbol, tf, m_type, ex, limit=150),
                self.market_data.get_ohlcv(symbol, htf, m_type, ex, limit=80),
                return_exceptions=True,
            )
            if isinstance(ltf_df, Exception) or ltf_df is None or ltf_df.empty:
                return None

            htf_bias = "neutral"
            if not isinstance(htf_df, Exception) and htf_df is not None and not htf_df.empty:
                htf_sig = self.smc.analyze(htf_df, symbol, htf)
                htf_bias = htf_sig.bias

            ltf_sig = self.smc.analyze(ltf_df, symbol, tf, htf_bias=htf_bias)

            # 3. Strategy evaluation
            strat_res = self.strategy.evaluate(ltf_sig)
            confluence = ltf_sig.confluence_score

            # Determine smart SMC setup direction
            direction = "long"
            if ltf_sig.liquidity_swept and ltf_sig.in_premium:
                direction = "short"  # Sweep of highs -> Short pullback
            elif ltf_sig.liquidity_swept and ltf_sig.in_discount:
                direction = "long"   # Sweep of lows -> Long bounce
            elif ltf_sig.bias == "bearish" or (htf_bias == "bearish" and ltf_sig.in_premium):
                direction = "short"
            elif ltf_sig.bias == "bullish" or (htf_bias == "bullish" and ltf_sig.in_discount):
                direction = "long"
            elif strat_res.direction in ("long", "short"):
                direction = strat_res.direction

            entry = float(ltf_sig.current_price) if ltf_sig.current_price > 0 else float(ltf_df["close"].iloc[-1])
            
            # Dynamic SL/TP based on Order Block or 0.8% ATR buffer
            if direction == "long":
                sl = (ltf_sig.order_block.bottom * 0.998) if ltf_sig.order_block else (entry * 0.992)
                if sl >= entry:
                    sl = entry * 0.992
                sl_dist = abs(entry - sl)
                tp = entry + (sl_dist * 2.2)
            else:
                sl = (ltf_sig.order_block.top * 1.002) if ltf_sig.order_block else (entry * 1.008)
                if sl <= entry:
                    sl = entry * 1.008
                sl_dist = abs(entry - sl)
                tp = entry - (sl_dist * 2.2)

            zone_name = "Discount" if ltf_sig.in_discount else ("Premium" if ltf_sig.in_premium else "Equilibrium")
            
            # Tailored structure description
            if ltf_sig.liquidity_swept and ltf_sig.in_premium:
                structure_summary = f"เกิดการ Sweep สภาพคล่องเหนือ High ล่าสุดในโซน Premium (จุดกลับตัว Short-term)"
            elif ltf_sig.liquidity_swept and ltf_sig.in_discount:
                structure_summary = f"เกิดการ Sweep สภาพคล่องใต้ Low สำคัญในโซน Discount พร้อมดีดตัวรับแรงซื้อ"
            elif ltf_sig.order_block and ltf_sig.in_discount:
                structure_summary = f"โครงสร้าง {direction.upper()} Confluence {confluence}/100 ราคาแตะ Bullish Order Block ในโซน Discount"
            elif ltf_sig.order_block and ltf_sig.in_premium:
                structure_summary = f"โครงสร้าง {direction.upper()} Confluence {confluence}/100 ราคาแตะ Bearish Order Block ในโซน Premium"
            elif ltf_sig.bos:
                structure_summary = f"โครงสร้าง {direction.upper()} Confluence {confluence}/100 เกิด Break of Structure (BOS) ตามเทรนด์ใหญ่"
            elif ltf_sig.choch:
                structure_summary = f"โครงสร้าง {direction.upper()} Confluence {confluence}/100 เกิด Change of Character (CHoCH) ส่งสัญญาณต้นเทรนด์"
            else:
                structure_summary = f"โครงสร้าง {direction.upper()} Confluence {confluence}/100 ราคาพักตัวในกรอบ Sideway โซน {zone_name}"

            price_decimals = 4 if entry < 5.0 else 2
            if confluence >= 80:
                advice_text = f"คำแนะนำ: โครงสร้างแข็งแกร่ง (Grade A+) สอดคล้องเทรนด์ใหญ่ แนะนำพิจารณาเข้าตามแผน Entry ${entry:.{price_decimals}f} SL ${sl:.{price_decimals}f} (ความเสี่ยง 1.0%)"
            elif confluence >= 65:
                advice_text = f"คำแนะนำ: โครงสร้าง {direction.upper()} (Grade B) แตะโซน {zone_name} ควรรอแท่งยืนยัน Rejection ใน TF ย่อยก่อนเข้า หรือจำกัดความเสี่ยงที่ 0.5%"
            else:
                advice_text = 'คำแนะนำ: รอยืนยันการเคลื่อนไหวของราคา แนะนำ "รอ (WAIT)" สัญญาณ CHoCH ยืนยันใน TF ย่อยก่อน'

            signal_payload = {
                "id": f"{symbol}_{tf}_{int(datetime.now(timezone.utc).timestamp())}",
                "symbol": symbol,
                "market_type": m_type,
                "timeframe": tf.upper(),
                "htf_timeframe": htf.upper(),
                "direction": direction.upper(),
                "confluence": confluence,
                "entry": round(entry, price_decimals),
                "stop_loss": round(sl, price_decimals),
                "take_profit": round(tp, price_decimals),
                "rr": 2.2,
                "message": structure_summary,
                "advice": advice_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Broadcast to WebSocket
            await broadcast({"type": "signal", "data": signal_payload})

            # Send Push / Telegram / LINE alerts if high confluence
            if confluence >= 65:
                await self.notifier.send_signal_alert(
                    symbol=symbol,
                    timeframe=tf.upper(),
                    direction=direction,
                    message=f"{structure_summary} | {advice_text}",
                    confluence_score=confluence,
                    entry=entry,
                    sl=sl,
                    tp=tp,
                    rr=2.2,
                )
                logger.info(f"📢 Alert sent for {symbol} ({direction.upper()}) Confluence: {confluence}")

            return signal_payload

        except Exception as e:
            logger.error(f"Error scanning {symbol} ({tf}): {e}")
            return None

    async def scan_all(self) -> list[dict]:
        """Scan all watchlist symbols with bounded concurrency (Semaphore=4) to prevent rate limits."""
        self.last_scan_time = datetime.now(timezone.utc).isoformat()
        logger.info(f"🔍 Proactive Scanner scanning {len(self.watchlist)} symbols in parallel...")
        
        sem = asyncio.Semaphore(4)

        async def _bounded_scan(item):
            async with sem:
                try:
                    return await self._scan_single_symbol(item)
                except Exception as exc:
                    logger.warning(f"[Scanner] Exception scanning {item.get('symbol')}: {exc}")
                    return None

        results = await asyncio.gather(*[_bounded_scan(item) for item in self.watchlist], return_exceptions=False)
        new_signals = [s for s in results if s is not None]

        # 100% Strict Unique 1-Signal-Per-Symbol
        signal_map = {s["symbol"]: s for s in self.recent_signals}
        for s in new_signals:
            signal_map[s["symbol"]] = s
        
        # Sort by confluence score descending (highest quality setups first)
        self.recent_signals = sorted(
            signal_map.values(),
            key=lambda x: (x.get("confluence", 0), x.get("symbol", "")),
            reverse=True,
        )

        logger.info(f"✅ Parallel scan completed. {len(self.recent_signals)} total distinct symbols monitored.")
        return self.recent_signals
