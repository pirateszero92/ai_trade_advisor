from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from app.api import signals, trades, chart, settings_api, journal_api
from app.api import chat_history_api
from app.api.ws import router as ws_router
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    logger.info(f"Starting AI Trade Advisor [{cfg.app_env}]")
    # Init SQLite chat history DB
    await chat_history_api.init_db()
    logger.info("[Chat] SQLite chat history DB initialized")
    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    await monitor.start()
    yield
    monitor.stop()
    try:
        from app.engines.market_data import close_shared_http_client
        await close_shared_http_client()
    except Exception:
        pass
    logger.info("Shutting down cleanly.")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Trade Advisor API",
        version="1.0.1",
        description="SMC-based AI trading advisor",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(signals.router, prefix="/api/v1/signals", tags=["signals"])
    app.include_router(trades.router, prefix="/api/v1/trades", tags=["trades"])
    app.include_router(chart.router, prefix="/api/v1/chart", tags=["chart"])
    app.include_router(settings_api.router, prefix="/api/v1/settings", tags=["settings"])
    app.include_router(journal_api.router, prefix="/api/v1/journal", tags=["journal"])
    app.include_router(chat_history_api.router, prefix="/api/v1/chat", tags=["chat-history"])
    app.include_router(ws_router, prefix="/ws", tags=["websocket"])

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "1.0.1"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    cfg = get_settings()
    uvicorn.run("app.main:app", host=cfg.api_host, port=cfg.api_port, reload=True)
