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
from app.services.mtf_analysis import mtf_analyses

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
    import time
    from app.core.runtime_config import load_runtime_config
    global _RUNTIME_CFG_CACHE
    now = time.time()
    last_ts, cached = _RUNTIME_CFG_CACHE
    if now - last_ts < 5.0 and cached:
        return cached
    try:
        data = load_runtime_config()
        _RUNTIME_CFG_CACHE = (now, data)
        return data
    except Exception as exc:
        logger.error(f"Unable to load runtime settings: {exc}")
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


def _confirmed_invalidation_direction(signal) -> str:
    """Return a direction only for a risk-reducing opposite-CHoCH exit.

    Exit safety is intentionally independent of the new-entry regime policy:
    compression or unknown regimes may block exposure, but must never block a
    confirmed reduction of an existing Paper position.
    """
    indicator_decision = getattr(signal, "indicator_decision", {})
    if not isinstance(indicator_decision, dict) or not indicator_decision.get("ready", False):
        return "wait"
    direction = getattr(signal, "direction", "wait")
    confluence = getattr(signal, "confluence_score", 0)
    if direction not in ("long", "short") or not getattr(signal, "choch", False):
        return "wait"
    return direction if confluence >= 65 else "wait"


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
        self._scan_lock = asyncio.Lock()
        self._position_lock = asyncio.Lock()
        self._quote_fallback_at: dict[str, float] = {}
        self._position_extremes: dict[str, tuple[float, float]] = {}

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

    async def stop(self):
        self.running = False
        tasks = [
            task for task in (
                getattr(self, "_scan_task", None),
                getattr(self, "_pos_task", None),
            ) if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
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
        if self._position_lock.locked():
            return
        async with self._position_lock:
            await self._check_positions_once()

    async def _check_positions_once(self):
        try:
            from app.api.trades import (
                auto_close_trade_sync,
                fill_pending_trade_sync,
                get_all_trades,
                update_trade_sl_sync,
            )
            from app.engines.price_hub import price_hub
            from app.services.paper_oms import PaperOMSError, paper_oms
            use_paper_oms = paper_oms.ready

            # Production Paper state is owned by the event-driven PostgreSQL
            # OMS. It handles limit fills, Auto-BE, trailing and TP/SL on the
            # same Price Hub tick; running this legacy polling path as well
            # would create two competing protection authorities.
            if use_paper_oms:
                return
            trades_dict = get_all_trades()

            # 0. Check pending limit orders first
            pending_trades = [
                t for t in trades_dict.values()
                if not use_paper_oms
                and t.get("status") == "pending"
                and t.get("mode", "paper") == "paper"
            ]
            for ptrade in pending_trades:
                psym = ptrade["symbol"]
                pdir = ptrade.get("direction", "long").lower()
                pentry = float(ptrade.get("entry", 0.0))
                pid = ptrade.get("id")
                psup = psym.upper().replace("/", "").replace("-", "")
                pmtype = "forex" if any(f in psup for f in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]) else ("crypto" if "/" in psym or "USDT" in psup or "THB" in psup else "stock")
                try:
                    price_hub.register_symbol(psym)
                    pprice = float(price_hub.get_price(psym) or 0.0)
                    if pprice <= 0:
                        fallback_key = f"pending:{psym}"
                        now_mono = asyncio.get_running_loop().time()
                        last_fallback = self._quote_fallback_at.get(fallback_key, 0.0)
                        if now_mono - last_fallback < 5.0:
                            continue
                        self._quote_fallback_at[fallback_key] = now_mono
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
                            filled_trade = fill_pending_trade_sync(pid, pprice)
                            if filled_trade:
                                logger.info(f"⚡ LIMIT ORDER FILLED: {psym} {ptrade.get('tag', pid)} filled at ${pprice:.2f}")
                                await broadcast({"type": "trade_updated", "data": filled_trade})
                except Exception as exc:
                    logger.debug(f"Pending order quote failed for {psym}: {exc}")

            # Reload so newly-filled orders are included and concurrently
            # cancelled orders are excluded.
            open_trades = [
                t for t in get_all_trades().values()
                if t.get("status") == "open" and t.get("mode", "paper") == "paper"
            ]
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
                    price_hub.register_symbol(sym)
                    current_price = float(price_hub.get_price(sym) or 0.0)
                    if current_price <= 0:
                        now_mono = asyncio.get_running_loop().time()
                        last_fallback = self._quote_fallback_at.get(sym, 0.0)
                        if now_mono - last_fallback < 5.0:
                            continue
                        self._quote_fallback_at[sym] = now_mono
                        ticker_data = await asyncio.wait_for(self.market_data.get_ticker_24h(sym, m_type), timeout=1.5)
                        current_price = float(ticker_data.get("price", 0.0))
                except Exception as exc:
                    logger.debug(f"Open-position quote failed for {sym}: {exc}")

                if current_price <= 0.0:
                    df = await self.market_data.get_ohlcv(sym, "1m", m_type, limit=5)
                    if not df.empty:
                        current_price = float(df["close"].iloc[-1])

                if current_price <= 0.0:
                    continue

                # A candle's high/low does not reveal whether TP or SL was hit
                # first and may include movement before this trade opened.  Use
                # sequential observed prices for deterministic paper exits.
                high_price = current_price
                low_price = current_price

                # -------------------------------------------------------------
                # 1. Multi-Tier Dynamic Trailing Stop & Auto-Breakeven Engine
                # -------------------------------------------------------------
                init_sl_dist = float(trade.get("initial_sl_dist", abs(entry - sl)))

                auto_be = bool(trade.get("auto_be", True))
                trailing_stop = bool(trade.get("trailing_stop", False))
                if init_sl_dist > 0 and (auto_be or trailing_stop):
                    # Update highest / lowest peak price achieved so far
                    if dir_ == "long":
                        prev_high = self._position_extremes.get(trade_id, (entry, entry))[0]
                        curr_high = max(prev_high, current_price, high_price)
                        self._position_extremes[trade_id] = (curr_high, self._position_extremes.get(trade_id, (entry, entry))[1])

                        r_multiple = (curr_high - entry) / init_sl_dist if init_sl_dist > 0 else 0.0
                        new_sl = sl
                        note = ""

                        # Tier 4: Peak Dynamic Trail (at >= 2.5R) -> Trail behind high by 0.8R
                        if trailing_stop and r_multiple >= 2.5:
                            cand_sl = round(curr_high - (init_sl_dist * 0.8), 6)
                            if cand_sl > sl:
                                new_sl = cand_sl
                                note = f"💎 Dynamic Trailing Stop (+{r_multiple:.1f}R Peak)"
                        # Tier 3: Lock +1.2R Profit (at >= 2.0R)
                        elif trailing_stop and r_multiple >= 2.0:
                            cand_sl = round(entry + (init_sl_dist * 1.2), 6)
                            if cand_sl > sl:
                                new_sl = cand_sl
                                note = "🚀 Trailing Stop (Locked +1.2R Profit)"
                        # Tier 2: Lock +0.6R Profit (at >= 1.5R)
                        elif trailing_stop and r_multiple >= 1.5:
                            cand_sl = round(entry + (init_sl_dist * 0.6), 6)
                            if cand_sl > sl:
                                new_sl = cand_sl
                                note = "📈 Trailing Stop (Locked +0.6R Profit)"
                        # Tier 1: Auto Breakeven Shield (at >= 1.0R)
                        elif auto_be and (r_multiple >= 1.0 or (tp > entry and current_price >= entry + (tp - entry) * 0.5)):
                            cand_sl = round(entry * 1.0005, 6)
                            if cand_sl > sl:
                                new_sl = cand_sl
                                note = "🛡️ Breakeven Shield (1.0R Reached)"

                        if new_sl > sl and note:
                            if use_paper_oms:
                                try:
                                    updated = await paper_oms.update_protection(
                                        trade_id, stop_loss=new_sl
                                    )
                                except PaperOMSError:
                                    updated = None
                            else:
                                updated = update_trade_sl_sync(trade_id, new_sl, note)
                            if updated:
                                logger.info(f"📈 TRAILING SL UP: {sym} {trade.get('tag', trade_id)} SL moved to ${new_sl:.2f} [{note}]")
                                if not use_paper_oms:
                                    await broadcast({"type": "trade_updated", "data": updated})
                                sl = new_sl

                    else:  # short
                        prev_low = self._position_extremes.get(trade_id, (entry, entry))[1]
                        curr_low = min(prev_low, current_price, low_price)
                        self._position_extremes[trade_id] = (self._position_extremes.get(trade_id, (entry, entry))[0], curr_low)

                        r_multiple = (entry - curr_low) / init_sl_dist if init_sl_dist > 0 else 0.0
                        new_sl = sl
                        note = ""

                        # Tier 4: Peak Dynamic Trail (at >= 2.5R) -> Trail above low by 0.8R
                        if trailing_stop and r_multiple >= 2.5:
                            cand_sl = round(curr_low + (init_sl_dist * 0.8), 6)
                            if cand_sl < sl or sl <= 0:
                                new_sl = cand_sl
                                note = f"💎 Dynamic Trailing Stop (+{r_multiple:.1f}R Peak)"
                        # Tier 3: Lock +1.2R Profit (at >= 2.0R)
                        elif trailing_stop and r_multiple >= 2.0:
                            cand_sl = round(entry - (init_sl_dist * 1.2), 6)
                            if cand_sl < sl or sl <= 0:
                                new_sl = cand_sl
                                note = "🚀 Trailing Stop (Locked +1.2R Profit)"
                        # Tier 2: Lock +0.6R Profit (at >= 1.5R)
                        elif trailing_stop and r_multiple >= 1.5:
                            cand_sl = round(entry - (init_sl_dist * 0.6), 6)
                            if cand_sl < sl or sl <= 0:
                                new_sl = cand_sl
                                note = "📈 Trailing Stop (Locked +0.6R Profit)"
                        # Tier 1: Auto Breakeven Shield (at >= 1.0R)
                        elif auto_be and (r_multiple >= 1.0 or (tp < entry and current_price <= entry - (entry - tp) * 0.5)):
                            cand_sl = round(entry * 0.9995, 6)
                            if cand_sl < sl or sl <= 0:
                                new_sl = cand_sl
                                note = "🛡️ Breakeven Shield (1.0R Reached)"

                        if (new_sl < sl or sl <= 0) and note:
                            if use_paper_oms:
                                try:
                                    updated = await paper_oms.update_protection(
                                        trade_id, stop_loss=new_sl
                                    )
                                except PaperOMSError:
                                    updated = None
                            else:
                                updated = update_trade_sl_sync(trade_id, new_sl, note)
                            if updated:
                                logger.info(f"📉 TRAILING SL DOWN: {sym} {trade.get('tag', trade_id)} SL moved to ${new_sl:.2f} [{note}]")
                                if not use_paper_oms:
                                    await broadcast({"type": "trade_updated", "data": updated})
                                sl = new_sl

                # -------------------------------------------------------------
                # 2. Check TP / SL Hit (Checking live price and extreme wick)
                # -------------------------------------------------------------
                hit_reason = None
                exit_price = current_price
                if dir_ == "long":
                    if tp > 0 and current_price >= tp:
                        hit_reason = "Take Profit (TP Hit) 🎯"
                        exit_price = tp
                    elif sl > 0 and current_price <= sl:
                        if sl >= entry:
                            hit_reason = "Trailing Stop (Profit Protected) 📈" if sl > entry * 1.002 else "Breakeven Exit 🛡️"
                        else:
                            hit_reason = "Stop Loss (SL Hit) 🛑"
                        exit_price = sl
                else:
                    if tp > 0 and current_price <= tp:
                        hit_reason = "Take Profit (TP Hit) 🎯"
                        exit_price = tp
                    elif sl > 0 and current_price >= sl:
                        if sl <= entry:
                            hit_reason = "Trailing Stop (Profit Protected) 📈" if sl < entry * 0.998 else "Breakeven Exit 🛡️"
                        else:
                            hit_reason = "Stop Loss (SL Hit) 🛑"
                        exit_price = sl

                if hit_reason:
                    if use_paper_oms:
                        try:
                            closed = await paper_oms.close_position(
                                trade_id,
                                close_price=exit_price,
                                reason=hit_reason,
                            )
                        except PaperOMSError:
                            closed = None
                    else:
                        closed = auto_close_trade_sync(trade_id, hit_reason, exit_price)
                    if closed:
                        self._position_extremes.pop(trade_id, None)
                        pnl = closed.get("pnl", 0.0)
                        pnl_pct = closed.get("pnl_pct", 0.0)
                        logger.info(f"⚡ AUTO EXIT: {sym} {closed.get('tag', trade_id)} closed by {hit_reason} at ${exit_price:.2f} (PnL: ${pnl:.2f})")
                        if not use_paper_oms:
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
        m_type = item.get("market_type", "crypto")
        ex = item.get("exchange", "binance")

        try:
            # Phase 5 owns the timeframe hierarchy. Watchlist timeframe fields
            # remain display defaults for older clients but cannot bypass the
            # ordered 4H Bias -> 1H Setup -> 15m Trigger gates.
            r_cfg = _get_cached_runtime_settings()
            entry_mode = r_cfg.get("entry_mode", "limit")
            auto_invalidation = r_cfg.get("auto_invalidation", True)
            mtf = await mtf_analyses.get(
                symbol=symbol,
                market_type=m_type,
                exchange=ex,
                entry_mode=entry_mode,
            )
            ltf_sig = mtf.trigger_signal
            ltf_df = mtf.frames["trigger"].copy()
            strat_res = mtf.strategy
            tf = mtf.stages["trigger"].timeframe
            setup_tf = mtf.stages["setup"].timeframe
            htf = mtf.stages["bias"].timeframe
            htf_bias = mtf.stages["bias"].signal.bias

            confluence = ltf_sig.confluence_score

            # StrategyEngine is the single approval gate. A rejected setup may
            # still be displayed as WAIT, but must never become an alert/trade.
            direction = strat_res.direction if strat_res.approved else "wait"
            exit_direction = _confirmed_invalidation_direction(ltf_sig)

            live_price = float(ltf_sig.current_price) if ltf_sig.current_price > 0 else float(ltf_df["close"].iloc[-1])

            # 4. Auto Invalidation Cut-Loss for Open Trades
            if auto_invalidation and exit_direction in ("long", "short"):
                from app.api.trades import get_all_trades, auto_close_trade_sync
                from app.services.paper_oms import PaperOMSError, paper_oms

                async def close_invalidated(trade_id: str, reason: str) -> Optional[dict]:
                    if paper_oms.ready:
                        try:
                            return await paper_oms.close_position(
                                trade_id,
                                close_price=live_price,
                                reason=reason,
                            )
                        except PaperOMSError:
                            return None
                    return auto_close_trade_sync(trade_id, reason, live_price)

                all_t = get_all_trades()
                for t in all_t.values():
                    if (
                        t.get("mode", "paper") == "paper"
                        and t.get("status") == "open"
                        and t.get("symbol") == symbol
                    ):
                        t_dir = str(t.get("direction", "")).lower()
                        t_entry = float(t.get("entry", live_price))
                        # Confirmed opposite structure (score >= 65) with CHoCH reversal
                        if t_dir == "short" and exit_direction == "long":
                            is_profit = live_price < t_entry
                            reason_msg = "Opposite Signal Early TP (Bullish Reversal) 🎯" if is_profit else "Structure Invalidation (Market turned Bullish) ⚠️"
                            closed = await close_invalidated(t["id"], reason_msg)
                            if closed:
                                pnl_val = closed.get("pnl", 0.0)
                                logger.info(f"⚡ AUTO EXIT: {symbol} Short position closed due to confirmed bullish reversal (confluence={confluence}, PnL=${pnl_val:+.2f})")
                                if not paper_oms.ready:
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
                        elif t_dir == "long" and exit_direction == "short":
                            is_profit = live_price > t_entry
                            reason_msg = "Opposite Signal Early TP (Bearish Reversal) 🎯" if is_profit else "Structure Invalidation (Market turned Bearish) ⚠️"
                            closed = await close_invalidated(t["id"], reason_msg)
                            if closed:
                                pnl_val = closed.get("pnl", 0.0)
                                logger.info(f"⚡ AUTO EXIT: {symbol} Long position closed due to confirmed bearish reversal (confluence={confluence}, PnL=${pnl_val:+.2f})")
                                if not paper_oms.ready:
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

            # 5. Reuse the volatility-aware setup produced by SMCEngine. This
            # avoids two calculators returning different entry/SL/TP values.
            if strat_res.approved:
                entry = float(ltf_sig.entry or 0.0)
                sl = float(ltf_sig.stop_loss or 0.0)
                tp = float(ltf_sig.take_profit or 0.0)
                setup_valid = (
                    entry > 0 and sl > 0 and tp > 0 and
                    ((direction == "long" and sl < entry < tp) or
                     (direction == "short" and tp < entry < sl))
                )
                if not setup_valid:
                    logger.warning(f"Rejected invalid SMC trade geometry for {symbol}: entry={entry}, sl={sl}, tp={tp}")
                    direction = "wait"
                    strat_res.approved = False
                    strat_res.rejection_reasons.append("Invalid entry/SL/TP geometry")
                    entry, sl, tp = live_price, 0.0, 0.0
            else:
                entry, sl, tp = live_price, 0.0, 0.0

            zone_name = "Discount" if ltf_sig.in_discount else ("Premium" if ltf_sig.in_premium else "Equilibrium")
            
            # Tailored structure description with Quantitative Multi-Layer details
            entry_type_label = "Limit Zone (OB/FVG)" if entry_mode == "limit" else "Market Price"
            if not strat_res.approved:
                reasons = "; ".join(strat_res.rejection_reasons[:3]) or "Strategy Gate rejected setup"
                structure_summary = f"WAIT — {reasons}"
            elif ltf_sig.liquidity_swept and ltf_sig.in_premium:
                structure_summary = "เกิดการ Sweep สภาพคล่องเหนือ High ล่าสุดในโซน Premium (จุดกลับตัว Short-term)"
            elif ltf_sig.liquidity_swept and ltf_sig.in_discount:
                structure_summary = "เกิดการ Sweep สภาพคล่องใต้ Low สำคัญในโซน Discount พร้อมดีดตัวรับแรงซื้อ"
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

            regime_data = ltf_sig.market_regime or {}
            regime_label = regime_data.get("label", "Unknown")
            regime_policy = regime_data.get("effective_policy") or regime_data.get("policy", {})
            structure_summary += f" | Regime: {regime_label}"

            price_decimals = 4 if entry < 5.0 else 2
            if not strat_res.approved:
                gate_reasons = "; ".join(strat_res.rejection_reasons[:3])
                advice_text = f'คำแนะนำ: รอ (WAIT) — {gate_reasons or "setup นี้ยังไม่ผ่าน Strategy Gate"}'
            elif confluence >= float(regime_policy.get("min_confluence", 65)) + 5:
                advice_text = f"คำแนะนำ: โครงสร้างผ่าน Strategy Gate ใน {regime_label} regime ให้พิจารณาแผน {entry_type_label} Entry ${entry:.{price_decimals}f} SL ${sl:.{price_decimals}f} และใช้ Risk ×{float(regime_policy.get('risk_multiplier', 1.0)):.2f} จาก Risk Engine"
            elif confluence >= float(regime_policy.get("min_confluence", 65)):
                advice_text = f"คำแนะนำ: โครงสร้าง {direction.upper()} ผ่านเกณฑ์ขั้นต่ำของ {regime_label} regime แต่ควรรอแท่งยืนยัน Rejection ใน TF ย่อยและให้ Risk Engine ตรวจขนาดก่อนเข้า"
            else:
                advice_text = 'คำแนะนำ: รอยืนยันการเคลื่อนไหวของราคา แนะนำ "รอ (WAIT)" สัญญาณ CHoCH หรือ Squeeze Release ก่อน'

            signal_payload = {
                "id": f"{symbol}_{tf}_{int(datetime.now(timezone.utc).timestamp())}",
                "symbol": symbol,
                "market_type": m_type,
                "exchange": ex,
                "timeframe": tf.upper(),
                "setup_timeframe": setup_tf.upper(),
                "htf_timeframe": htf.upper(),
                "direction": direction.upper(),
                "setup_direction": strat_res.setup_direction.upper(),
                "actionable": strat_res.approved,
                "strategy_approved": strat_res.approved,
                "strategy_name": strat_res.strategy_name,
                "rejection_reasons": strat_res.rejection_reasons,
                "effective_policy": strat_res.effective_policy,
                "strategy": strat_res.to_dict(),
                "confluence": confluence,
                "entry": round(entry, price_decimals) if strat_res.approved else None,
                "stop_loss": round(sl, price_decimals) if strat_res.approved else None,
                "take_profit": round(tp, price_decimals) if strat_res.approved else None,
                "rr": ltf_sig.risk_reward if strat_res.approved else 0.0,
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
                "indicator_decision": ltf_sig.indicator_decision,
                "market_regime": ltf_sig.market_regime,
                "analysis_snapshot": mtf.metadata(),
                "mtf": mtf.to_dict(),
                "message": structure_summary,
                "advice": advice_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Phase 3 evidence is analysis-only. It cannot call a broker,
            # mutate either order ledger, or grant a Live Session.
            from app.services.evidence import (
                capture_decision_evidence,
            )
            evidence = await capture_decision_evidence(
                source="proactive_scanner",
                symbol=symbol,
                timeframe=tf,
                market_type=m_type,
                exchange=ex,
                market_data=ltf_df,
                htf_bias=htf_bias,
                entry_mode=entry_mode,
                signal=ltf_sig.to_dict(),
                strategy=strat_res.to_dict(),
                risk=None,
                ai_analysis=None,
                config_snapshot=mtf.config_snapshot,
                mtf_market_data=mtf.frames,
                mtf_decision=mtf.decision_dict(),
            )
            signal_payload["evidence"] = evidence

            # Broadcast to WebSocket
            await broadcast({"type": "signal", "data": signal_payload})

            # Send Push / Telegram / LINE alerts if high confluence with 30-min debounce cooldown
            alert_threshold = float(
                strat_res.effective_policy.get("min_confluence", 65)
            )
            if strat_res.approved and confluence >= alert_threshold and direction in ("long", "short"):
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
                        rr=ltf_sig.risk_reward,
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
        if self._scan_lock.locked():
            return list(self.recent_signals)
        async with self._scan_lock:
            return await self._scan_all_once()

    async def _scan_all_once(self) -> list[dict]:
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
        now = datetime.now(timezone.utc)
        max_age_seconds = max(self.interval * 3, 300)

        def is_fresh(signal: dict) -> bool:
            try:
                created = datetime.fromisoformat(str(signal.get("timestamp", "")).replace("Z", "+00:00"))
                return (now - created).total_seconds() <= max_age_seconds
            except (TypeError, ValueError):
                return False

        signal_map = {
            _compact_symbol(s.get("symbol", "")): s for s in self.recent_signals
            if _compact_symbol(s.get("symbol", "")) in active_norm_symbols and is_fresh(s)
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
