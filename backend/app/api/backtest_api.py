"""Phase 3 out-of-sample backtest, metrics and deterministic release gates."""

from __future__ import annotations

import asyncio
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_api_key
from app.engines.backtest_engine import (
    ExecutionAssumptions,
    ReleaseCriteria,
    evaluate_release_gate,
    run_walk_forward_backtest,
)
from app.engines.market_data import MarketDataEngine
from app.engines.strategy_engine import StrategyEngine
from app.models.base import get_db
from app.models.phase3 import (
    BacktestRun,
    FillLedgerRecord,
    JsonMigrationCheckpoint,
    OrderLedgerRecord,
    ReleaseGateEvaluation,
    TradeLedgerRecord,
)
from app.services.evidence import current_decision_config, fingerprint


router = APIRouter()
_market = MarketDataEngine()
_strategy = StrategyEngine()


class ExecutionAssumptionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fee_bps: float = Field(default=10.0, ge=0, le=500)
    spread_bps: float = Field(default=5.0, ge=0, le=500)
    slippage_bps: float = Field(default=3.0, ge=0, le=500)
    latency_bars: int = Field(default=1, ge=0, le=20)
    entry_timeout_bars: int = Field(default=3, ge=1, le=100)
    max_holding_bars: int = Field(default=24, ge=1, le=1000)
    max_volume_participation: float = Field(default=0.01, gt=0, le=1)
    max_fill_fraction_per_bar: float = Field(default=0.50, gt=0, le=1)
    zero_volume_fill_fraction: float = Field(default=0.25, gt=0, le=1)


class ReleaseCriteriaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_completed_trades: int = Field(default=30, ge=1, le=100_000)
    min_expectancy_r: float = Field(default=0.05, ge=-5, le=10)
    min_profit_factor: float = Field(default=1.15, ge=0, le=100)
    max_drawdown_pct: float = Field(default=12.0, gt=0, le=100)
    min_fill_rate: float = Field(default=0.70, ge=0, le=1)
    min_regimes_tested: int = Field(default=2, ge=1, le=10)
    require_out_of_sample: bool = True


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(default="BTC/USDT", min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_./:-]+$")
    timeframe: Literal["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"] = "15m"
    market_type: Literal["crypto", "forex", "stock"] = "crypto"
    exchange: Literal["binance", "bybit", "innovestx", "mt5", "alpaca", "yfinance"] = "binance"
    limit: int = Field(default=750, ge=200, le=1000)
    initial_capital: float = Field(default=10_000.0, gt=0, le=1_000_000_000)
    risk_per_trade_pct: float = Field(default=1.0, gt=0, le=5)
    max_leverage: float = Field(default=3.0, gt=0, le=100)
    warmup_bars: int = Field(default=100, ge=60, le=500)
    oos_fraction: float = Field(default=0.70, ge=0.50, le=0.95)
    stride_bars: int = Field(default=3, ge=1, le=100)
    max_trades: int = Field(default=1000, ge=1, le=5000)
    assumptions: ExecutionAssumptionsRequest = Field(default_factory=ExecutionAssumptionsRequest)
    release_criteria: ReleaseCriteriaRequest = Field(default_factory=ReleaseCriteriaRequest)


def _assumptions(req: ExecutionAssumptionsRequest) -> ExecutionAssumptions:
    return ExecutionAssumptions(**req.model_dump())


def _criteria(req: ReleaseCriteriaRequest) -> ReleaseCriteria:
    return ReleaseCriteria(**req.model_dump())


def _run_summary(run: BacktestRun) -> dict:
    return {
        "id": str(run.id),
        "created_at": run.created_at.isoformat(),
        "run_type": run.run_type,
        "status": run.status,
        "symbol": run.symbol,
        "timeframe": run.timeframe,
        "strategy_version": run.strategy_version,
        "evaluation_mode": run.evaluation_mode,
        "data_start": run.data_start.isoformat() if run.data_start else None,
        "data_end": run.data_end.isoformat() if run.data_end else None,
        "metrics": run.metrics,
        "result_hash": run.result_hash,
    }


@router.get("/ledger/status")
async def get_postgres_ledger_status(
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_db),
):
    from app.services.ledger_migration import MIGRATION_NAME, ledger_mirror

    try:
        trade_count = int((await session.scalar(select(func.count(TradeLedgerRecord.id)))) or 0)
        order_count = int((await session.scalar(select(func.count(OrderLedgerRecord.id)))) or 0)
        fill_count = int((await session.scalar(select(func.count(FillLedgerRecord.id)))) or 0)
        checkpoint = await session.get(JsonMigrationCheckpoint, MIGRATION_NAME)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="PostgreSQL ledger is unavailable") from exc
    return {
        "status": "ready" if ledger_mirror.ready else "degraded",
        "runtime_mirror": {
            "running": ledger_mirror.running,
            "ready": ledger_mirror.ready,
            "last_error": ledger_mirror.last_error,
            "last_stats": ledger_mirror.last_stats,
        },
        "records": {"trades": trade_count, "orders": order_count, "fills": fill_count},
        "migration": {
            "name": checkpoint.name,
            "completed_at": checkpoint.completed_at.isoformat(),
            "source_hash": checkpoint.source_hash,
            "stats": checkpoint.stats,
        } if checkpoint else None,
        "json_role": "compatibility_backup_and_migration_source",
        "postgres_role": "normalized_phase3_ledger_mirror",
    }


