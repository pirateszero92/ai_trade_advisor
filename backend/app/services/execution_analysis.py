"""Canonical single-timeframe decision snapshot used by every trading path."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from weakref import WeakValueDictionary

import pandas as pd
from loguru import logger

from app.core.strategy_config_store import read_strategy_config
from app.core.runtime_config import load_runtime_config
from app.core.config import get_settings
from app.engines.indicator_core import load_indicator_core_config
from app.engines.market_data import MarketDataEngine
from app.engines.regime_engine import load_regime_policy_config
from app.engines.smc_engine import SMCEngine, SMCSignal
from app.engines.strategy_engine import StrategyEngine, StrategyResult
from app.engines.timeframe_profiles import (
    load_timeframe_profiles,
    resolve_profile_indicator_config,
    validate_timeframe_profiles,
)
from app.services.analysis_snapshot import _frame_digest, _next_refresh_at
from app.services.candidate_lifecycle import CandidateState, candidate_lifecycle
from app.services.decision_trace import decision_trace
from app.services.evidence import fingerprint
from app.services.observability import observability


STRATEGY_FILE = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"


def execution_entry_mode() -> str:
    mode = load_runtime_config().get("entry_mode", "limit")
    return mode if mode in {"limit", "market"} else "limit"


@dataclass
class ExecutionAnalysis:
    symbol: str
    timeframe: str
    frame: pd.DataFrame
    signal: SMCSignal
    strategy: StrategyResult
    config_snapshot: dict[str, Any]
    snapshot_id: str
    generated_at: datetime
    valid_until: datetime
    market_type: str = ""
    exchange: str = ""
    entry_mode: str = ""
    systemic_risk: dict[str, Any] = field(default_factory=dict)

    def clone(self) -> ExecutionAnalysis:
        """Return an isolated working copy of the cached canonical result.

        API and background-scanner callers enrich or reject a result for their
        own presentation/execution path.  They must never mutate the object
        retained in the cache, otherwise another caller can observe those
        changes during the same closed-candle window.
        """
        return ExecutionAnalysis(
            symbol=self.symbol,
            timeframe=self.timeframe,
            frame=self.frame.copy(deep=True),
            signal=deepcopy(self.signal),
            strategy=deepcopy(self.strategy),
            config_snapshot=deepcopy(self.config_snapshot),
            snapshot_id=self.snapshot_id,
            generated_at=self.generated_at,
            valid_until=self.valid_until,
            market_type=self.market_type,
            exchange=self.exchange,
            entry_mode=self.entry_mode,
            systemic_risk=deepcopy(self.systemic_risk),
        )

    def metadata(self) -> dict[str, Any]:
        candle_digest = hashlib.sha256(_frame_digest(self.frame)).hexdigest()[:24]
        return {
            "snapshot_id": self.snapshot_id,
            "authority": "execution_timeframe_only",
            "decision_authority": "tri_core_setup_engine",
            "timeframe": self.timeframe,
            "generated_at": self.generated_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "last_closed_candle": pd.Timestamp(self.frame.index[-1]).isoformat(),
            "closed_at": pd.Timestamp(self.frame.index[-1]).isoformat(),
            "candle_digest": candle_digest,
            "ai_review": deepcopy(self.signal.ai_review),
            "systemic_risk": deepcopy(self.systemic_risk),
        }

    @property
    def execution_allowed(self) -> bool:
        return bool(
            self.strategy.approved
            and self.systemic_risk.get("execution_allowed", True) is True
        )


def analyze_execution_frame(
    *,
    frame: pd.DataFrame,
    symbol: str,
    entry_mode: str,
    config_snapshot: dict[str, Any],
    timeframe: str | None = None,
) -> tuple[SMCSignal, StrategyResult, str]:
    """Pure closed-candle decision function shared by live and backtest paths."""
    profiles = validate_timeframe_profiles(config_snapshot.get("timeframe_profiles"))
    profile = profiles["roles"]["trigger"]
    effective_tf = timeframe or profile["timeframe"]
    indicator = resolve_profile_indicator_config(
        profile,
        config_snapshot.get("indicator_core"),
    )
    signal = SMCEngine(**profile["smc"]).analyze(
        frame.copy(),
        symbol,
        effective_tf,
        htf_bias="neutral",
        entry_mode=entry_mode,
        indicator_config=indicator,
        regime_config=config_snapshot.get("regime_policy"),
        tri_core_policy=config_snapshot.get("tri_core_policy"),
    )
    # VPIN Order Flow Toxicity
    try:
        from app.engines.vpin_engine import VPINEngine
        vpin_res = VPINEngine().calculate(frame)
        signal.order_flow = {
            "vpin": vpin_res.vpin,
            "toxic_flow_detected": vpin_res.toxic_flow_detected,
            "percentile_toxicity": vpin_res.percentile_toxicity,
            "status": vpin_res.status,
        }
    except Exception as exc:
        signal.order_flow = {"vpin": None, "toxic_flow_detected": False, "status": "unavailable"}

    strategy = StrategyEngine(strategy_config=config_snapshot).evaluate(signal)
    if strategy.effective_policy:
        signal.market_regime["effective_policy"] = strategy.effective_policy
    return signal, strategy, effective_tf


class ExecutionAnalysisService:
    def __init__(self) -> None:
        self._market = MarketDataEngine()
        self._cache: dict[tuple[str, ...], ExecutionAnalysis] = {}
        self._by_id: dict[str, ExecutionAnalysis] = {}
        self._locks: WeakValueDictionary = WeakValueDictionary()
        self._tasks: set[asyncio.Task] = set()

    @staticmethod
    def _config_snapshot() -> dict[str, Any]:
        config = read_strategy_config(STRATEGY_FILE)
        config["indicator_core"] = load_indicator_core_config()
        config["regime_policy"] = load_regime_policy_config()
        config["timeframe_profiles"] = load_timeframe_profiles()
        config["decision_authority"] = "tri_core_setup_engine"
        config["execution_scope"] = "execution_timeframe_only"
        cfg = get_settings()
        runtime = load_runtime_config()
        scanner_mode = str(runtime.get("scanner_ai_mode", cfg.scanner_ai_mode or "off")).lower()
        config["scanner_ai"] = {
            "mode": scanner_mode,
            "model": cfg.local_llm_model,
            "endpoint": cfg.local_llm_endpoint,
            "provider": runtime.get("provider", "local"),
        }
        config["execution_policy"] = {
            "entry_policy": str(runtime.get("auto_trade_entry_policy", "fixed")),
            "limit_timeout_bars": int(runtime.get("auto_trade_limit_timeout_bars", 3)),
        }
        return config

    def clear(self) -> None:
        self._cache.clear()
        self._by_id.clear()
        for task in self._tasks:
            task.cancel()

    async def close(self) -> None:
        self.clear()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    @staticmethod
    def _monitor_item_matches_analysis(
        item: dict[str, Any], analysis: ExecutionAnalysis
    ) -> bool:
        """Match an async annotation to its exact immutable candidate.

        A symbol can produce multiple snapshots while an AI provider is still
        running.  Symbol-only matching can therefore attach an old verdict to
        a newer candle, another exchange, or another timeframe.
        """
        snapshot = item.get("analysis_snapshot") or {}
        if not isinstance(snapshot, dict):
            return False
        return (
            str(snapshot.get("snapshot_id") or "") == analysis.snapshot_id
            and str(item.get("symbol") or "").upper() == analysis.symbol.upper()
            and str(item.get("exchange") or "").lower() == analysis.exchange.lower()
            and str(snapshot.get("timeframe") or item.get("timeframe") or "").lower()
            == analysis.timeframe.lower()
        )

    @classmethod
    def _sync_review_to_monitor(
        cls, analysis: ExecutionAnalysis, review: dict[str, Any]
    ) -> bool:
        from app.services.event_trigger import MarketMonitor

        monitor = MarketMonitor.get_instance()
        for item in monitor.recent_signals:
            if cls._monitor_item_matches_analysis(item, analysis):
                item["ai_review"] = deepcopy(review)
                return True
        return False

    async def _review(self, key, analysis, mode):
        from app.services.scanner_ai import scanner_ai
        try:
            setup = getattr(analysis.signal, "tri_core_setup", {}) or {}
            event_id = str(setup.get("event_id", ""))

            async def publish_progress(stage: str, details: dict[str, Any]) -> None:
                cached_progress = self._cache.get(key)
                if (
                    cached_progress is None
                    or cached_progress.snapshot_id != analysis.snapshot_id
                ):
                    return
                pending = cached_progress.clone()
                progress_review = deepcopy(pending.signal.ai_review or {})
                progress_review.update({
                    "status": "pending",
                    "verdict": "PENDING",
                    "review_stage": stage,
                    "stage_updated_at": datetime.now(timezone.utc).isoformat(),
                    **details,
                })
                pending.signal.ai_review = progress_review
                self._cache[key] = pending
                self._by_id[pending.snapshot_id] = pending
                self._sync_review_to_monitor(pending, progress_review)
                if event_id:
                    await decision_trace.record(
                        snapshot_id=analysis.snapshot_id,
                        event_id=event_id,
                        symbol=analysis.symbol,
                        stage="AI_REVIEW",
                        facts={
                            "status": "pending",
                            "verdict": "PENDING",
                            "review_stage": stage,
                            "input_snapshot_id": analysis.snapshot_id,
                            "input_event_id": event_id,
                            "input_exchange": analysis.exchange,
                            "input_timeframe": analysis.timeframe,
                            **details,
                        },
                    )

            reviewed = await scanner_ai.review(
                analysis,
                mode,
                on_progress=publish_progress,
            )
            cached = self._cache.get(key)
            review_payload = reviewed.signal.ai_review or {}
            if cached is None or cached.snapshot_id != analysis.snapshot_id:
                observability.inc(
                    "ai_trade_ai_reviews",
                    outcome="discarded",
                    reason="snapshot_replaced",
                )
                if event_id:
                    await decision_trace.record(
                        snapshot_id=analysis.snapshot_id,
                        event_id=event_id,
                        symbol=analysis.symbol,
                        stage="AI_REVIEW_DISCARDED",
                        facts={
                            "reason_code": "SNAPSHOT_REPLACED",
                            "status": review_payload.get("status"),
                            "verdict": review_payload.get("verdict"),
                            "failure_type": review_payload.get("failure_type"),
                        },
                    )
                return  # Cache invalidated/replaced while the provider was running.
            if str(review_payload.get("status", "")).lower() == "discarded":
                observability.inc(
                    "ai_trade_ai_reviews",
                    outcome="discarded",
                    reason=str(review_payload.get("failure_type") or "stale_review").lower(),
                )
                if event_id:
                    reason_code = str(review_payload.get("failure_type") or "STALE_REVIEW")
                    if reason_code == "SNAPSHOT_EXPIRED":
                        await candidate_lifecycle.expire(
                            symbol=analysis.symbol,
                            snapshot_id=analysis.snapshot_id,
                            event_id=event_id,
                            reason=str(review_payload.get("error") or "")[:300],
                        )
                    await decision_trace.record(
                        snapshot_id=analysis.snapshot_id,
                        event_id=event_id,
                        symbol=analysis.symbol,
                        stage="AI_REVIEW_DISCARDED",
                        facts={
                            "reason_code": reason_code,
                            "status": review_payload.get("status"),
                            "failure_type": review_payload.get("failure_type"),
                            "error": review_payload.get("error"),
                        },
                    )
                return
            if datetime.now(timezone.utc) >= analysis.valid_until:
                if event_id:
                    await candidate_lifecycle.expire(
                        symbol=analysis.symbol,
                        snapshot_id=analysis.snapshot_id,
                        event_id=event_id,
                        reason="AI response arrived after snapshot expiry",
                    )
                return
            verdict = str((reviewed.signal.ai_review or {}).get("verdict", "UNAVAILABLE")).upper()
            observability.inc(
                "ai_trade_ai_reviews",
                outcome=verdict.lower(),
                reason=str(review_payload.get("failure_type") or "none").lower(),
            )
            latency_ms = review_payload.get("latency_ms")
            if latency_ms is not None:
                try:
                    observability.observe(
                        "ai_trade_ai_review_latency_seconds",
                        float(latency_ms) / 1000.0,
                        provider=str(review_payload.get("provider") or "unknown"),
                    )
                except (TypeError, ValueError):
                    pass
            lifecycle_state = {
                "COHERENT": CandidateState.AI_COHERENT,
                "CAUTION": CandidateState.AI_CAUTION,
                "CONFLICT": CandidateState.AI_CONFLICT,
                "UNAVAILABLE": CandidateState.AI_UNAVAILABLE,
            }.get(verdict, CandidateState.AI_UNAVAILABLE)
            runtime = load_runtime_config()
            ai_policy = str(runtime.get("auto_trade_ai_policy", "strict")).lower()
            require_ai = bool(runtime.get("auto_trade_require_ai_approval", True))
            is_advisory = (ai_policy == "advisory") or (not require_ai)

            if event_id:
                if not is_advisory:
                    failure_type = str(review_payload.get("failure_type") or "").upper()
                    reason_code = (
                        f"AI_{failure_type}"
                        if verdict == "UNAVAILABLE" and failure_type
                        else f"AI_{verdict}"
                    )
                    changed = await candidate_lifecycle.transition(
                        symbol=analysis.symbol,
                        snapshot_id=analysis.snapshot_id,
                        event_id=event_id,
                        new_state=lifecycle_state,
                        reason_code=reason_code,
                        reason=str(
                            review_payload.get("reason")
                            or review_payload.get("error")
                            or ""
                        )[:300],
                    )
                    if not changed:
                        return  # Stale/replaced candidate cannot be mutated by async AI.
                review_payload = reviewed.signal.ai_review or {}
                await decision_trace.record(
                    snapshot_id=analysis.snapshot_id,
                    event_id=event_id,
                    symbol=analysis.symbol,
                    stage="AI_REVIEW",
                    occurred_at=str(review_payload.get("reviewed_at") or datetime.now(timezone.utc).isoformat()),
                    facts={
                        "provider": review_payload.get("provider"),
                        "model": review_payload.get("model"),
                        "prompt_version": review_payload.get("prompt_version"),
                        "input_snapshot_id": review_payload.get("input_snapshot_id"),
                        "input_event_id": review_payload.get("input_event_id"),
                        "input_exchange": review_payload.get("input_exchange"),
                        "input_timeframe": review_payload.get("input_timeframe"),
                        "status": review_payload.get("status"),
                        "verdict": review_payload.get("verdict"),
                        "review_stage": review_payload.get("review_stage"),
                        "stage_updated_at": review_payload.get("reviewed_at"),
                        "latency_ms": review_payload.get("latency_ms"),
                        "reason": review_payload.get("reason") or review_payload.get("error"),
                        "failure_type": review_payload.get("failure_type"),
                        "conflicts": review_payload.get("conflicts"),
                        "evidence": review_payload.get("evidence"),
                    },
                )
            # AI is an annotation on the deterministic input snapshot, not a
            # new market decision. Preserve the canonical snapshot identity.
            if str(review_payload.get("status", "")).lower() != "error":
                self._cache[key] = reviewed
                self._by_id[reviewed.snapshot_id] = reviewed
                try:
                    self._sync_review_to_monitor(reviewed, reviewed.signal.ai_review)
                except Exception as exc:
                    logger.debug(f"[ExecutionAnalysis] Sync to monitor failed: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception:
            observability.inc(
                "ai_trade_ai_reviews",
                outcome="error",
                reason="consumer_exception",
            )
            logger.exception("Scanner AI review failed")
            cached = self._cache.get(key)
            if cached is not None and cached.snapshot_id == analysis.snapshot_id:
                setup = getattr(analysis.signal, "tri_core_setup", {}) or {}
                event_id = str(setup.get("event_id", ""))
                if event_id:
                    runtime = load_runtime_config()
                    ai_policy = str(runtime.get("auto_trade_ai_policy", "strict")).lower()
                    require_ai = bool(runtime.get("auto_trade_require_ai_approval", True))
                    if ai_policy != "advisory" and require_ai:
                        await candidate_lifecycle.transition(
                            symbol=analysis.symbol,
                            snapshot_id=analysis.snapshot_id,
                            event_id=event_id,
                            new_state=CandidateState.AI_UNAVAILABLE,
                            reason_code="AI_REVIEW_ERROR",
                            reason="AI review unavailable",
                        )
                    await decision_trace.record(
                        snapshot_id=analysis.snapshot_id,
                        event_id=event_id,
                        symbol=analysis.symbol,
                        stage="AI_REVIEW",
                        facts={
                            "status": "error",
                            "verdict": "UNAVAILABLE",
                            "reason": "AI review unavailable",
                        },
                    )
                failed = cached.clone()
                failed.signal.ai_review = {
                    "status": "error",
                    "mode": mode,
                    "verdict": "UNAVAILABLE",
                    "input_snapshot_id": analysis.snapshot_id,
                    "input_event_id": event_id,
                    "input_symbol": analysis.symbol,
                    "input_exchange": analysis.exchange,
                    "input_timeframe": analysis.timeframe,
                    "advisory_only": True,
                    "error": "AI review unavailable",
                }
                failed.valid_until = datetime.now(timezone.utc) + timedelta(seconds=15)
                self._cache[key] = failed
                try:
                    self._sync_review_to_monitor(failed, failed.signal.ai_review)
                except Exception as exc:
                    logger.debug(f"[ExecutionAnalysis] Failed-review sync to monitor skipped: {exc}")

    @staticmethod
    def _should_trigger_ai_review(analysis: ExecutionAnalysis) -> tuple[bool, str]:
        setup = getattr(analysis.signal, "tri_core_setup", {}) or {}
        if analysis.strategy.approved and setup.get("actionable") is True:
            return True, "deterministic_tri_core_setup"
        return False, "No deterministic Tri-Core setup; AI was not requested"

    async def get(
        self,
        *,
        symbol: str,
        market_type: str,
        exchange: str,
        entry_mode: str,
        timeframe: str | None = None,
    ) -> ExecutionAnalysis:
        config = self._config_snapshot()
        profile = validate_timeframe_profiles(config.get("timeframe_profiles"))["roles"]["trigger"]
        effective_tf = timeframe or profile["timeframe"]
        if effective_tf != profile["timeframe"]:
            raise ValueError("Requested timeframe is not the configured execution timeframe")
        config_hash = hashlib.sha256(
            json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:20]
        key = (symbol.upper(), market_type.lower(), exchange.lower(), entry_mode, effective_tf, config_hash)
        now = datetime.now(timezone.utc)
        cached = self._cache.get(key)
        if cached and now < cached.valid_until:
            return cached.clone()

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = datetime.now(timezone.utc)
            cached = self._cache.get(key)
            if cached and now < cached.valid_until:
                return cached.clone()
            frame = await self._market.get_ohlcv(
                symbol,
                effective_tf,
                market_type,
                exchange,
                limit=int(profile["lookback"]),
                closed_only=True,
            )
            if frame.empty:
                raise ValueError("No closed execution-timeframe candles available")
            signal, strategy, res_timeframe = analyze_execution_frame(
                frame=frame,
                symbol=symbol,
                entry_mode=entry_mode,
                config_snapshot=config,
                timeframe=effective_tf,
            )

            # Hourly OI/funding is research context only.  It may be attached
            # to a 15M snapshot for display/ablation, but it never causes a
            # Strategy re-evaluation and never changes snapshot identity.
            if market_type.lower() == "crypto":
                try:
                    from app.engines.sentiment_derivatives_engine import SentimentDerivativesEngine
                    p_change = 0.0
                    bars_per_hour = 4 if effective_tf == "15m" else 1
                    if len(frame) > bars_per_hour:
                        c0 = float(frame["close"].iloc[-1 - bars_per_hour])
                        c1 = float(frame["close"].iloc[-1])
                        if c0 > 0:
                            p_change = ((c1 - c0) / c0) * 100.0
                    deriv_res = await SentimentDerivativesEngine.get_sentiment(
                        symbol, market_type, exchange, price_change_pct_1h=p_change
                    )
                    signal.derivatives_sentiment = {
                        **deriv_res.to_dict(),
                        "advisory_only": True,
                        "decision_authority": False,
                        "source_timeframe": "1h",
                    }
                except Exception as exc:
                    logger.debug(f"[ExecutionAnalysis] Derivatives sentiment fetch skipped: {exc}")

            # Cross-asset BTC context is a downstream systemic-risk fact.  It
            # never mutates Tri-Core direction/grade, but it is bound into this
            # canonical snapshot so Chart, Scanner and execution see one result.
            setup = getattr(signal, "tri_core_setup", {}) or {}
            setup_direction = str(setup.get("direction") or "wait").lower()
            systemic_risk: dict[str, Any] = {
                "status": "NOT_APPLICABLE",
                "execution_allowed": True,
                "reason_code": "NOT_APPLICABLE",
            }
            if (
                market_type.lower() == "crypto"
                and setup.get("actionable") is True
                and setup_direction in {"long", "short"}
            ):
                from app.services.btc_market_anchor import (
                    btc_market_anchor,
                    is_crypto_altcoin,
                )

                if is_crypto_altcoin(symbol, market_type, exchange):
                    try:
                        btc_analysis = await self.get(
                            symbol="BTC/USDT",
                            market_type="crypto",
                            exchange="binance",
                            entry_mode=entry_mode,
                            timeframe=effective_tf,
                        )
                        allowed, reason, systemic_risk = await btc_market_anchor.evaluate(
                            symbol,
                            setup_direction,
                            market_type=market_type,
                            exchange=exchange,
                            expected_timeframe=effective_tf,
                            btc_analysis=btc_analysis,
                        )
                        systemic_risk = {
                            **systemic_risk,
                            "execution_allowed": bool(allowed),
                            "reason": reason,
                            "direction": setup_direction,
                        }
                    except Exception as exc:
                        logger.warning(
                            f"[SystemicRisk] BTC assessment unavailable for {symbol}: {exc}"
                        )
                        systemic_risk = {
                            "status": "UNAVAILABLE",
                            "execution_allowed": False,
                            "reason_code": "BTC_ANCHOR_UNAVAILABLE",
                            "reason": "BTC systemic-risk assessment is unavailable",
                            "error_type": type(exc).__name__,
                            "direction": setup_direction,
                        }

            signal.systemic_risk = deepcopy(systemic_risk)
            if not systemic_risk.get("execution_allowed", True):
                strategy.approved = False
                strategy.direction = "wait"
                strategy.rejection_reasons.append(
                    f"BTC Market Anchor: {systemic_risk.get('reason') or systemic_risk.get('reason_code')}"
                )

            digest = hashlib.sha256()
            digest.update("|".join(key).encode("utf-8"))
            digest.update(_frame_digest(frame))
            digest.update(
                json.dumps(systemic_risk, sort_keys=True, default=str).encode("utf-8")
            )
            own_valid_until = _next_refresh_at(frame.index[-1], res_timeframe)
            btc_valid_until_raw = systemic_risk.get("valid_until")
            if btc_valid_until_raw:
                try:
                    btc_valid_until = datetime.fromisoformat(
                        str(btc_valid_until_raw).replace("Z", "+00:00")
                    )
                    own_valid_until = min(own_valid_until, btc_valid_until)
                except ValueError:
                    pass
            analysis = ExecutionAnalysis(
                symbol=symbol,
                timeframe=res_timeframe,
                frame=frame.copy(),
                signal=signal,
                strategy=strategy,
                config_snapshot=deepcopy(config),
                snapshot_id=digest.hexdigest()[:24],
                generated_at=now,
                valid_until=own_valid_until,
                market_type=market_type,
                exchange=exchange,
                entry_mode=entry_mode,
                systemic_risk=systemic_risk,
            )
            self._cache[key] = analysis
            self._by_id[analysis.snapshot_id] = analysis

            event_id = str(setup.get("event_id", ""))
            if event_id:
                record = await candidate_lifecycle.register(
                    symbol=symbol,
                    snapshot_id=analysis.snapshot_id,
                    event_id=event_id,
                    ready=bool(
                        setup.get("actionable") is True
                        and strategy.approved
                        and str(setup.get("direction", "")).lower() in {"long", "short"}
                    ),
                )
                analysis.signal.tri_core_setup["candidate_lifecycle_state"] = record.state.value
                await decision_trace.record(
                    snapshot_id=analysis.snapshot_id,
                    event_id=event_id,
                    symbol=symbol,
                    stage="SNAPSHOT",
                    occurred_at=now.isoformat(),
                    facts={
                        "decision_authority": "tri_core_setup_engine",
                        "timeframe": analysis.timeframe,
                        "generated_at": analysis.generated_at.isoformat(),
                        "valid_until": analysis.valid_until.isoformat(),
                        "candle_digest": _frame_digest(frame),
                        "config_hash": fingerprint(config),
                        "policy_version": setup.get("policy_version"),
                        "calibration_status": setup.get("calibration_status"),
                        "direction": setup.get("direction"),
                        "setup_type": setup.get("setup_type"),
                        "strategy_family": setup.get("strategy_family"),
                        "entry_policy": setup.get("entry_policy"),
                        "order_type": setup.get("order_type"),
                        "entry": signal.entry,
                        "stop_loss": signal.stop_loss,
                        "take_profit": signal.take_profit,
                        "structural_rr": signal.risk_reward,
                        "grade": setup.get("grade"),
                    },
                )
                await decision_trace.record(
                    snapshot_id=analysis.snapshot_id,
                    event_id=event_id,
                    symbol=symbol,
                    stage="SYSTEMIC_RISK",
                    occurred_at=now.isoformat(),
                    facts=deepcopy(systemic_risk),
                )

            if len(self._by_id) > 500:
                self._by_id.pop(next(iter(self._by_id)), None)
            mode = config.get("scanner_ai", {}).get("mode", "off")
            if exchange.lower() == "innovestx":
                mode = "shadow" if mode != "off" else "off"
            if mode in {"shadow", "paper"}:
                baseline = analysis.clone()
                should_review, filter_reason = self._should_trigger_ai_review(analysis)
                if should_review:
                    if event_id:
                        await candidate_lifecycle.transition(
                            symbol=symbol,
                            snapshot_id=analysis.snapshot_id,
                            event_id=event_id,
                            new_state=CandidateState.AI_REVIEW_PENDING,
                            reason_code="AI_REVIEW_QUEUED",
                        )
                    analysis.signal.ai_review = {
                        "status": "pending",
                        "review_stage": "QUEUED",
                        "mode": mode,
                        "input_snapshot_id": analysis.snapshot_id,
                        "input_event_id": event_id,
                        "input_symbol": analysis.symbol,
                        "input_exchange": analysis.exchange,
                        "input_timeframe": analysis.timeframe,
                        "verdict": "PENDING",
                        "advisory_only": True,
                    }
                    if len(self._tasks) < 64:
                        if event_id:
                            await decision_trace.record(
                                snapshot_id=analysis.snapshot_id,
                                event_id=event_id,
                                symbol=analysis.symbol,
                                stage="AI_REVIEW",
                                facts={
                                    "status": "pending",
                                    "verdict": "PENDING",
                                    "review_stage": "QUEUED",
                                    "input_snapshot_id": analysis.snapshot_id,
                                    "input_event_id": event_id,
                                    "input_exchange": analysis.exchange,
                                    "input_timeframe": analysis.timeframe,
                                },
                            )
                        observability.inc(
                            "ai_trade_ai_review_requests",
                            outcome="queued",
                            mode=mode,
                        )
                        task = asyncio.create_task(self._review(key, baseline, mode))
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)
                    else:
                        analysis.signal.ai_review["status"] = "queue_full"
                        analysis.signal.ai_review["review_stage"] = "QUEUE_FULL"
                        if event_id:
                            await decision_trace.record(
                                snapshot_id=analysis.snapshot_id,
                                event_id=event_id,
                                symbol=analysis.symbol,
                                stage="AI_REVIEW",
                                facts={
                                    "status": "queue_full",
                                    "verdict": "UNAVAILABLE",
                                    "review_stage": "QUEUE_FULL",
                                    "input_snapshot_id": analysis.snapshot_id,
                                    "input_event_id": event_id,
                                },
                            )
                        observability.inc(
                            "ai_trade_ai_review_requests",
                            outcome="queue_full",
                            mode=mode,
                        )
                else:
                    observability.inc(
                        "ai_trade_ai_review_requests",
                        outcome="not_requested",
                        mode=mode,
                    )
                    analysis.signal.ai_review = {
                        "status": "not_requested",
                        "mode": mode,
                        "input_snapshot_id": analysis.snapshot_id,
                        "input_event_id": event_id,
                        "input_symbol": analysis.symbol,
                        "input_exchange": analysis.exchange,
                        "input_timeframe": analysis.timeframe,
                        "reason": filter_reason,
                        "advisory_only": True,
                    }
            if len(self._cache) > 500:
                self._cache.pop(next(iter(self._cache)), None)
            return analysis.clone()

    def get_by_snapshot_id(self, snapshot_id: str) -> ExecutionAnalysis | None:
        analysis = self._by_id.get(snapshot_id)
        return analysis.clone() if analysis is not None else None


execution_analyses = ExecutionAnalysisService()
