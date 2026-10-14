"""Bar-by-bar Counterfactual Market Replay Engine for Paper Trading.

Replays candidate setups rejected or filtered by the AI gate (CONFLICT, UNAVAILABLE,
STAND_ASIDE) against subsequent market OHLCV bars using deterministic execution
simulation to evaluate true AI alpha, avoided losses, and missed profits.

Includes:
- Strict next-bar candle boundary policy (zero look-ahead bias)
- Assumption & data fingerprinting for automatic cache invalidation
- Rigorous coverage tracking and tiered confidence (replay_partial vs replay_validated)
- Multi-dimensional breakdown by symbol, regime, direction, and timeframe
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from loguru import logger

from app.core.config import get_settings
from app.core.json_store import read_json, write_json
from app.engines.backtest_engine import ExecutionAssumptions, simulate_execution
from app.services.paper_evaluation import calculate_paper_metrics

_CACHE_FILE = Path("data/counterfactual_replay_cache.json")
REPLAY_ENGINE_VERSION = "phase5.v2"
COVERAGE_THRESHOLD_FOR_VALIDATION = 0.95


def _normalize_direction(direction: Any) -> str:
    raw = str(direction or "").strip().lower()
    if raw in {"long", "buy"}:
        return "long"
    if raw in {"short", "sell"}:
        return "short"
    return "unknown"


def _parse_timestamp(val: Any) -> pd.Timestamp | None:
    if val is None:
        return None
    try:
        ts = pd.to_datetime(val)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts
    except Exception:
        return None


def is_candidate_geometry_valid(candidate: dict[str, Any]) -> bool:
    """Check if candidate setup has physically valid trade geometry."""
    direction = _normalize_direction(candidate.get("direction"))
    if direction not in {"long", "short"}:
        return False
    try:
        entry = float(candidate.get("entry") or 0.0)
        stop_loss = float(candidate.get("stop_loss") or 0.0)
        take_profit = float(candidate.get("take_profit") or 0.0)
    except (TypeError, ValueError):
        return False
    if entry <= 0 or stop_loss <= 0 or take_profit <= 0:
        return False
    if direction == "long":
        return stop_loss < entry < take_profit
    else:
        return take_profit < entry < stop_loss


def compute_assumptions_hash(assumptions: ExecutionAssumptions, risk_usd: float) -> str:
    """Compute deterministic hash of execution assumptions for cache validation."""
    payload = {
        "engine_version": REPLAY_ENGINE_VERSION,
        "fee_bps": float(assumptions.fee_bps),
        "spread_bps": float(assumptions.spread_bps),
        "slippage_bps": float(assumptions.slippage_bps),
        "latency_bars": int(assumptions.latency_bars),
        "entry_timeout_bars": int(assumptions.entry_timeout_bars),
        "max_holding_bars": int(assumptions.max_holding_bars),
        "max_volume_participation": float(assumptions.max_volume_participation),
        "max_fill_fraction_per_bar": float(assumptions.max_fill_fraction_per_bar),
        "zero_volume_fill_fraction": float(assumptions.zero_volume_fill_fraction),
        "risk_per_trade_usd": float(risk_usd),
    }
    dumped = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:16]


class CounterfactualReplayEngine:
    """Deterministic bar-by-bar replay engine for counterfactual candidate setups."""

    def __init__(
        self,
        *,
        assumptions: ExecutionAssumptions | None = None,
        cache_path: Path | None = None,
        risk_per_trade_usd: float = 100.0,
    ) -> None:
        self.assumptions = assumptions or ExecutionAssumptions(
            fee_bps=10.0,
            spread_bps=5.0,
            slippage_bps=3.0,
            latency_bars=1,
            entry_timeout_bars=12,
            max_holding_bars=48,
        )
        self.cache_path = cache_path or _CACHE_FILE
        self.risk_per_trade_usd = risk_per_trade_usd
        self.assumption_hash = compute_assumptions_hash(self.assumptions, self.risk_per_trade_usd)

    def load_cache(self) -> dict[str, dict[str, Any]]:
        """Load persisted replay results, invalidating entries with mismatched fingerprints."""
        try:
            raw = read_json(self.cache_path, dict)
            validated: dict[str, dict[str, Any]] = {}
            for cid, entry in raw.items():
                if not isinstance(entry, dict):
                    continue
                # Validate fingerprint envelope
                if (
                    entry.get("replay_engine_version") == REPLAY_ENGINE_VERSION
                    and entry.get("assumption_hash") == self.assumption_hash
                    and isinstance(entry.get("result"), dict)
                ):
                    validated[cid] = entry["result"]
                elif "result" not in entry and entry.get("status"):
                    # Unwrapped legacy entry: invalidate because it has no assumption hash
                    continue
            return validated
        except Exception as exc:
            logger.warning(f"[ReplayEngine] Cache read error, using empty: {exc}")
            return {}

    def save_cache(self, results_dict: dict[str, dict[str, Any]]) -> None:
        """Persist replay results with configuration fingerprint envelope."""
        try:
            envelope: dict[str, dict[str, Any]] = {}
            for cid, res in results_dict.items():
                envelope[cid] = {
                    "candidate_id": cid,
                    "replay_engine_version": REPLAY_ENGINE_VERSION,
                    "assumption_hash": self.assumption_hash,
                    "result": res,
                }
            write_json(self.cache_path, envelope)
        except Exception as exc:
            logger.error(f"[ReplayEngine] Cache save failed: {exc}")

    def replay_candidate(
        self,
        candidate: dict[str, Any],
        future_bars: pd.DataFrame,
    ) -> dict[str, Any]:
        """Replay a single candidate against subsequent OHLCV bars bar-by-bar."""
        cid = str(candidate.get("candidate_id") or f"{candidate.get('snapshot_id')}:{candidate.get('event_id')}")
        symbol = str(candidate.get("symbol") or "UNKNOWN")
        raw_direction = candidate.get("direction")
        direction = _normalize_direction(raw_direction)

        try:
            entry = float(candidate.get("entry") or 0.0)
            stop_loss = float(candidate.get("stop_loss") or 0.0)
            take_profit = float(candidate.get("take_profit") or 0.0)
        except (TypeError, ValueError):
            entry, stop_loss, take_profit = 0.0, 0.0, 0.0

        timeframe = str(candidate.get("timeframe") or "15m")
        occurred_at = str(candidate.get("occurred_at") or "")
        verdict = str(candidate.get("verdict") or "UNKNOWN").upper()
        reason_code = str(candidate.get("reason_code") or f"AI_{verdict}")
        filter_reason = str(candidate.get("filter_reason") or "")
        market_regime = str(candidate.get("market_regime") or "unknown")

        base_res: dict[str, Any] = {
            "candidate_id": cid,
            "snapshot_id": candidate.get("snapshot_id"),
            "event_id": candidate.get("event_id"),
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "timeframe": timeframe,
            "occurred_at": occurred_at,
            "verdict": verdict,
            "reason_code": reason_code,
            "filter_reason": filter_reason,
            "market_regime": market_regime,
            "status": "unfilled",
            "outcome_type": "invalid_geometry",
            "realized_pnl_net": 0.0,
            "gross_pnl": 0.0,
            "fees": 0.0,
            "slippage_cost": 0.0,
            "spread_cost": 0.0,
            "r_multiple": 0.0,
            "exit_reason": None,
            "exit_price": None,
            "average_entry": None,
            "holding_bars": 0,
            "fill_rate": 0.0,
            "replayed_at": datetime.now(timezone.utc).isoformat(),
        }

        if direction not in {"long", "short"}:
            base_res["status"] = "invalid_geometry"
            base_res["outcome_type"] = "invalid_geometry"
            return base_res

        if entry <= 0 or stop_loss <= 0 or take_profit <= 0:
            base_res["status"] = "invalid_geometry"
            base_res["outcome_type"] = "invalid_geometry"
            return base_res

        geometry_ok = (
            stop_loss < entry < take_profit
            if direction == "long"
            else take_profit < entry < stop_loss
        )
        if not geometry_ok:
            base_res["status"] = "invalid_geometry"
            base_res["outcome_type"] = "invalid_geometry"
            return base_res

        if future_bars is None or future_bars.empty:
            base_res["status"] = "insufficient_data"
            base_res["outcome_type"] = "insufficient_data"
            return base_res

        # Calculate normalized position size based on standard risk ($100 per trade)
        risk_distance = abs(entry - stop_loss)
        requested_quantity = self.risk_per_trade_usd / risk_distance if risk_distance > 0 else 1.0

        try:
            exec_sim = simulate_execution(
                direction=direction,
                order_type="limit",
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                requested_quantity=requested_quantity,
                future_bars=future_bars,
                assumptions=self.assumptions,
            )
        except Exception as exc:
            logger.debug(f"[ReplayEngine] Simulation error for {cid}: {exc}")
            base_res["status"] = "error"
            base_res["outcome_type"] = "insufficient_data"
            return base_res

        status = exec_sim.get("status") or "unfilled"
        base_res["status"] = status
        base_res["fill_rate"] = float(exec_sim.get("fill_rate") or 0.0)

        if status == "closed":
            net_pnl = float(exec_sim.get("net_pnl") or 0.0)
            r_mult = float(exec_sim.get("r_multiple") or 0.0)
            exit_reason = exec_sim.get("exit_reason")

            base_res["realized_pnl_net"] = round(net_pnl, 2)
            base_res["gross_pnl"] = round(float(exec_sim.get("gross_pnl") or 0.0), 2)
            base_res["fees"] = round(float(exec_sim.get("fees") or 0.0), 2)
            base_res["r_multiple"] = round(r_mult, 4)
            base_res["exit_reason"] = exit_reason
            base_res["exit_price"] = exec_sim.get("exit_price")
            base_res["average_entry"] = exec_sim.get("average_entry")
            base_res["holding_bars"] = exec_sim.get("holding_bars", 0)

            fills = exec_sim.get("fills") or []
            base_res["slippage_cost"] = round(sum(f.get("slippage_cost", 0.0) for f in fills), 2)
            base_res["spread_cost"] = round(sum(f.get("spread_cost", 0.0) for f in fills), 2)

            if net_pnl < 0 or exit_reason == "stop_loss":
                base_res["outcome_type"] = "avoided_loss"
            elif net_pnl > 0 or exit_reason == "take_profit":
                base_res["outcome_type"] = "missed_profit"
            else:
                base_res["outcome_type"] = "breakeven"
        elif status == "unfilled":
            base_res["outcome_type"] = "neutral_unfilled"
            base_res["exit_reason"] = "entry_timeout"
        else:
            base_res["outcome_type"] = "insufficient_data"

        return base_res

    async def replay_candidates_batch(
        self,
        candidates: list[dict[str, Any]],
        *,
        market_data_engine: Any = None,
        candle_provider_sync: Any = None,
        force_recompute: bool = False,
    ) -> list[dict[str, Any]]:
        """Replay candidate setups, utilizing cache for past closed events.
        
        Uses Strict Next-Bar Execution Boundary (b_idx > occurred_ts) to eliminate look-ahead bias.
        """
        cache = self.load_cache() if not force_recompute else {}
        results: list[dict[str, Any]] = []
        pending_by_symbol_tf: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

        for candidate in candidates:
            cid = str(candidate.get("candidate_id") or f"{candidate.get('snapshot_id')}:{candidate.get('event_id')}")
            if not force_recompute and cid in cache:
                cached_res = cache[cid]
                if cached_res.get("status") in {"closed", "unfilled", "invalid_geometry"}:
                    results.append(cached_res)
                    continue

            symbol = str(candidate.get("symbol") or "UNKNOWN")
            tf = str(candidate.get("timeframe") or "15m")
            pending_by_symbol_tf[(symbol, tf)].append(candidate)

        if not pending_by_symbol_tf:
            return results

        # Group and fetch candles once per (symbol, timeframe)
        for (symbol, tf), c_list in pending_by_symbol_tf.items():
            bars: pd.DataFrame = pd.DataFrame()
            if candle_provider_sync is not None:
                bars = candle_provider_sync(symbol, tf)
            elif market_data_engine is not None:
                try:
                    bars = await market_data_engine.get_ohlcv(symbol, tf, limit=1500)
                except Exception as exc:
                    logger.warning(f"[ReplayEngine] Market data fetch failed for {symbol} {tf}: {exc}")

            for candidate in c_list:
                cid = str(candidate.get("candidate_id") or f"{candidate.get('snapshot_id')}:{candidate.get('event_id')}")
                occurred_ts = _parse_timestamp(candidate.get("occurred_at"))

                if bars is not None and not bars.empty and occurred_ts is not None:
                    # Strict Next-Bar Execution Boundary Policy:
                    # To eliminate look-ahead bias, replay begins strictly on the next open candle
                    # whose open timestamp is after the candidate's creation timestamp (b_idx > occurred_ts).
                    try:
                        b_idx = bars.index
                        if b_idx.tzinfo is None:
                            b_idx = b_idx.tz_localize("UTC")
                        else:
                            b_idx = b_idx.tz_convert("UTC")
                        future = bars[b_idx > occurred_ts]
                    except Exception as exc:
                        logger.debug(f"[ReplayEngine] Timestamp parsing error for {cid}: {exc}")
                        future = pd.DataFrame()  # Strict policy: do not fallback to arbitrary tail; mark insufficient_data
                else:
                    future = pd.DataFrame()

                res = self.replay_candidate(candidate, future)
                cache[cid] = res
                results.append(res)

        self.save_cache(cache)
        return results

    @staticmethod
    def summarize_replay(
        replay_results: list[dict[str, Any]],
        *,
        total_filtered_candidates: list[dict[str, Any]] | None = None,
        initial_equity: float = 10_000.0,
    ) -> dict[str, Any]:
        """Aggregate counterfactual metrics: coverage, avoided losses, missed profits, net alpha, and breakdowns."""
        total_replayed = len(replay_results)
        by_outcome = Counter(r.get("outcome_type") for r in replay_results)
        by_verdict = defaultdict(lambda: Counter())

        # Multi-dimensional breakdowns
        by_symbol = defaultdict(lambda: {"avoided_losses": 0, "missed_profits": 0, "unfilled": 0, "net_dollar_alpha": 0.0})
        by_regime = defaultdict(lambda: {"avoided_losses": 0, "missed_profits": 0, "unfilled": 0, "net_dollar_alpha": 0.0})
        by_direction = defaultdict(lambda: {"avoided_losses": 0, "missed_profits": 0, "unfilled": 0, "net_dollar_alpha": 0.0})
        by_timeframe = defaultdict(lambda: {"avoided_losses": 0, "missed_profits": 0, "unfilled": 0, "net_dollar_alpha": 0.0})

        avoided_losses: list[dict[str, Any]] = []
        missed_profits: list[dict[str, Any]] = []
        unfilled_list: list[dict[str, Any]] = []

        for r in replay_results:
            outcome = r.get("outcome_type")
            verdict = str(r.get("verdict") or "UNKNOWN")
            sym = str(r.get("symbol") or "UNKNOWN")
            reg = str(r.get("market_regime") or "unknown")
            dir_ = str(r.get("direction") or "unknown")
            tf = str(r.get("timeframe") or "unknown")
            pnl = float(r.get("realized_pnl_net") or 0.0)

            by_verdict[verdict][outcome] += 1

            for d, k in [(by_symbol, sym), (by_regime, reg), (by_direction, dir_), (by_timeframe, tf)]:
                if outcome == "avoided_loss":
                    d[k]["avoided_losses"] += 1
                    d[k]["net_dollar_alpha"] = round(d[k]["net_dollar_alpha"] + abs(pnl), 2)
                elif outcome == "missed_profit":
                    d[k]["missed_profits"] += 1
                    d[k]["net_dollar_alpha"] = round(d[k]["net_dollar_alpha"] - abs(pnl), 2)
                elif outcome == "neutral_unfilled":
                    d[k]["unfilled"] += 1

            if outcome == "avoided_loss":
                avoided_losses.append(r)
            elif outcome == "missed_profit":
                missed_profits.append(r)
            elif outcome == "neutral_unfilled":
                unfilled_list.append(r)

        total_saved_usd = sum(abs(float(r.get("realized_pnl_net") or 0.0)) for r in avoided_losses)
        total_saved_r = sum(abs(float(r.get("r_multiple") or 0.0)) for r in avoided_losses)

        total_missed_usd = sum(float(r.get("realized_pnl_net") or 0.0) for r in missed_profits)
        total_missed_r = sum(float(r.get("r_multiple") or 0.0) for r in missed_profits)

        net_alpha_usd = total_saved_usd - total_missed_usd
        net_alpha_r = total_saved_r - total_missed_r

        decisive_count = len(avoided_losses) + len(missed_profits)
        filter_precision = (
            round(len(avoided_losses) / decisive_count, 4) if decisive_count > 0 else 0.0
        )
        closed_replays = len(avoided_losses) + len(missed_profits)
        win_rate_of_blocked = (
            round(len(missed_profits) / closed_replays, 4) if closed_replays > 0 else 0.0
        )

        # Coverage Calculation & Tiered Status
        if total_filtered_candidates is not None:
            all_filtered = list(total_filtered_candidates)
            eligible_candidates = [c for c in all_filtered if is_candidate_geometry_valid(c)]
            total_eligible_count = len(eligible_candidates)
            invalid_geom_count = len(all_filtered) - total_eligible_count

            eligible_cids = {
                str(c.get("candidate_id") or f"{c.get('snapshot_id')}:{c.get('event_id')}")
                for c in eligible_candidates
            }
            replayed_cids = {
                r.get("candidate_id")
                for r in replay_results
                if r.get("status") in {"closed", "unfilled"}
            }
            replayed_eligible_count = len(replayed_cids & eligible_cids)
            coverage_pct = (
                round(replayed_eligible_count / total_eligible_count, 4)
                if total_eligible_count > 0
                else 1.0
            )
        else:
            total_eligible_count = total_replayed
            invalid_geom_count = by_outcome.get("invalid_geometry", 0)
            replayed_eligible_count = total_replayed - invalid_geom_count
            coverage_pct = 1.0 if total_replayed > 0 else 0.0

        if total_replayed == 0:
            replay_status = "replay_unexecuted"
        elif coverage_pct < COVERAGE_THRESHOLD_FOR_VALIDATION:
            replay_status = "replay_partial"
        elif by_outcome.get("invalid_geometry", 0) > (total_replayed * 0.5):
            replay_status = "replay_invalid_or_incomplete"
        else:
            replay_status = "replay_validated"

        decisive_samples = len(avoided_losses) + len(missed_profits)
        if decisive_samples >= 30:
            sample_size_assessment = "statistically_meaningful"
        elif decisive_samples > 0:
            sample_size_assessment = "insufficient_decisive_samples"
        else:
            sample_size_assessment = "no_decisive_samples"

        return {
            "replay_status": replay_status,
            "coverage_pct": coverage_pct,
            "coverage_threshold_for_validation": COVERAGE_THRESHOLD_FOR_VALIDATION,
            "total_candidates_replayed": total_replayed,
            "replayed_eligible_candidates": replayed_eligible_count,
            "total_eligible_filtered_candidates": total_eligible_count,
            "executed_in_simulation": closed_replays,
            "decisive_samples_count": decisive_samples,
            "required_decisive_samples": 30,
            "unfilled_in_simulation": len(unfilled_list),
            "invalid_geometry_count": invalid_geom_count,
            "insufficient_data_count": by_outcome.get("insufficient_data", 0),
            "sample_size_assessment": sample_size_assessment,
            "avoided_losses": {
                "count": len(avoided_losses),
                "total_dollar_saved": round(total_saved_usd, 2),
                "total_r_saved": round(total_saved_r, 2),
                "avg_r_saved": round(total_saved_r / max(1, len(avoided_losses)), 2),
            },
            "missed_profits": {
                "count": len(missed_profits),
                "total_dollar_missed": round(total_missed_usd, 2),
                "total_r_missed": round(total_missed_r, 2),
                "avg_r_missed": round(total_missed_r / max(1, len(missed_profits)), 2),
            },
            "net_ai_filter_alpha": {
                "net_dollar_alpha": round(net_alpha_usd, 2),
                "net_r_alpha": round(net_alpha_r, 2),
                "is_alpha_positive": net_alpha_usd >= 0.0,
                "interpretation": (
                    f"AI filter saved ${round(total_saved_usd, 2)} from bad trades while missing "
                    f"${round(total_missed_usd, 2)} in profitable trades (Net Alpha: ${round(net_alpha_usd, 2)})"
                ),
            },
            "filter_precision": filter_precision,
            "simulated_win_rate_of_blocked_trades": win_rate_of_blocked,
            "breakdown_by_verdict": {k: dict(v) for k, v in by_verdict.items()},
            "breakdown_by_symbol": dict(by_symbol),
            "breakdown_by_regime": dict(by_regime),
            "breakdown_by_direction": dict(by_direction),
            "breakdown_by_timeframe": dict(by_timeframe),
        }

    @staticmethod
    def build_pure_tri_core_baseline(
        executed_trades: list[dict[str, Any]],
        replay_results: list[dict[str, Any]],
        *,
        initial_equity: float = 10_000.0,
    ) -> dict[str, Any]:
        """Combine unscaled executed trades with replayed filtered trades into Pure Tri-Core portfolio."""
        unscaled_trades: list[dict[str, Any]] = []
        for trade in executed_trades:
            sim = dict(trade)
            source_payload = trade.get("source_payload") or {}
            raw_mult = (
                trade.get("ai_risk_multiplier")
                if trade.get("ai_risk_multiplier") is not None
                else (source_payload.get("ai_risk_multiplier") if isinstance(source_payload, dict) else None)
            )
            try:
                mult = float(raw_mult) if raw_mult is not None else 1.0
            except (TypeError, ValueError):
                mult = 1.0

            if 0.0 < mult < 0.99:
                pnl = float(trade.get("realized_pnl_net") or 0.0)
                sim["realized_pnl_net"] = pnl / mult
                risk = float(trade.get("estimated_risk") or 0.0)
                if risk > 0:
                    sim["estimated_risk"] = risk / mult
            unscaled_trades.append(sim)

        replayed_as_trades: list[dict[str, Any]] = []
        for r in replay_results:
            if r.get("status") == "closed":
                replayed_as_trades.append({
                    "decision_trace_id": r.get("candidate_id"),
                    "status": "closed",
                    "realized_pnl_net": r.get("realized_pnl_net", 0.0),
                    "estimated_risk": 100.0,
                    "fees_total": r.get("fees", 0.0),
                    "spread_cost_total": r.get("spread_cost", 0.0),
                    "slippage_cost_total": r.get("slippage_cost", 0.0),
                    "opened_at": r.get("occurred_at"),
                    "closed_at": r.get("replayed_at"),
                    "max_r_multiple": r.get("r_multiple"),
                    "ai_review_verdict": "BLOCKED_SIMULATED",
                    "ai_risk_multiplier": 1.0,
                    "market_regime": r.get("market_regime", "replayed"),
                    "entry_timing": "replayed_limit",
                })

        pure_portfolio = unscaled_trades + replayed_as_trades
        return calculate_paper_metrics(pure_portfolio, initial_equity=initial_equity)
