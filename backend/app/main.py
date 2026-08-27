from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from app.api import backtest_api, signals, trades, chart, settings_api, journal_api, briefing_api, evidence_api, live_api, market_data_api, paper_api
from app.api import chat_history_api
from app.api.ws import router as ws_router
from app.core.config import get_settings
from app.core.security import is_securely_configured
from app.engines.price_hub import price_hub


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    if cfg.app_env == "production" and not is_securely_configured():
        raise RuntimeError("APP_SECRET_KEY must be a unique secret of at least 32 characters")
    cfg = get_settings()
    logger.info(f"Starting AI Trade Advisor [{cfg.app_env}]")
    # Init SQLite chat history DB
    await chat_history_api.init_db()
    logger.info("[Chat] SQLite chat history DB initialized")

    # Phase 3: preserve JSON files as read-only migration sources while
    # maintaining a normalized PostgreSQL trade/order/fill mirror.
    from app.services.ledger_migration import ledger_mirror, migrate_json_ledgers
    try:
        migration_stats = await migrate_json_ledgers(
            trades.PAPER_TRADES_STORE_FILE,
            trades.LIVE_TRADES_STORE_FILE,
        )
        await ledger_mirror.start()
        logger.info("[Phase3] JSON ledger migration: {}", migration_stats)
    except Exception as exc:
        ledger_mirror.ready = False
        ledger_mirror.last_error = type(exc).__name__
        logger.error("[Phase3] PostgreSQL ledger migration failed: {}", exc)

    # Phase 6: PostgreSQL is authoritative for Paper execution. The service
    # imports the compatibility JSON once, recovers active state after a
    # restart, and then writes JSON as a projection only.
    from app.services.paper_oms import paper_oms
    try:
        recovery = await paper_oms.start(
            trades.PAPER_TRADES_STORE_FILE,
            trades.PAPER_CONFIG_FILE,
        )
        logger.info("[Phase6] Paper OMS recovery: {}", recovery)
    except Exception as exc:
        logger.error("[Phase6] Paper OMS unavailable: {}", exc)
    
    # Start In-Memory Price Hub streaming daemon
    try:
        await price_hub.start_stream()
        logger.info("[PriceHub] Central In-Memory Price Hub stream online")
    except Exception as e:
        logger.error(f"[STARTUP] PriceHub stream failed to start: {e}")

    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    try:
        await monitor.start()
    except Exception as e:
        logger.error(f"[STARTUP] MarketMonitor failed to start: {e}. API running without proactive background scanning.")
    
    yield
    
    await monitor.stop()
    await paper_oms.stop()
    await ledger_mirror.stop()
    await price_hub.stop_stream()
    try:
        from app.engines.market_data import close_shared_http_client
        await close_shared_http_client()
    except Exception:
        pass
    logger.info("Shutting down cleanly.")


def create_app() -> FastAPI:
    cfg = get_settings()
    allowed_origins = [
        origin.strip() for origin in cfg.cors_allowed_origins.split(",") if origin.strip()
    ]
    app = FastAPI(
        title="AI Trade Advisor API",
        version="1.0.2",
        description="SMC-based AI trading advisor with Full-Duplex WebSocket Push Hub",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(signals.router, prefix="/api/v1/signals", tags=["signals"])
    app.include_router(trades.router, prefix="/api/v1/trades", tags=["trades"])
    app.include_router(paper_api.router, prefix="/api/v1/paper", tags=["paper-trading"])
    app.include_router(live_api.router, prefix="/api/v1/live", tags=["live-gateway"])
    app.include_router(chart.router, prefix="/api/v1/chart", tags=["chart"])
    app.include_router(settings_api.router, prefix="/api/v1/settings", tags=["settings"])
    app.include_router(journal_api.router, prefix="/api/v1/journal", tags=["journal"])
    app.include_router(chat_history_api.router, prefix="/api/v1/chat", tags=["chat-history"])
    app.include_router(briefing_api.router, prefix="/api/v1/briefing", tags=["briefing"])
    app.include_router(evidence_api.router, prefix="/api/v1/evidence", tags=["evidence-replay"])
    app.include_router(backtest_api.router, prefix="/api/v1/backtests", tags=["backtests-release-gates"])
    app.include_router(market_data_api.router, prefix="/api/v1/market-data", tags=["real-time-market-data"])
    app.include_router(ws_router, prefix="/ws", tags=["websocket"])

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": app.version}

    @app.get("/ready")
    async def ready():
        from fastapi.responses import JSONResponse
        from app.services.event_trigger import MarketMonitor
        from app.engines.price_hub import price_hub
        from app.services.ledger_migration import ledger_mirror
        from app.services.paper_oms import paper_oms

        monitor = MarketMonitor.get_instance()
        market_data_health = price_hub.health_snapshot()
        ready_state = bool(
            monitor.running and price_hub.is_running and ledger_mirror.ready and paper_oms.ready
        )
        payload = {
            "status": "ok" if ready_state else "degraded",
            "version": app.version,
            "market_monitor": monitor.running,
            "price_hub": price_hub.is_running,
            "market_data_status": market_data_health["status"],
            "market_data_realtime_symbols": market_data_health["fresh_realtime_symbols"],
            "postgres_ledger": ledger_mirror.ready,
            "paper_oms": paper_oms.health_snapshot(),
        }
        return JSONResponse(payload, status_code=200 if ready_state else 503)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    cfg = get_settings()
    uvicorn.run(
        "app.main:app",
        host=cfg.api_host,
        port=cfg.api_port,
        reload=(cfg.app_env == "development"),
    )