async def _store_gate(
    session: AsyncSession,
    run: BacktestRun,
    criteria: ReleaseCriteria,
) -> tuple[ReleaseGateEvaluation, dict]:
    result = evaluate_release_gate(run.metrics, criteria)
    gate_payload = {"criteria": criteria.to_dict(), **result}
    gate = ReleaseGateEvaluation(
        backtest_run_id=run.id,
        passed=result["passed"],
        human_approval_required=True,
        production_eligible=False,
        criteria=criteria.to_dict(),
        checks=result["checks"],
        failure_reasons=result["failure_reasons"],
        result_hash=fingerprint(gate_payload),
    )
    session.add(gate)
    await session.flush()
    return gate, {"id": str(gate.id), **result, "criteria": criteria.to_dict(), "result_hash": gate.result_hash}


@router.post("/runs")
async def create_backtest_run(
    req: BacktestRequest,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_db),
):
    if req.warmup_bars >= req.limit:
        raise HTTPException(status_code=422, detail="warmup_bars must be below limit")
    frame = await _market.get_ohlcv(
        req.symbol,
        req.timeframe,
        req.market_type,
        req.exchange,
        limit=req.limit,
    )
    if frame.empty or len(frame) <= req.warmup_bars:
        raise HTTPException(status_code=502, detail="Insufficient market data for backtest")
    config = current_decision_config(_strategy)
    assumptions = _assumptions(req.assumptions)
    try:
        result = await asyncio.to_thread(
            run_walk_forward_backtest,
            market_data=frame,
            symbol=req.symbol,
            timeframe=req.timeframe,
            config=config,
            assumptions=assumptions,
            initial_capital=req.initial_capital,
            risk_per_trade_pct=req.risk_per_trade_pct,
            max_leverage=req.max_leverage,
            warmup_bars=req.warmup_bars,
            oos_fraction=req.oos_fraction,
            stride_bars=req.stride_bars,
            max_trades=req.max_trades,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    run = BacktestRun(
        run_type="oos_backtest",
        status=result["status"],
        symbol=req.symbol,
        timeframe=req.timeframe,
        strategy_version=str(config.get("version", "unknown"))[:30],
        evaluation_mode=result["evaluation_mode"],
        data_start=frame.index[0].to_pydatetime(),
        data_end=frame.index[-1].to_pydatetime(),
        config=config,
        assumptions={
            **assumptions.to_dict(),
            "initial_capital": req.initial_capital,
            "risk_per_trade_pct": req.risk_per_trade_pct,
            "max_leverage": req.max_leverage,
            "warmup_bars": req.warmup_bars,
            "oos_fraction": req.oos_fraction,
            "stride_bars": req.stride_bars,
        },
        metrics=result["metrics"],
        result=result,
        result_hash=fingerprint(result),
    )
    session.add(run)
    await session.flush()
    gate, gate_result = await _store_gate(session, run, _criteria(req.release_criteria))
    return {
        "run": _run_summary(run),
        "release_gate": gate_result,
        "result": result,
    }


@router.get("/runs")
async def list_backtest_runs(
    run_type: Literal["batch_replay", "oos_backtest"] | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_db),
):
    query = select(BacktestRun)
    if run_type:
        query = query.where(BacktestRun.run_type == run_type)
    query = query.order_by(BacktestRun.created_at.desc()).limit(limit)
    try:
        runs = list((await session.scalars(query)).all())
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Backtest store is unavailable") from exc
    return {"total": len(runs), "runs": [_run_summary(run) for run in runs]}


@router.get("/runs/{run_id}")
async def get_backtest_run(
    run_id: uuid.UUID,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_db),
):
    run = await session.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    gate_query = (
        select(ReleaseGateEvaluation)
        .where(ReleaseGateEvaluation.backtest_run_id == run_id)
        .order_by(ReleaseGateEvaluation.created_at.desc())
    )
    gate = await session.scalar(gate_query)
    return {
        "run": {**_run_summary(run), "config": run.config, "assumptions": run.assumptions},
        "release_gate": {
            "id": str(gate.id),
            "passed": gate.passed,
            "human_approval_required": gate.human_approval_required,
            "production_eligible": gate.production_eligible,
            "criteria": gate.criteria,
            "checks": gate.checks,
            "failure_reasons": gate.failure_reasons,
            "result_hash": gate.result_hash,
        } if gate else None,
        "result": run.result,
    }


@router.post("/runs/{run_id}/release-gate")
async def evaluate_backtest_release_gate(
    run_id: uuid.UUID,
    req: ReleaseCriteriaRequest,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_db),
):
    run = await session.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    if run.run_type != "oos_backtest":
        raise HTTPException(status_code=409, detail="Release gates require an OOS backtest run")
    _, result = await _store_gate(session, run, _criteria(req))
    return {"run_id": str(run.id), "release_gate": result}
