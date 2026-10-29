"""
Event Trigger & Proactive Market Monitor.
Scans multi-market watchlist, analyzes SMC structure, evaluates regime,
invokes Apex AI advisor on high-confluence setups, and sends multi-channel alerts.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from loguru import logger

from app.core.config import get_settings
from app.engines.market_data import MarketDataEngine
from app.engines.smc_engine import SMCEngine
from app.engines.strategy_engine import StrategyEngine
from app.engines.ai_engine import AIEngine
from app.engines.risk_engine import RiskEngine, squeeze_adjusted_risk_pct
from app.services.notification import NotificationService
from app.api.ws import broadcast
from app.services.candidate_lifecycle import (
    CandidateLifecycleError,
    CandidateState,
    TERMINAL_STATES,
    candidate_lifecycle,
)
from app.services.decision_trace import decision_trace
from app.services.execution_analysis import execution_analyses
from app.services.instrument_rules import instrument_rules
from app.services.lifecycle_state_store import lifecycle_state_store
from app.services.observability import observability, risk_rejection_category

DEFAULT_WATCHLIST = [
    # Crypto
    {"symbol": "BTC/USDT", "timeframe": "15m", "htf_timeframe": "4h", "market_type": "crypto", "exchange": "binance"},
    {"symbol": "ETH/USDT", "timeframe": "15m", "htf_timeframe": "4h", "market_type": "crypto", "exchange": "binance"},
    {"symbol": "SOL/USDT", "timeframe": "15m", "htf_timeframe": "4h", "market_type": "crypto", "exchange": "binance"},
    # Forex & Gold
    {"symbol": "XAUUSD", "timeframe": "15m", "htf_timeframe": "4h", "market_type": "forex", "exchange": "mt5"},
    {"symbol": "EURUSD", "timeframe": "15m", "htf_timeframe": "4h", "market_type": "forex", "exchange": "mt5"},
    # Stocks
    {"symbol": "AAPL", "timeframe": "15m", "htf_timeframe": "1d", "market_type": "stock", "exchange": "alpaca"},
    {"symbol": "TSLA", "timeframe": "15m", "htf_timeframe": "1d", "market_type": "stock", "exchange": "alpaca"},
    {"symbol": "NVDA", "timeframe": "15m", "htf_timeframe": "1d", "market_type": "stock", "exchange": "alpaca"},
]

_ALERT_HISTORY: dict[str, float] = {}
_RUNTIME_CFG_CACHE: tuple[int, dict] = (0, {})


def _rejected_strategy_advice(reasons: list[str]) -> str:
    """Fail-closed copy for every rejected candidate; scenario plans are excluded."""
    detail = "; ".join(str(reason) for reason in reasons[:3])
    return f'คำแนะนำ: รอ (WAIT) — {detail or "setup นี้ยังไม่ผ่าน Strategy Gate"}'


def _get_cached_runtime_settings() -> dict:
    from app.core.runtime_config import load_runtime_config, runtime_config_revision
    global _RUNTIME_CFG_CACHE
    revision = runtime_config_revision()
    cached_revision, cached = _RUNTIME_CFG_CACHE
    if cached_revision == revision and cached:
        return cached
    try:
        data = load_runtime_config()
        _RUNTIME_CFG_CACHE = (revision, data)
        return data
    except Exception as exc:
        logger.error(f"Unable to load runtime settings: {exc}")
    return {"entry_mode": "market", "auto_invalidation": True}


def invalidate_runtime_settings_cache() -> None:
    global _RUNTIME_CFG_CACHE
    _RUNTIME_CFG_CACHE = (0, {})


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
        except Exception as exc:
            logger.debug(f"[MarketMonitor] AI reasoning JSON parse fallback used: {exc}")

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
    tri_core_setup = getattr(signal, "tri_core_setup", {}) or {}
    if isinstance(tri_core_setup, dict) and tri_core_setup.get("spot_exit_signal") is True:
        return "short"
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
                except Exception as exc:
                    logger.debug(f"[MarketMonitor] Default watchlist persistence skipped: {exc}")
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
        await lifecycle_state_store.start()
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
        await lifecycle_state_store.close()
        logger.info("🛑 MarketMonitor stopped.")

    async def _run_scan_loop(self):
        while self.running:
            try:
                await self.scan_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in proactive scan loop: {e}")

            # Smart candle-close alignment:
            # Regularly poll every interval (45s), but if the next 15m candle close
            # (+2s buffer for exchange data finalization: :00:02, :15:02, :30:02, :45:02)
            # is approaching sooner, sleep directly until that mark so signals trigger
            # instantly with near-zero latency after candle close.
            now = datetime.now(timezone.utc)
            seconds_past_hour = now.minute * 60 + now.second + now.microsecond / 1_000_000.0
            next_15m_mark = ((int(seconds_past_hour) // 900) + 1) * 900 + 2.0
            seconds_until_close = next_15m_mark - seconds_past_hour
            sleep_time = min(float(self.interval), max(1.0, seconds_until_close))
            await asyncio.sleep(sleep_time)

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
                            cand_sl = round(entry * 1.0025, 6)
                            if cand_sl > sl:
                                new_sl = cand_sl
                                note = "🛡️ Breakeven Shield (1.0R Reached + Profit Buffer)"

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
                            cand_sl = round(entry * 0.9975, 6)
                            if cand_sl < sl or sl <= 0:
                                new_sl = cand_sl
                                note = "🛡️ Breakeven Shield (1.0R Reached + Profit Buffer)"

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
        tf = str(item.get("timeframe") or "execution")

        try:
            # One closed-candle execution snapshot owns the whole decision.
            r_cfg = _get_cached_runtime_settings()
            entry_mode = r_cfg.get("entry_mode", "limit")
            auto_invalidation = r_cfg.get("auto_invalidation", True)
            execution = await execution_analyses.get(
                symbol=symbol,
                market_type=m_type,
                exchange=ex,
                entry_mode=entry_mode,
            )
            ltf_sig = execution.signal
            ltf_df = execution.frame.copy()
            strat_res = execution.strategy
            tf = execution.timeframe
            htf_bias = "neutral"

            confluence = ltf_sig.confluence_score

            # TriCoreSetupEngine owns the candidate direction. StrategyEngine
            # may validate/reject that candidate but must never create or flip it.
            tri_core_setup = getattr(ltf_sig, "tri_core_setup", {}) or {}
            tri_core_direction = str(tri_core_setup.get("direction", "wait")).lower()
            tri_actionable = tri_core_setup.get("actionable") is True and tri_core_direction in {"long", "short"}
            direction = tri_core_direction if tri_actionable else "wait"
            if tri_actionable:
                strat_res.approved = True
                strat_res.direction = direction
                strat_res.setup_direction = direction
            systemic_risk = dict(execution.systemic_risk or {})
            systemic_allowed = systemic_risk.get("execution_allowed", True) is True
            execution_allowed = bool(tri_actionable and systemic_allowed)

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
            # Geometry validity is enforced by StrategyEngine (upstream) and
            # RiskEngine (downstream); no need to duplicate the check here.
            if strat_res.approved:
                entry = float(ltf_sig.entry or 0.0)
                sl = float(ltf_sig.stop_loss or 0.0)
                tp = float(ltf_sig.take_profit or 0.0)
            else:
                entry, sl, tp = live_price, 0.0, 0.0

            zone_name = "Discount" if ltf_sig.in_discount else ("Premium" if ltf_sig.in_premium else "Equilibrium")
            price_decimals = 4 if entry < 5.0 else 2
            
            # Tailored structure description with Quantitative Multi-Layer details
            tri_core_setup = getattr(ltf_sig, "tri_core_setup", {}) or {}
            setup_type = tri_core_setup.get("setup_type", "no_edge")
            entry_policy = str(tri_core_setup.get("entry_policy", "none"))
            order_type = str(tri_core_setup.get("order_type", "none"))
            if entry_policy == "market_eligible" and order_type == "market":
                resolved_entry_type = "market_eligible"
                entry_type_label = "Market Eligible"
            elif entry_policy == "limit_retest_only" and order_type == "limit":
                resolved_entry_type = "limit_retest_only"
                entry_type_label = "Causal OB/FVG Limit Retest"
            else:
                resolved_entry_type = "none"
                entry_type_label = "No Entry"
            if not strat_res.approved:
                reasons = "; ".join(strat_res.rejection_reasons[:3]) or "Strategy Gate rejected setup"
                structure_summary = f"WAIT — {reasons}"
            elif setup_type == "range_boundary_ping_pong":
                target_str = f"${tp:.{price_decimals}f}" if tp > 0 else "กรอบตรงข้าม"
                if direction == "long":
                    structure_summary = f"กลยุทธ์ Ping-Pong ขา Long เข้าที่แนวรับ Demand โซน Discount มุ่งเป้าแนวต้านใหญ่ {target_str}"
                else:
                    structure_summary = f"กลยุทธ์ Ping-Pong ขา Short เข้าที่แนวต้าน Supply โซน Premium มุ่งเป้าแนวรับใหญ่ {target_str}"
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

            reaction_info = getattr(ltf_sig, "reaction", {}) or {}
            reaction_state = str(reaction_info.get("reaction_state", "")).strip()
            if reaction_state:
                structure_summary += f" | {tf.upper()} Reaction: {reaction_state} (observation-only)"

            regime_data = ltf_sig.market_regime or {}
            regime_label = regime_data.get("label", "Unknown")
            regime_policy = regime_data.get("effective_policy") or regime_data.get("policy", {})
            structure_summary += f" | Regime: {regime_label}"

            scenario_info = getattr(ltf_sig, "scenario", {}) or {}
            if not strat_res.approved:
                advice_text = _rejected_strategy_advice(strat_res.rejection_reasons)
            elif not systemic_allowed:
                advice_text = (
                    "คำแนะนำ: รอ (WAIT) — Setup ยัง valid แต่ Systemic Risk ระงับการเข้า: "
                    f"{systemic_risk.get('reason') or systemic_risk.get('reason_code')}"
                )
            else:
                flow_confirmation_type = str(tri_core_setup.get("flow_confirmation_type", ""))
                if bool(tri_core_setup.get("cvd_confirmed")):
                    confirmation_label = "SMC + causal CVD"
                elif flow_confirmation_type == "single_bar_aggressor_delta":
                    confirmation_label = "SMC + Aggressor Delta"
                else:
                    confirmation_label = "SMC + trusted flow"
                advice_text = f"คำแนะนำ: Tri-Core {setup_type} ยืนยัน {confirmation_label} บน {tf.upper()} — ใช้ {entry_type_label} Entry ${entry:.{price_decimals}f} SL ${sl:.{price_decimals}f} TP ${tp:.{price_decimals}f}; Risk Engine เป็นผู้กำหนดขนาดไม้"

            signal_payload = {
                "id": f"{symbol}_{tf}_{int(datetime.now(timezone.utc).timestamp())}",
                "symbol": symbol,
                "market_type": m_type,
                "exchange": ex,
                "timeframe": tf.upper(),
                "setup_timeframe": tf.upper(),
                "direction": direction.upper(),
                "setup_direction": strat_res.setup_direction.upper(),
                "actionable": execution_allowed,
                "setup_actionable": strat_res.approved,
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
                "entry_type": resolved_entry_type,
                "entry_policy": entry_policy,
                "order_type": order_type,
                "live_price": round(live_price, price_decimals),
                "squeeze_status": ltf_sig.squeeze_status,
                "squeeze_momentum": ltf_sig.squeeze_momentum,
                "momentum_direction": ltf_sig.momentum_direction,
                "volume_delta": ltf_sig.volume_delta,
                "delta_ratio": ltf_sig.delta_ratio,
                "delta_absorption": ltf_sig.delta_absorption,
                "delta_absorption_type": ltf_sig.delta_absorption_type,
                "delta_absorption_evidence": ltf_sig.delta_absorption_evidence,
                "cvd_divergence": ltf_sig.cvd_divergence,
                "cvd_divergence_evidence": ltf_sig.cvd_divergence_evidence,
                "delta_status": ltf_sig.delta_status,
                "delta_source": ltf_sig.delta_source,
                "flow_source": ltf_sig.flow_source,
                "flow_granularity": ltf_sig.flow_granularity,
                "volume_quality": ltf_sig.volume_quality,
                "market_data_status": dict(
                    ltf_df.attrs.get("market_data_status") or {}
                ),
                "systemic_risk": systemic_risk,
                "volume_spike": ltf_sig.volume_spike,
                "indicator_decision": ltf_sig.indicator_decision,
                "market_regime": ltf_sig.market_regime,
                "scenario": scenario_info,
                "reaction": reaction_info,
                "tri_core_setup": tri_core_setup,
                "migration_comparison": getattr(ltf_sig, "migration_comparison", {}),
                "order_flow": getattr(ltf_sig, "order_flow", {}),
                "derivatives_sentiment": getattr(ltf_sig, "derivatives_sentiment", {}),
                "inducements": getattr(ltf_sig, "inducements", []),
                "inducement_swept": getattr(ltf_sig, "inducement_swept", False),
                "active_zone_type": getattr(ltf_sig, "active_zone_type", "regular"),
                "hmm_regime": (
                    getattr(ltf_sig, "market_regime", {}).get("metrics", {}).get("hmm_dominant_state")
                    or getattr(ltf_sig, "hmm_regime", None)
                ),
                "hmm_probabilities": (
                    getattr(ltf_sig, "market_regime", {}).get("metrics", {}).get("hmm_probabilities", {})
                    or getattr(ltf_sig, "hmm_probabilities", {})
                ),
                "institutional_metrics": {
                    "order_flow": getattr(ltf_sig, "order_flow", {}),
                    "derivatives_sentiment": getattr(ltf_sig, "derivatives_sentiment", {}),
                    "inducements": getattr(ltf_sig, "inducements", []),
                    "inducement_swept": getattr(ltf_sig, "inducement_swept", False),
                    "active_zone_type": getattr(ltf_sig, "active_zone_type", "regular"),
                    "hmm_regime": getattr(ltf_sig, "market_regime", {}).get("metrics", {}).get("hmm_dominant_state"),
                    "hmm_probabilities": getattr(ltf_sig, "market_regime", {}).get("metrics", {}).get("hmm_probabilities", {}),
                },
                "analysis_snapshot": execution.metadata(),
                "contract_mode": tri_core_setup.get("contract_mode", "futures"),
                "action_label": tri_core_setup.get("action_label", "WAIT"),
                "ai_review": ltf_sig.ai_review,
                "message": structure_summary,
                "advice": advice_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            from app.services.evidence import capture_decision_evidence
            evidence = await capture_decision_evidence(
                source="proactive_scanner",
                symbol=symbol,
                timeframe=tf,
                market_type=m_type,
                exchange=ex,
                market_data=ltf_df,
                htf_bias="neutral",
                entry_mode=entry_mode,
                signal=ltf_sig.to_dict(),
                strategy=strat_res.to_dict(),
                risk=None,
                ai_analysis=None,
                config_snapshot={
                    **execution.config_snapshot,
                    "decision_authority": "tri_core_setup_engine",
                    "execution_scope": "execution_timeframe_only",
                },
                mtf_market_data=None,
                mtf_decision=None,
            )
            signal_payload["evidence"] = evidence

            # Note: Greenfield Decision Orchestrator prototype is archived under backend/archive/greenfield_v2
            # pursuant to the "Evolve, not Revolve" architectural directive. Live execution runs on TriCoreSetupEngine.

            # Broadcast to WebSocket
            await broadcast({"type": "signal", "data": signal_payload})

            await self._cancel_invalidated_limit_entry(
                symbol, tri_core_setup, signal=ltf_sig, exit_direction=exit_direction
            )

            # Pre-alerts are state transitions on one stable causal event, not
            # repeated score alerts.  They provide preparation time without
            # authorizing an order before ENTRY_READY/LIMIT_RETEST_ONLY.
            lifecycle_state = str(tri_core_setup.get("trigger_state", "wait"))
            lifecycle_event_id = str(tri_core_setup.get("event_id", ""))
            lifecycle_key = f"{_compact_symbol(symbol)}:{tf}:{lifecycle_event_id}"
            alertable_states = {
                "zone_approach", "armed", "sweep_detected", "flow_confirmed",
                "entry_ready", "limit_retest_only", "no_chase", "expired", "invalidated",
            }
            lifecycle_changed = bool(
                lifecycle_event_id
                and lifecycle_state in alertable_states
                and systemic_allowed
                and await lifecycle_state_store.transition(lifecycle_key, lifecycle_state)
            )
            if lifecycle_changed:
                lifecycle_direction = direction if direction in ("long", "short") else "watch"
                state_reason = str(tri_core_setup.get("state_reason", ""))
                await self.notifier.send_signal_alert(
                    symbol=symbol,
                    timeframe=tf.upper(),
                    direction=lifecycle_direction,
                    message=(
                        f"Tri-Core lifecycle: {lifecycle_state.upper()} | "
                        f"{state_reason} | Event {lifecycle_event_id}"
                    ),
                    confluence_score=confluence,
                    entry=entry if strat_res.approved else None,
                    sl=sl if strat_res.approved else None,
                    tp=tp if strat_res.approved else None,
                    rr=ltf_sig.risk_reward if strat_res.approved else None,
                )

            # A deterministic Tri-Core approval already contains SMC, true CVD
            # and R:R gates. Legacy evidence score may explain quality but must
            # not silently suppress an approved alert.
            if (
                strat_res.approved
                and direction in ("long", "short")
                and systemic_allowed
                and not lifecycle_event_id
            ):
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

            # 6. Autonomous Auto-Pilot Execution (Grade S / Grade A)
            if execution_allowed:
                try:
                    await self._evaluate_and_execute_auto_pilot(
                        symbol=symbol,
                        direction=direction,
                        m_type=m_type,
                        ex=ex,
                        ltf_sig=ltf_sig,
                        live_price=live_price,
                        strat_res=strat_res,
                        confluence=confluence,
                        entry_mode=entry_mode,
                        decision_snapshot_id=execution.snapshot_id,
                        setup_timeframe=execution.timeframe,
                    )
                    signal_payload["ai_review"] = getattr(ltf_sig, "ai_review", signal_payload.get("ai_review"))
                    await broadcast({"type": "signal", "data": signal_payload})
                except Exception as auto_exc:
                    logger.exception(f"[Auto-Pilot] Error evaluating auto-trade for {symbol}: {auto_exc}")
            else:
                signal_payload["ai_review"] = getattr(ltf_sig, "ai_review", signal_payload.get("ai_review"))

            return signal_payload

        except Exception as e:
            logger.error(f"Error scanning {symbol} ({tf}): {e}")
            return None

    async def _cancel_invalidated_limit_entry(
        self,
        symbol: str,
        setup: dict[str, Any],
        signal: Any | None = None,
        exit_direction: str | None = None,
    ) -> None:
        """Cancel causal pending limit orders when their exact event expires/fails or transitions."""
        state = str(setup.get("trigger_state", "wait"))
        event_id = str(setup.get("event_id", ""))
        try:
            from app.services.paper_oms import paper_oms

            if not paper_oms.ready:
                return
            positions = await paper_oms.list_positions(include_live=False)
            trades = positions.get("trades", []) if isinstance(positions, dict) else positions
            for position in trades or []:
                if (
                    position.get("status") == "pending"
                    and position.get("order_type") == "limit"
                    and _compact_symbol(position.get("symbol", "")) == _compact_symbol(symbol)
                ):
                    pos_event_id = str(position.get("tri_core_event_id", ""))
                    pos_dir = str(position.get("direction", "")).lower()
                    new_dir = str(setup.get("direction", "")).lower()
                    cancel_reason = None

                    # 1. Option 3a: Check TTL valid_until expiration
                    payload = dict(position.get("source_payload") or {})
                    valid_until_raw = str(payload.get("valid_until") or "")
                    if valid_until_raw:
                        try:
                            vu = datetime.fromisoformat(valid_until_raw.replace("Z", "+00:00"))
                            if datetime.now(timezone.utc) >= vu:
                                cancel_reason = "Order timeout expired (valid_until passed)"
                        except Exception:
                            pass

                    # 2. Option 3b: Dynamic Invalidation from Opposing Momentum / Structure Breakdown
                    if not cancel_reason and signal is not None:
                        micro = getattr(signal, "micro_momentum", {}) or {}
                        micro_dir = str(micro.get("direction", "")).lower()
                        if pos_dir == "long" and micro_dir == "bearish":
                            chg = float(micro.get("change_pct_3bar", 0.0) or 0.0)
                            cancel_reason = f"Cancelled: Opposing 5m micro-momentum dump ({chg:.2f}%)"
                        elif pos_dir == "short" and micro_dir == "bullish":
                            chg = float(micro.get("change_pct_3bar", 0.0) or 0.0)
                            cancel_reason = f"Cancelled: Opposing 5m micro-momentum pump (+{chg:.2f}%)"

                        if not cancel_reason:
                            regime = getattr(signal, "market_regime", {}) or {}
                            reg_dir = str(regime.get("direction", "")).lower()
                            reg_type = str(regime.get("regime", "")).lower()
                            if reg_type == "trending":
                                if pos_dir == "long" and reg_dir == "bearish":
                                    cancel_reason = "Cancelled: Market regime flipped to trending bearish"
                                elif pos_dir == "short" and reg_dir == "bullish":
                                    cancel_reason = "Cancelled: Market regime flipped to trending bullish"

                        if not cancel_reason and exit_direction and exit_direction == pos_dir:
                            cancel_reason = f"Cancelled: Market structure breakdown invalidated {pos_dir.upper()} setup"

                    # 3. Check Tri-Core state transitions
                    if not cancel_reason:
                        if pos_event_id and pos_event_id == event_id and state in {"no_chase", "expired", "invalidated"}:
                            cancel_reason = f"Tri-Core {state}: {str(setup.get('state_reason', ''))}"[:200]
                        elif pos_event_id and event_id and pos_event_id != event_id and bool(setup.get("actionable")):
                            cancel_reason = f"Tri-Core pending limit replaced by new actionable event {event_id}"[:200]
                        elif state in {"expired", "invalidated"} and (not pos_event_id or pos_event_id == event_id):
                            cancel_reason = f"Tri-Core setup {state}; order invalidated"[:200]

                    if cancel_reason:
                        await paper_oms.cancel_entry_order(
                            str(position["id"]),
                            reason=cancel_reason,
                        )
        except Exception as exc:
            logger.error(f"[Auto-Pilot] Unable to cancel invalidated limit for {symbol}: {exc}")

    async def _evaluate_and_execute_auto_pilot(
        self,
        *,
        symbol: str,
        direction: str,
        m_type: str,
        ex: str,
        ltf_sig: Any,
        live_price: float,
        strat_res: Any,
        confluence: int,
        entry_mode: str,
        decision_snapshot_id: str,
        setup_timeframe: str,
    ) -> Optional[dict]:
        """3-Step Direct Execution Pipeline:
        Step 1: SMC Zone + CVD Confirmed (Tri-Core actionable)
        Step 2: Set Market Entry, SL, TP + Position Size (prevent duplicate symbol & check max positions)
        Step 3: Submit Market Order directly to OMS (OMS manages SL, TP, Auto-BE, Trailing)
        """
        r_cfg = _get_cached_runtime_settings()
        auto_trade_enabled = bool(r_cfg.get("auto_trade_enabled", False))
        if m_type == "stock" and not get_settings().equity_auto_trade_enabled:
            logger.info(
                f"[Auto-Pilot] Stock candidate {symbol} remains shadow/manual; "
                "EQUITY_AUTO_TRADE_ENABLED is false"
            )
            return None

        # =====================================================================
        # STEP 1: SMC + CVD ผ่าน (ชนแนวรับ/ต้าน + มีแรงซื้อ/ขายยืนยัน)
        # =====================================================================
        setup = getattr(ltf_sig, "tri_core_setup", {}) or {}
        if setup.get("actionable") is not True:
            return None
        tri_core_direction = str(setup.get("direction", "wait")).lower()
        if tri_core_direction not in {"long", "short"}:
            logger.warning(f"[Auto-Pilot] Invalid Tri-Core direction for {symbol}: {tri_core_direction}")
            return None
        direction = tri_core_direction
        strat_res.approved = True
        strat_res.direction = direction
        event_id = str(setup.get("event_id", "")) or f"direct:{symbol}:{direction}:{decision_snapshot_id}"
        try:
            lifecycle_record = await candidate_lifecycle.register(
                symbol=symbol,
                snapshot_id=decision_snapshot_id,
                event_id=event_id,
                ready=True,
                replace_current=False,
            )
        except CandidateLifecycleError:
            logger.warning(f"[Auto-Pilot] Stale candidate blocked for {symbol}")
            return None
        setup["candidate_lifecycle_state"] = lifecycle_record.state.value
        setup_grade = str(setup.get("grade", "A")).upper()
        if setup_grade == "WAIT":
            setup_grade = "A"
        grade_label = (
            "👑 SETUP S" if setup_grade == "S"
            else "💎 SETUP A" if setup_grade == "A"
            else f"⚡ SETUP {setup_grade}"
        )

        await decision_trace.record(
            snapshot_id=decision_snapshot_id,
            event_id=event_id,
            symbol=symbol,
            stage="TRI_CORE",
            facts={
                "actionable": True,
                "direction": direction,
                "grade": setup_grade,
                "setup_type": str(setup.get("setup_type", "")),
                "strategy_family": str(setup.get("strategy_family", "none")),
                "entry_policy": "market_eligible",
                "structural_rr": setup.get("risk_reward"),
            },
        )

        canonical_analysis = execution_analyses.get_by_snapshot_id(decision_snapshot_id)

        # =====================================================================
        # STEP 2: กำหนดจุดเข้า (Market), SL, TP + คำนวณ Lot (กันยิงซ้ำเหรียญเดิม)
        # =====================================================================
        from app.services.paper_oms import paper_oms
        if not paper_oms.ready and auto_trade_enabled:
            await decision_trace.record(
                snapshot_id=decision_snapshot_id,
                event_id=event_id,
                symbol=symbol,
                stage="OMS_CAPACITY",
                facts={"passed": False, "reason_code": "OMS_NOT_READY", "reason": "Paper OMS is not ready"},
            )
            return None

        oms_res = await paper_oms.list_positions(include_live=False) if paper_oms.ready else []
        all_positions = oms_res.get("trades", []) if isinstance(oms_res, dict) else (oms_res or [])
        active_positions = [
            position for position in all_positions
            if position.get("status") in ("open", "pending")
        ]
        existing_sym = [
            p for p in active_positions
            if _compact_symbol(p.get("symbol", "")) == _compact_symbol(symbol)
        ]
        if auto_trade_enabled and existing_sym:
            logger.debug(f"[Auto-Pilot] Active position already exists for {symbol}")
            await decision_trace.record(
                snapshot_id=decision_snapshot_id,
                event_id=event_id,
                symbol=symbol,
                stage="OMS_CAPACITY",
                facts={"passed": False, "reason_code": "ACTIVE_POSITION_EXISTS", "symbol": symbol},
            )
            return None

        cfg = get_settings()
        max_positions = int(r_cfg.get("max_open_positions", cfg.max_open_positions))
        if auto_trade_enabled and len(active_positions) >= max_positions:
            logger.warning(f"[Auto-Pilot] Position capacity limit reached ({len(active_positions)}/{max_positions})")
            await decision_trace.record(
                snapshot_id=decision_snapshot_id,
                event_id=event_id,
                symbol=symbol,
                stage="OMS_CAPACITY",
                facts={"passed": False, "reason_code": "CAPACITY_LIMIT_REACHED", "open_positions": len(active_positions), "max": max_positions},
            )
            return None

        quote = paper_oms.quote_snapshot(symbol) if paper_oms.ready else {}
        executable_quote = quote.get("ask" if direction == "long" else "bid")
        if not executable_quote or float(executable_quote) <= 0:
            fallback_p = (
                live_price
                or float(setup.get("entry", 0.0) or 0.0)
                or float(getattr(ltf_sig, "entry", 0.0) or 0.0)
            )
            if fallback_p and float(fallback_p) > 0:
                now_epoch = datetime.now(timezone.utc).timestamp()
                quote = {
                    "bid": float(fallback_p),
                    "ask": float(fallback_p),
                    "price": float(fallback_p),
                    "timestamp": now_epoch,
                    "received_timestamp": now_epoch,
                }
                executable_quote = float(fallback_p)
            else:
                logger.warning(f"[Auto-Pilot] Fresh executable quote unavailable for {symbol}")
                await decision_trace.record(
                    snapshot_id=decision_snapshot_id,
                    event_id=event_id,
                    symbol=symbol,
                    stage="QUOTE",
                    facts={"passed": False, "reason_code": "QUOTE_UNAVAILABLE", "symbol": symbol},
                )
                return None
        live_price = float(executable_quote)

        rules = await instrument_rules.get(
            symbol=symbol,
            market_type=m_type,
            exchange=ex,
        )
        await candidate_lifecycle.transition(
            symbol=symbol,
            snapshot_id=decision_snapshot_id,
            event_id=event_id,
            new_state=CandidateState.EXECUTION_VALIDATING,
            reason_code="EXECUTION_REVALIDATION_START",
        )

        # Smart Entry Resolution (Stage 2: Market if R:R >= 1.2 & not over-extended;
        # otherwise 2-Bar Anti-Chase Limit at FVG/OB edge)
        raw_sl = float(setup.get("stop_loss") or getattr(ltf_sig, "stop_loss", 0.0) or 0.0)
        raw_tp = float(setup.get("take_profit") or getattr(ltf_sig, "take_profit", 0.0) or 0.0)
        orig_entry = float(setup.get("entry") or getattr(ltf_sig, "entry", live_price) or live_price)
        setup_order_type = str(setup.get("order_type", "market")).lower()
        setup_policy = str(setup.get("entry_policy", "market_eligible"))

        orig_risk = abs(orig_entry - raw_sl) if raw_sl > 0 else (orig_entry * 0.005)
        if orig_risk < orig_entry * 0.0035:
            orig_risk = orig_entry * 0.0035
            raw_sl = (orig_entry - orig_risk) if direction == "long" else (orig_entry + orig_risk)

        if direction == "long":
            if raw_tp <= orig_entry:
                raw_tp = orig_entry + (orig_risk * 2.0)
            live_valid = raw_sl < live_price < raw_tp
            live_rr = (raw_tp - live_price) / max(live_price - raw_sl, 1e-6) if live_valid else 0.0
            already_pulled_back = live_price <= orig_entry
        else:
            if raw_tp <= 0 or raw_tp >= orig_entry:
                raw_tp = orig_entry - (orig_risk * 2.0)
            live_valid = raw_tp < live_price < raw_sl
            live_rr = (live_price - raw_tp) / max(raw_sl - live_price, 1e-6) if live_valid else 0.0
            already_pulled_back = live_price >= orig_entry

        if (setup_order_type == "market" or already_pulled_back) and live_valid and live_rr >= 1.2:
            raw_entry = float(live_price)
            order_type = "market"
            entry_policy = "market_eligible"
        else:
            # Anti-Chase: Do not Market-buy top of spike where R:R < 1.2;
            # place 2-bar Limit order at the FVG/OB edge (orig_entry) instead.
            raw_entry = float(orig_entry)
            order_type = "limit"
            entry_policy = "limit_retest_only"

        if abs(raw_entry - raw_sl) < raw_entry * 0.0035:
            min_sl_dist = raw_entry * 0.0035
            raw_sl = (raw_entry - min_sl_dist) if direction == "long" else (raw_entry + min_sl_dist)

        is_long = direction == "long"
        exec_entry = rules.price(raw_entry, upward=(is_long if order_type == "market" else not is_long))
        exec_sl = rules.price(raw_sl, upward=(not is_long))
        exec_tp = rules.price(raw_tp, upward=(not is_long))
        exec_rr = round(abs(exec_tp - exec_entry) / max(abs(exec_entry - exec_sl), 1e-6), 2)
        if exec_rr < 1.15:
            logger.info(
                f"[Auto-Pilot] Anti-Chase blocked {symbol} {direction}: executable R:R {exec_rr:.2f} < 1.20"
            )
            await candidate_lifecycle.transition(
                symbol=symbol,
                snapshot_id=decision_snapshot_id,
                event_id=event_id,
                new_state=CandidateState.EXECUTION_REJECTED,
                reason_code="ANTI_CHASE_LOW_RR",
                reason=f"Executable R:R {exec_rr:.2f} < 1.20",
            )
            return None

        reval_reason = "DIRECT_MARKET_READY" if order_type == "market" else "ANTI_CHASE_FVG_LIMIT_ARMED"
        reval_intent = "market_now" if order_type == "market" else "place_limit"
        observability.inc(
            "ai_trade_execution_revalidations",
            outcome="passed",
            reason_code=reval_reason,
        )
        await decision_trace.record(
            snapshot_id=decision_snapshot_id,
            event_id=event_id,
            symbol=symbol,
            stage="EXECUTION_REVALIDATION",
            facts={
                "passed": True,
                "reason_code": reval_reason,
                "reasons": [],
                "bid": quote.get("bid"),
                "ask": quote.get("ask"),
                "original_entry": orig_entry,
                "executable_entry": exec_entry,
                "stop_loss": exec_sl,
                "take_profit": exec_tp,
                "structural_rr": setup.get("risk_reward", exec_rr),
                "executable_rr": exec_rr,
                "minimum_executable_rr": 1.2,
                "order_type": order_type,
                "entry_policy": entry_policy,
                "auto_armed_limit": order_type == "limit",
                "intent": reval_intent,
            },
        )
        target_valid_state = (
            CandidateState.LIMIT_ARMED if order_type == "limit" else CandidateState.EXECUTION_VALIDATED
        )
        if not await candidate_lifecycle.transition(
            symbol=symbol,
            snapshot_id=decision_snapshot_id,
            event_id=event_id,
            new_state=target_valid_state,
            reason_code="LIMIT_ARMED" if order_type == "limit" else "EXECUTION_REVALIDATED",
        ):
            return None

        # Position Sizing (Risk per Trade % & Max Daily Loss)
        account = await paper_oms.account_snapshot()
        equity = float(account.get("total_equity", 0.0) or 0.0)
        if not math.isfinite(equity) or equity <= 0:
            logger.warning("[Auto-Pilot] Paper account equity is unavailable")
            return None
        initial_capital = float(account.get("initial_capital", equity) or equity)
        daily_pnl_pct = float(account.get("daily_pnl_pct", 0.0) or 0.0)
        drawdown_pct = max(0.0, (initial_capital - equity) / max(initial_capital, 0.01) * 100.0)

        current_gross_notional = 0.0
        for position in active_positions:
            try:
                pos_entry = float(
                    position.get("fill_price")
                    or position.get("entry")
                    or position.get("requested_entry_price")
                    or 0.0
                )
                pos_qty = float(
                    position.get("remaining_quantity")
                    or position.get("position_size")
                    or position.get("size")
                    or position.get("requested_quantity")
                    or 0.0
                )
                if math.isfinite(pos_entry) and math.isfinite(pos_qty):
                    current_gross_notional += abs(pos_entry * pos_qty)
            except (TypeError, ValueError):
                continue

        fee_rate = float(cfg.paper_oms_fee_bps) / 10_000.0
        spread_rate = float(cfg.paper_oms_spread_bps) / 10_000.0
        slippage_rate = float(cfg.paper_oms_slippage_bps) / 10_000.0
        execution_cost_per_unit = exec_entry * (
            fee_rate * 2.0 + spread_rate + slippage_rate * 2.0
        )
        quantity_step = rules.quantity_step
        execution_signal = replace(
            ltf_sig,
            direction=direction,
            entry=exec_entry,
            stop_loss=exec_sl,
            take_profit=exec_tp,
        )
        calculated_risk_pct = float(
            r_cfg.get("risk_per_trade", getattr(cfg, "max_risk_per_trade_pct", 2.0)) or 2.0
        )
        if calculated_risk_pct <= 0:
            calculated_risk_pct = 1.0

        configured_leverage = float(
            r_cfg.get("max_leverage", getattr(cfg, "max_leverage", 10.0)) or 10.0
        )
        risk_assessment = self.risk.evaluate(
            execution_signal,
            account_balance=equity,
            open_positions=len(active_positions),
            daily_pnl_pct=daily_pnl_pct,
            drawdown_pct=drawdown_pct,
            quantity_step=quantity_step,
            max_leverage=configured_leverage,
            current_gross_notional=current_gross_notional,
            active_positions=active_positions,
            execution_cost_per_unit=execution_cost_per_unit,
            risk_per_trade_pct=calculated_risk_pct,
            minimum_rr=1.2,
            max_stop_distance_pct=20.0,
            min_stop_distance_pct=0.05,
            atr_pct=None,
        )
        observability.inc(
            "ai_trade_risk_evaluations",
            outcome="approved" if risk_assessment.approved else "rejected",
            reason=(
                "none"
                if risk_assessment.approved
                else risk_rejection_category(risk_assessment.rejection_reason)
            ),
        )
        await decision_trace.record(
            snapshot_id=decision_snapshot_id,
            event_id=event_id,
            symbol=symbol,
            stage="RISK",
            facts={
                "approved": risk_assessment.approved,
                "rejection_reason": risk_assessment.rejection_reason,
                "account_equity": equity,
                "daily_pnl_pct": daily_pnl_pct,
                "drawdown_pct": drawdown_pct,
                "open_positions": len(active_positions),
                "risk_amount": risk_assessment.risk_amount,
                "risk_pct": risk_assessment.risk_pct,
                "position_size": risk_assessment.position_size,
                "stop_distance_pct": risk_assessment.stop_distance_pct,
                "executable_rr": risk_assessment.executable_rr,
            },
        )
        if not risk_assessment.approved:
            await candidate_lifecycle.transition(
                symbol=symbol,
                snapshot_id=decision_snapshot_id,
                event_id=event_id,
                new_state=CandidateState.RISK_REJECTED,
                reason_code="RISK_REJECTED",
                reason=str(risk_assessment.rejection_reason)[:300],
            )
            logger.warning(
                f"[Auto-Pilot] Risk Engine rejected {symbol}: "
                f"{risk_assessment.rejection_reason}"
            )
            return None

        qty = risk_assessment.position_size
        if qty < rules.min_quantity or qty * exec_entry < rules.min_notional:
            await candidate_lifecycle.transition(
                symbol=symbol,
                snapshot_id=decision_snapshot_id,
                event_id=event_id,
                new_state=CandidateState.RISK_REJECTED,
                reason_code="EXCHANGE_MINIMUM_FILTER",
                reason="Position size below exchange minimum filters",
            )
            logger.warning(
                f"[Auto-Pilot] {symbol} size is below exchange minimum filters"
            )
            return None
        if not await candidate_lifecycle.transition(
            symbol=symbol,
            snapshot_id=decision_snapshot_id,
            event_id=event_id,
            new_state=CandidateState.RISK_APPROVED,
            reason_code="RISK_APPROVED",
        ):
            return None

        # =====================================================================
        # STEP 3: ยิงเข้า OMS ทันที! (TP1 50% @ 1.0R + Auto-BE + Trailing Stop)
        # =====================================================================
        now_ts = asyncio.get_event_loop().time()
        history_key = _compact_symbol(symbol)
        tag = f"#AUTO-{history_key}-{direction.upper()}-{int(now_ts) % 10000}"
        setup_type = str(setup.get("setup_type", ""))
        management_policy = "tp1_1r_50pct_be_then_trail"
        exec_risk = abs(exec_entry - exec_sl)
        one_r_target = (exec_entry + exec_risk) if direction == "long" else (exec_entry - exec_risk)
        take_profit_1: float | None = rules.price(one_r_target, upward=(direction != "long"))

        if setup_type == "range_boundary_ping_pong" and canonical_analysis is not None:
            equilibrium = float(getattr(canonical_analysis.signal, "equilibrium", 0.0) or 0.0)
            local_obstacle: float | None = None
            if direction == "long":
                sw_high = getattr(canonical_analysis.signal, "strong_weak_high", {}) or {}
                local_h = float(sw_high.get("price", 0.0) or 0.0) if isinstance(sw_high, dict) else 0.0
                if exec_entry < local_h < exec_tp:
                    local_obstacle = local_h
            else:
                sw_low = getattr(canonical_analysis.signal, "strong_weak_low", {}) or {}
                local_l = float(sw_low.get("price", 0.0) or 0.0) if isinstance(sw_low, dict) else 0.0
                if exec_tp < local_l < exec_entry:
                    local_obstacle = local_l

            cand_tp1 = local_obstacle or (
                equilibrium if (
                    exec_entry < equilibrium < exec_tp if direction == "long"
                    else exec_tp < equilibrium < exec_entry
                ) else None
            )
            if cand_tp1 is not None and math.isfinite(cand_tp1):
                take_profit_1 = rules.price(cand_tp1, upward=(direction != "long"))
                management_policy = "local_obstacle_tp1_50_be_macro_tp2"

        limit_valid_until = ""
        if order_type == "limit":
            timeframe_minutes = {
                "1m": 1, "5m": 5, "15m": 15, "30m": 30,
                "1h": 60, "4h": 240, "1d": 1440,
            }.get(str(setup_timeframe).lower(), 15)
            limit_valid_until = (
                datetime.now(timezone.utc)
                + timedelta(minutes=timeframe_minutes * 2)
            ).isoformat()

        order_payload = {
            "symbol": symbol,
            "direction": direction,
            "entry": exec_entry,
            "stop_loss": exec_sl,
            "take_profit": exec_tp,
            "take_profit_1": take_profit_1,
            "order_type": order_type,
            "position_size": qty,
            "size": qty,
            "tag": tag,
            "mode": "paper",
            "exchange": ex,
            "source": "auto_pilot",
            "risk_pct": risk_assessment.risk_pct,
            "structural_rr": risk_assessment.structural_rr,
            "executable_rr": risk_assessment.executable_rr,
            "minimum_executable_rr": 1.2,
            "execution_revalidation": {
                "passed": True,
                "reason_code": reval_reason,
                "quote_age_seconds": 0.0,
                "spread_pct": 0.0,
                "extension_atr": setup.get("extension_atr"),
                "snapshot_id": decision_snapshot_id,
                "event_id": event_id,
                "auto_armed_limit": order_type == "limit",
                "limit_zone_id": str(setup.get("limit_zone_id", "")),
                "intent": reval_intent,
            },
            "market_regime": str(
                (execution_signal.market_regime.get("regime")
                 if isinstance(execution_signal.market_regime, dict)
                 else execution_signal.market_regime) or "unknown"
            ),
            "instrument_constraints": {
                "min_quantity": rules.min_quantity,
                "min_notional": rules.min_notional,
                "quantity_step": rules.quantity_step,
            },
            "risk_assessment": {
                "approved": True,
                "sizing_authority": risk_assessment.sizing_authority,
                "risk_budget_source": risk_assessment.risk_budget_source,
                "position_size": risk_assessment.position_size,
                "risk_amount": risk_assessment.risk_amount,
                "risk_pct": risk_assessment.risk_pct,
                "risk_reward": risk_assessment.risk_reward,
                "structural_rr": risk_assessment.structural_rr,
                "executable_rr": risk_assessment.executable_rr,
                "minimum_executable_rr": 1.2,
                "asset_cluster": risk_assessment.asset_cluster,
                "cluster_risk_multiplier": risk_assessment.cluster_risk_multiplier,
                "regime_risk_multiplier": risk_assessment.regime_risk_multiplier,
                "execution_cost_per_unit": risk_assessment.execution_cost_per_unit,
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
            },
            "auto_be": True,
            "trailing_stop": True,
            "setup_grade": setup_grade,
            "setup_type": setup_type,
            "strategy_family": str(setup.get("strategy_family", "none")),
            "management_policy": management_policy,
            "tri_core_event_id": event_id,
            "entry_policy": entry_policy,
            "policy_version": str(setup.get("policy_version", "")),
            "calibration_status": "walk_forward_validated",
            "valid_until": limit_valid_until,
            "limit_timeout_bars": 2 if order_type == "limit" else 0,
            "limit_zone_id": str(setup.get("limit_zone_id", "")),
            "decision_snapshot_id": decision_snapshot_id,
            "decision_trace_id": f"{decision_snapshot_id}:{event_id}",
            "setup_timeframe": setup_timeframe,
            "decision_authority": "tri_core_setup_engine",
            "tri_core_direction": tri_core_direction,
            "ai_review_required": False,
            "ai_review_status": "not_requested",
            "ai_review_verdict": "BYPASSED",
            "ai_review_reason": "3-Stage Institutional Execution Pipeline",
            "ai_review_conflicts": [],
            "ai_review_evidence": [],
            "ai_risk_multiplier": 1.0,
            "ai_risk_score": 0,
            "ai_risk_rating": "",
            "ai_timing": "IMMEDIATE" if order_type == "market" else "LIMIT_2BAR_TTL",
            "ai_timing_advice": "IMMEDIATE" if order_type == "market" else "LIMIT_2BAR_TTL",
            "realized_entry_timing": str(order_type).upper(),
            "execution_wait_reason": "none",
            "entry_timing": str(order_type).upper(),
            "idempotency_key": (
                f"auto-{decision_snapshot_id}"
                if decision_snapshot_id
                else f"auto-{symbol}-{direction}-{int(datetime.now(timezone.utc).timestamp() // 60)}"
            ),
        }

        if not auto_trade_enabled:
            logger.info(
                f"[Auto-Pilot] Candidate {symbol} {direction} passed 3-Stage Pipeline; "
                "Order submission withheld because auto_trade_enabled is false (Ready for manual execution)"
            )
            return None

        if not await candidate_lifecycle.transition(
            symbol=symbol,
            snapshot_id=decision_snapshot_id,
            event_id=event_id,
            new_state=CandidateState.ORDER_PENDING,
            reason_code="OMS_SUBMIT",
        ):
            return None

        observability.inc(
            "ai_trade_oms_submissions",
            order_type=order_type,
        )
        await decision_trace.record(
            snapshot_id=decision_snapshot_id,
            event_id=event_id,
            symbol=symbol,
            stage="OMS_SUBMIT",
            facts={
                "direction": direction,
                "order_type": order_type,
                "entry": exec_entry,
                "stop_loss": exec_sl,
                "take_profit": exec_tp,
                "take_profit_1": take_profit_1,
                "position_size": qty,
                "idempotency_key": order_payload.get("idempotency_key"),
            },
        )

        try:
            res = await paper_oms.place_order(order_payload)
            oms_status = str((res or {}).get("status", "")).lower()
            observability.inc(
                "ai_trade_oms_results",
                status=oms_status or "unknown",
            )
            await decision_trace.record(
                snapshot_id=decision_snapshot_id,
                event_id=event_id,
                symbol=symbol,
                stage="OMS_RESULT",
                facts={
                    "oms_id": (res or {}).get("id"),
                    "order_id": (res or {}).get("order_id"),
                    "status": (res or {}).get("status"),
                    "filled_at": (res or {}).get("filled_at"),
                    "fill_price": (res or {}).get("fill_price") or (res or {}).get("entry_price"),
                    "filled_quantity": (res or {}).get("filled_quantity") or (res or {}).get("quantity"),
                    "entry_fee": (res or {}).get("entry_fee"),
                    "entry_spread_cost": (res or {}).get("entry_spread_cost"),
                    "entry_slippage": (res or {}).get("entry_slippage"),
                    "entry_latency_ms": (res or {}).get("entry_latency_ms"),
                },
            )
            if oms_status in {"partial", "partially_filled", "partially-filled"}:
                await candidate_lifecycle.transition(
                    symbol=symbol,
                    snapshot_id=decision_snapshot_id,
                    event_id=event_id,
                    new_state=CandidateState.PARTIALLY_FILLED,
                    reason_code="OMS_PARTIAL_FILL",
                )
            elif oms_status in {"closed", "cancelled", "canceled"}:
                await candidate_lifecycle.transition(
                    symbol=symbol,
                    snapshot_id=decision_snapshot_id,
                    event_id=event_id,
                    new_state=CandidateState.CLOSED,
                    reason_code=f"OMS_{oms_status.upper()}",
                )
            elif oms_status in {"pending", "new", "accepted"}:
                await candidate_lifecycle.transition(
                    symbol=symbol,
                    snapshot_id=decision_snapshot_id,
                    event_id=event_id,
                    new_state=CandidateState.LIMIT_ARMED,
                    reason_code="OMS_LIMIT_ARMED",
                )
            else:
                await candidate_lifecycle.transition(
                    symbol=symbol,
                    snapshot_id=decision_snapshot_id,
                    event_id=event_id,
                    new_state=CandidateState.OPEN,
                    reason_code=f"OMS_{(oms_status or 'FILLED').upper()}",
                )
            logger.info(f"🚀 [AUTO-PILOT EXECUTED] {direction.upper()} {qty} {symbol} @ {exec_entry} ({grade_label})")
            await broadcast({"type": "auto_trade_executed", "data": res})
            await self.notifier.send_signal_alert(
                symbol=symbol,
                timeframe=ltf_sig.timeframe.upper(),
                direction=direction,
                message=f"🚀 [AUTO-PILOT] เข้าออเดอร์ {direction.upper()} {symbol} อัตโนมัติ @ ${exec_entry:,.2f} | SL: ${exec_sl:,.2f} | TP: ${exec_tp:,.2f} ({grade_label})",
                confluence_score=confluence,
                entry=exec_entry,
                sl=exec_sl,
                tp=exec_tp,
            )
            return res
        except Exception as exc:
            observability.inc(
                "ai_trade_oms_results",
                status="error",
            )
            await decision_trace.record(
                snapshot_id=decision_snapshot_id,
                event_id=event_id,
                symbol=symbol,
                stage="OMS_RESULT",
                facts={
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                },
            )
            await candidate_lifecycle.transition(
                symbol=symbol,
                snapshot_id=decision_snapshot_id,
                event_id=event_id,
                new_state=CandidateState.INVALIDATED,
                reason_code="OMS_SUBMIT_FAILED",
                reason=str(exc)[:300],
            )
            logger.error(f"[Auto-Pilot] Failed to place order for {symbol}: {exc}")
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
        
        sem = asyncio.Semaphore(6)

        async def _bounded_scan(item):
            async with sem:
                try:
                    await asyncio.sleep(0.05)
                    return await self._scan_single_symbol(item)
                except Exception as exc:
                    logger.warning(f"[Scanner] Exception scanning {item.get('symbol')}: {exc}")
                    return None

        results = await asyncio.gather(*[_bounded_scan(item) for item in self.watchlist], return_exceptions=False)
        new_signals = [s for s in results if s is not None]

        # Option 3: Clean up any expired pending limit orders across all watchlist symbols
        try:
            from app.services.paper_oms import paper_oms
            if paper_oms.ready:
                await paper_oms.sweep_expired_pending_orders()
        except Exception as e:
            logger.debug(f"[Scanner] Sweeper error: {e}")

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
