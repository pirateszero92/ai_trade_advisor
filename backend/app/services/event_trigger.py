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

_ALERT_HISTORY: dict[str, float] = {}
_RUNTIME_CFG_CACHE: tuple[float, dict] = (0.0, {})


def _get_cached_runtime_settings() -> dict:
    import time, json
    from app.api.settings_api import RUNTIME_SETTINGS_FILE
    global _RUNTIME_CFG_CACHE
    now = time.time()
    last_ts, cached = _RUNTIME_CFG_CACHE
    if now - last_ts < 5.0 and cached:
        return cached
    if RUNTIME_SETTINGS_FILE.exists():
        try:
            data = json.loads(RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
            _RUNTIME_CFG_CACHE = (now, data)
            return data
        except Exception:
            pass
    return {"entry_mode": "limit", "auto_invalidation": True}


def invalidate_runtime_settings_cache() -> None:
    global _RUNTIME_CFG_CACHE
    _RUNTIME_CFG_CACHE = (0.0, {})


def _compact_symbol(s: str) -> str:
    """Match symbols across BTC/USDT, BTC-USDT, BTCUSDT."""
    return str(s or "").strip().upper().replace("-", "").replace("/", "").replace("_", "")


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
        if watchlist is not None:
            self.watchlist = watchlist
        else:
            cfg = _get_cached_runtime_settings()
            saved_wl = cfg.get("watchlist")
            # Empty list is a valid user choice — only seed defaults when unset.
            if isinstance(saved_wl, list):
                self.watchlist = list(saved_wl)
            else:
                self.watchlist = list(DEFAULT_WATCHLIST)
                try:
                    from app.api.settings_api import _save_runtime_watchlist
                    _save_runtime_watchlist(self.watchlist)
                except Exception:
                    pass
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
        self._scan_task = asyncio.create_task(self._run_scan_loop())
        self._pos_task = asyncio.create_task(self._run_position_monitor_loop())

    def stop(self):
        self.running = False
        if hasattr(self, "_scan_task") and self._scan_task:
            self._scan_task.cancel()
        if hasattr(self, "_pos_task") and self._pos_task:
            self._pos_task.cancel()
        logger.info("🛑 MarketMonitor stopped.")

    async def _run_scan_loop(self):
        while self.running:
            try:
                await self.scan_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in proactive scan loop: {e}")
            await asyncio.sleep(self.interval)

    async def _run_position_monitor_loop(self):
        """Fast high-frequency loop checking open positions TP/SL, Breakeven Shield, and live PnL every 1.5s."""
        while self.running:
            try:
                await self._check_open_positions_tp_sl()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in position monitor loop: {e}")
            await asyncio.sleep(1.5)

    async def _check_open_positions_tp_sl(self):
        """Auto-monitor open positions and execute TP/SL exits, Pending limit fills & Auto-Breakeven shields automatically."""
        try:
            from app.api.trades import get_all_trades, auto_close_trade_sync, update_trade_sl_sync, _save_trades
            trades_dict = get_all_trades()

            # 0. Check pending limit orders first
            pending_trades = [t for t in trades_dict.values() if t.get("status") == "pending"]
            for ptrade in pending_trades:
                psym = ptrade["symbol"]
                pdir = ptrade.get("direction", "long").lower()
                pentry = float(ptrade.get("entry", 0.0))
                pid = ptrade.get("id")
                psup = psym.upper().replace("/", "").replace("-", "")
                pmtype = "forex" if any(f in psup for f in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]) else ("crypto" if "/" in psym or "USDT" in psup or "THB" in psup else "stock")
                try:
                    pticker = await asyncio.wait_for(self.market_data.get_ticker_24h(psym, pmtype), timeout=1.5)
                    pprice = float(pticker.get("price", 0.0))
                    if pprice > 0:
                        # Check if limit touched
                        filled = False
                        if pdir == "long" and pprice <= pentry:
                            filled = True
                        elif pdir == "short" and pprice >= pentry:
                            filled = True
                        if filled:
                            ptrade["status"] = "open"
                            ptrade["filled_at"] = time.time()
                            _save_trades()
                            logger.info(f"⚡ LIMIT ORDER FILLED: {psym} {ptrade.get('tag', pid)} filled at ${pprice:.2f}")
                            await broadcast({"type": "trade_updated", "data": ptrade})
                except Exception:
                    pass

            open_trades = [t for t in trades_dict.values() if t.get("status") == "open"]
            if not open_trades:
                return

            for trade in open_trades:
                sym = trade["symbol"]
                dir_ = trade.get("direction", "long").lower()
                entry = float(trade.get("entry", 0.0))
                sl = float(trade.get("stop_loss", 0.0))
                tp = float(trade.get("take_profit", 0.0))
                trade_id = trade.get("id")

                if entry <= 0:
                    continue

                sl_dist = abs(entry - sl) if sl > 0 else 0.0

                # Detect correct market type
                s_up = sym.upper().replace("/", "").replace("-", "")
                if any(f in s_up for f in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "AUDUSD", "USDCAD"]):
                    m_type = "forex"
                elif "/" in sym or "USDT" in s_up or "THB" in s_up:
                    m_type = "crypto"
                else:
                    m_type = "stock"

                # Get latest live ticker price & extreme candle high/low for wick guard
                current_price = 0.0
                high_price = 0.0
                low_price = 0.0
                try:
                    ticker_data = await asyncio.wait_for(self.market_data.get_ticker_24h(sym, m_type), timeout=1.5)
                    current_price = float(ticker_data.get("price", 0.0))
                except Exception:
                    pass

                if current_price <= 0.0:
                    df = await self.market_data.get_ohlcv(sym, "1m", m_type, limit=5)
                    if not df.empty:
                        current_price = float(df["close"].iloc[-1])
                        high_price = float(df["high"].iloc[-1])
                        low_price = float(df["low"].iloc[-1])

                if current_price <= 0.0:
                    continue

                if high_price <= 0.0:
                    high_price = current_price
                if low_price <= 0.0:
                    low_price = current_price

                # -------------------------------------------------------------
                # 1. Multi-Tier Dynamic Trailing Stop & Auto-Breakeven Engine
                # -------------------------------------------------------------
                init_sl_dist = float(trade.get("initial_sl_dist", abs(entry - sl)))
                if init_sl_dist <= 0 and entry > 0:
                    init_sl_dist = entry * 0.01

                if init_sl_dist > 0:
                    # Update highest / lowest peak price achieved so far
                    if dir_ == "long":
                        prev_high = float(trade.get("highest_price", entry))
                        curr_high = max(prev_high, current_price, high_price)
                        if curr_high > prev_high:
                            trade["highest_price"] = curr_high

                        r_multiple = (curr_high - entry) / init_sl_dist if init_sl_dist > 0 else 0.0
                        new_sl = sl
                        note = ""

                        # Tier 4: Peak Dynamic Trail (at >= 2.5R) -> Trail behind high by 0.8R
                        if r_multiple >= 2.5:
                            cand_sl = round(curr_high - (init_sl_dist * 0.8), 6)
                            if cand_sl > sl:
                                new_sl = cand_sl
                                note = f"💎 Dynamic Trailing Stop (+{r_multiple:.1f}R Peak)"
                        # Tier 3: Lock +1.2R Profit (at >= 2.0R)
                        elif r_multiple >= 2.0:
                            cand_sl = round(entry + (init_sl_dist * 1.2), 6)
                            if cand_sl > sl:
                                new_sl = cand_sl
                                note = "🚀 Trailing Stop (Locked +1.2R Profit)"
                        # Tier 2: Lock +0.6R Profit (at >= 1.5R)
                        elif r_multiple >= 1.5:
                            cand_sl = round(entry + (init_sl_dist * 0.6), 6)
                            if cand_sl > sl:
                                new_sl = cand_sl
                                note = "📈 Trailing Stop (Locked +0.6R Profit)"
                        # Tier 1: Auto Breakeven Shield (at >= 1.0R)
                        elif r_multiple >= 1.0 or (tp > entry and current_price >= entry + (tp - entry) * 0.5):
                            cand_sl = round(entry * 1.0005, 6)
                            if cand_sl > sl:
                                new_sl = cand_sl
                                note = "🛡️ Breakeven Shield (1.0R Reached)"

                        if new_sl > sl and note:
                            updated = update_trade_sl_sync(trade_id, new_sl, note)
                            if updated:
                                logger.info(f"📈 TRAILING SL UP: {sym} {trade.get('tag', trade_id)} SL moved to ${new_sl:.2f} [{note}]")
                                await broadcast({"type": "trade_updated", "data": updated})
                                sl = new_sl

                    else:  # short
                        prev_low = float(trade.get("lowest_price", entry))
                        curr_low = min(prev_low, current_price, low_price)
                        if curr_low < prev_low:
                            trade["lowest_price"] = curr_low

                        r_multiple = (entry - curr_low) / init_sl_dist if init_sl_dist > 0 else 0.0
                        new_sl = sl
                        note = ""

                        # Tier 4: Peak Dynamic Trail (at >= 2.5R) -> Trail above low by 0.8R
                        if r_multiple >= 2.5:
                            cand_sl = round(curr_low + (init_sl_dist * 0.8), 6)
                            if cand_sl < sl or sl <= 0:
                                new_sl = cand_sl
                                note = f"💎 Dynamic Trailing Stop (+{r_multiple:.1f}R Peak)"
                        # Tier 3: Lock +1.2R Profit (at >= 2.0R)
                        elif r_multiple >= 2.0:
                            cand_sl = round(entry - (init_sl_dist * 1.2), 6)
                            if cand_sl < sl or sl <= 0:
                                new_sl = cand_sl
                                note = "🚀 Trailing Stop (Locked +1.2R Profit)"
                        # Tier 2: Lock +0.6R Profit (at >= 1.5R)
                        elif r_multiple >= 1.5:
                            cand_sl = round(entry - (init_sl_dist * 0.6), 6)
                            if cand_sl < sl or sl <= 0:
                                new_sl = cand_sl
                                note = "📈 Trailing Stop (Locked +0.6R Profit)"
                        # Tier 1: Auto Breakeven Shield (at >= 1.0R)
                        elif r_multiple >= 1.0 or (tp < entry and current_price <= entry - (entry - tp) * 0.5):
                            cand_sl = round(entry * 0.9995, 6)
                            if cand_sl < sl or sl <= 0:
                                new_sl = cand_sl
                                note = "🛡️ Breakeven Shield (1.0R Reached)"

                        if (new_sl < sl or sl <= 0) and note:
                            updated = update_trade_sl_sync(trade_id, new_sl, note)
                            if updated:
                                logger.info(f"📉 TRAILING SL DOWN: {sym} {trade.get('tag', trade_id)} SL moved to ${new_sl:.2f} [{note}]")
                                await broadcast({"type": "trade_updated", "data": updated})
                                sl = new_sl

                # -------------------------------------------------------------
                # 2. Check TP / SL Hit (Checking live price and extreme wick)
                # -------------------------------------------------------------
                hit_reason = None
                exit_price = current_price
                if dir_ == "long":
                    if tp > 0 and (current_price >= tp or high_price >= tp):
                        hit_reason = "Take Profit (TP Hit) 🎯"
                        exit_price = tp
                    elif sl > 0 and (current_price <= sl or low_price <= sl):
                        if sl >= entry:
                            hit_reason = "Trailing Stop (Profit Protected) 📈" if sl > entry * 1.002 else "Breakeven Exit 🛡️"
                        else:
                            hit_reason = "Stop Loss (SL Hit) 🛑"
                        exit_price = sl
                else:
                    if tp > 0 and (current_price <= tp or low_price <= tp):
                        hit_reason = "Take Profit (TP Hit) 🎯"
                        exit_price = tp
                    elif sl > 0 and (current_price >= sl or high_price >= sl):
                        if sl <= entry:
                            hit_reason = "Trailing Stop (Profit Protected) 📈" if sl < entry * 0.998 else "Breakeven Exit 🛡️"
                        else:
                            hit_reason = "Stop Loss (SL Hit) 🛑"
                        exit_price = sl

                if hit_reason:
                    closed = auto_close_trade_sync(trade_id, hit_reason, exit_price)
                    if closed:
                        pnl = closed.get("pnl", 0.0)
                        pnl_pct = closed.get("pnl_pct", 0.0)
                        logger.info(f"⚡ AUTO EXIT: {sym} {closed.get('tag', trade_id)} closed by {hit_reason} at ${exit_price:.2f} (PnL: ${pnl:.2f})")
                        await broadcast({"type": "trade_closed", "data": closed})
                        await self.notifier.send_signal_alert(
                            symbol=sym,
                            timeframe="1M",
                            direction="closed",
                            message=f"[{hit_reason}] Position {closed.get('tag', sym)} ปิดสถานะอัตโนมัติที่ราคา ${exit_price:.2f} | Realized PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)",
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

            # Load user-configured risk & entry settings
            r_cfg = _get_cached_runtime_settings()
            entry_mode = r_cfg.get("entry_mode", "limit")
            auto_invalidation = r_cfg.get("auto_invalidation", True)

            ltf_sig = self.smc.analyze(ltf_df, symbol, tf, htf_bias=htf_bias, entry_mode=entry_mode)

            # 3. Strategy evaluation
            strat_res = self.strategy.evaluate(ltf_sig)
            confluence = ltf_sig.confluence_score

            # Determine smart SMC setup direction
            direction = "wait"
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
            elif ltf_sig.direction in ("long", "short"):
                direction = ltf_sig.direction

            live_price = float(ltf_sig.current_price) if ltf_sig.current_price > 0 else float(ltf_df["close"].iloc[-1])

            # 4. Auto Invalidation Cut-Loss for Open Trades
            if auto_invalidation:
                from app.api.trades import get_all_trades, auto_close_trade_sync
                all_t = get_all_trades()
                for t in all_t.values():
                    if t.get("status") == "open" and t.get("symbol") == symbol:
                        t_dir = str(t.get("direction", "")).lower()
                        t_entry = float(t.get("entry", live_price))
                        # Confirmed opposite structure (score >= 65) with CHoCH reversal
                        if t_dir == "short" and (direction == "long" and ltf_sig.choch and confluence >= 65):
                            is_profit = live_price < t_entry
                            reason_msg = "Opposite Signal Early TP (Bullish Reversal) 🎯" if is_profit else "Structure Invalidation (Market turned Bullish) ⚠️"
                            closed = auto_close_trade_sync(t["id"], reason_msg, live_price)
                            if closed:
                                pnl_val = closed.get("pnl", 0.0)
                                logger.info(f"⚡ AUTO EXIT: {symbol} Short position closed due to confirmed bullish reversal (confluence={confluence}, PnL=${pnl_val:+.2f})")
                                await broadcast({"type": "trade_closed", "data": closed})
                                await self.notifier.send_signal_alert(
                                    symbol=symbol,
                                    timeframe=tf.upper(),
                                    direction="closed",
                                    message=f"[{reason_msg}] ปิดสถานะ SHORT {symbol} อัตโนมัติเนื่องจากโครงสร้างตลาดกลับตัวเป็น BULLISH (Confluence {confluence}/100) | Realized PnL: ${pnl_val:+.2f}",
                                    confluence_score=confluence,
                                    entry=t.get("entry", 0),
                                    sl=t.get("stop_loss", 0),
                                    tp=t.get("take_profit", 0),
                                    exit_price=live_price,
                                )
                        # If open long and new confirmed structure is strong bearish with CHoCH
                        elif t_dir == "long" and (direction == "short" and ltf_sig.choch and confluence >= 65):
                            is_profit = live_price > t_entry
                            reason_msg = "Opposite Signal Early TP (Bearish Reversal) 🎯" if is_profit else "Structure Invalidation (Market turned Bearish) ⚠️"
                            closed = auto_close_trade_sync(t["id"], reason_msg, live_price)
                            if closed:
                                pnl_val = closed.get("pnl", 0.0)
                                logger.info(f"⚡ AUTO EXIT: {symbol} Long position closed due to confirmed bearish reversal (confluence={confluence}, PnL=${pnl_val:+.2f})")
                                await broadcast({"type": "trade_closed", "data": closed})
                                await self.notifier.send_signal_alert(
                                    symbol=symbol,
                                    timeframe=tf.upper(),
                                    direction="closed",
                                    message=f"[{reason_msg}] ปิดสถานะ LONG {symbol} อัตโนมัติเนื่องจากโครงสร้างตลาดกลับตัวเป็น BEARISH (Confluence {confluence}/100) | Realized PnL: ${pnl_val:+.2f}",
                                    confluence_score=confluence,
                                    entry=t.get("entry", 0),
                                    sl=t.get("stop_loss", 0),
                                    tp=t.get("take_profit", 0),
                                    exit_price=live_price,
                                )

            # 5. Calculate entry, SL, TP based on entry_mode
            if entry_mode == "limit":
                if direction == "long":
                    if ltf_sig.order_block and ltf_sig.order_block.direction == "bullish":
                        entry = ltf_sig.order_block.mid
                        sl = ltf_sig.order_block.bottom * 0.998
                    elif ltf_sig.fvg and ltf_sig.fvg.direction == "bullish":
                        entry = ltf_sig.fvg.mid
                        sl = ltf_sig.fvg.bottom * 0.998
                    else:
                        entry = live_price
                        sl = entry * 0.992
                    if sl >= entry:
                        sl = entry * 0.992
                    sl_dist = abs(entry - sl)
                    tp = entry + (sl_dist * 2.5)
                else:
                    if ltf_sig.order_block and ltf_sig.order_block.direction == "bearish":
                        entry = ltf_sig.order_block.mid
                        sl = ltf_sig.order_block.top * 1.002
                    elif ltf_sig.fvg and ltf_sig.fvg.direction == "bearish":
                        entry = ltf_sig.fvg.mid
                        sl = ltf_sig.fvg.top * 1.002
                    else:
                        entry = live_price
                        sl = entry * 1.008
                    if sl <= entry:
                        sl = entry * 1.008
                    sl_dist = abs(entry - sl)
                    tp = entry - (sl_dist * 2.5)
            else:  # market entry
                entry = live_price
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
            
            # Tailored structure description with Quantitative Multi-Layer details
            entry_type_label = "Limit Zone (OB/FVG)" if entry_mode == "limit" else "Market Price"
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

            if ltf_sig.delta_absorption:
                structure_summary += " | 🌊 Smart Money Absorption ยืนยัน"
            elif ltf_sig.volume_spike:
                structure_summary += " | 📊 Volume Spike"

            if ltf_sig.squeeze_status == "squeeze_fire":
                structure_summary += " | ⚡ Squeeze Fired"

            price_decimals = 4 if entry < 5.0 else 2
            if confluence >= 80:
                advice_text = f"คำแนะนำ: โครงสร้างแข็งแกร่ง (Grade A+) สอดคล้องเทรนด์ใหญ่ + Volume Delta แนะนำเข้าตามแผน {entry_type_label} Entry ${entry:.{price_decimals}f} SL ${sl:.{price_decimals}f} (ความเสี่ยง 1.0%)"
            elif confluence >= 65:
                advice_text = f"คำแนะนำ: โครงสร้าง {direction.upper()} (Grade B) แตะโซน {zone_name} ควรรอแท่งยืนยัน Rejection ใน TF ย่อยก่อนเข้า หรือจำกัดความเสี่ยงที่ 0.5%"
            else:
                advice_text = 'คำแนะนำ: รอยืนยันการเคลื่อนไหวของราคา แนะนำ "รอ (WAIT)" สัญญาณ CHoCH หรือ Squeeze Release ก่อน'

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
                "rr": 2.5 if entry_mode == "limit" else 2.2,
                "entry_type": entry_mode,
                "live_price": round(live_price, price_decimals),
                "squeeze_status": ltf_sig.squeeze_status,
                "squeeze_momentum": ltf_sig.squeeze_momentum,
                "momentum_direction": ltf_sig.momentum_direction,
                "volume_delta": ltf_sig.volume_delta,
                "delta_ratio": ltf_sig.delta_ratio,
                "delta_absorption": ltf_sig.delta_absorption,
                "delta_status": ltf_sig.delta_status,
                "volume_spike": ltf_sig.volume_spike,
                "message": structure_summary,
                "advice": advice_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Broadcast to WebSocket
            await broadcast({"type": "signal", "data": signal_payload})

            # Send Push / Telegram / LINE alerts if high confluence with 30-min debounce cooldown
            if confluence >= 65 and direction in ("long", "short"):
                alert_key = f"{symbol}:{direction.upper()}"
                now_ts = asyncio.get_event_loop().time()
                last_alert_ts = _ALERT_HISTORY.get(alert_key, 0.0)

                if now_ts - last_alert_ts > 1800.0:  # 30-min cooldown
                    _ALERT_HISTORY[alert_key] = now_ts
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
                else:
                    logger.debug(f"Alert for {alert_key} skipped due to 30m cooldown")

            return signal_payload

        except Exception as e:
            logger.error(f"Error scanning {symbol} ({tf}): {e}")
            return None

    async def scan_all(self) -> list[dict]:
        """Scan only Settings watchlist symbols with bounded concurrency."""
        self.last_scan_time = datetime.now(timezone.utc).isoformat()
        if not self.watchlist:
            self.recent_signals = []
            logger.info("🔍 Proactive Scanner skipped — Settings watchlist is empty.")
            return []

        logger.info(f"🔍 Proactive Scanner scanning {len(self.watchlist)} Settings watchlist symbols...")
        
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

        # Keep at most one signal per Settings-watchlist symbol
        active_norm_symbols = {
            _compact_symbol(w.get("symbol", ""))
            for w in self.watchlist if w.get("symbol")
        }
        signal_map = {
            _compact_symbol(s.get("symbol", "")): s for s in self.recent_signals
            if _compact_symbol(s.get("symbol", "")) in active_norm_symbols
        }
        for s in new_signals:
            sym_norm = _compact_symbol(s.get("symbol", ""))
            if sym_norm in active_norm_symbols:
                signal_map[sym_norm] = s
        
        # Sort by confluence score descending (highest quality setups first)
        self.recent_signals = sorted(
            signal_map.values(),
            key=lambda x: (x.get("confluence", 0), x.get("symbol", "")),
            reverse=True,
        )

        logger.info(f"✅ Parallel scan completed. {len(self.recent_signals)} active watchlist symbols monitored.")
        return self.recent_signals
