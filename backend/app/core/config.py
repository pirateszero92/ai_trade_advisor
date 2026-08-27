from functools import lru_cache
import threading
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "production"] = "development"
    # Deliberately has no usable default. Production must supply this through
    # the environment or an external secret manager.
    app_secret_key: str = ""
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_allowed_origins: str = "http://localhost:3000,http://localhost:8080"
    allowed_llm_hosts: str = "localhost,127.0.0.1,::1,host.docker.internal"

    database_url: str = "postgresql+asyncpg://localhost/ai_trade_db"
    redis_url: str = "redis://localhost:6379/0"

    local_llm_endpoint: str = "http://localhost:1234/v1"
    local_llm_model: str = "llama-3.2-8b"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-3.5-sonnet"

    binance_api_key: str = ""
    binance_api_secret: str = ""
    bybit_api_key: str = ""
    bybit_api_secret: str = ""

    # Phase 4 public market-data stream. These endpoints never receive broker
    # credentials and cannot submit orders.
    market_stream_enabled: bool = True
    binance_market_ws_url: str = "wss://stream.binance.com:9443/stream"
    binance_market_rest_url: str = "https://data-api.binance.vision"
    binance_kline_intervals: str = "1m,15m,1h,4h,1d"
    market_stream_max_symbols: int = Field(default=100, ge=1, le=100)
    market_stream_max_recovery_trades: int = Field(default=5000, ge=1, le=10_000)
    market_data_stale_after_seconds: float = Field(default=10.0, gt=1.0, le=120.0)
    market_data_fallback_stale_after_seconds: float = Field(default=45.0, gt=5.0, le=600.0)

    # Phase 6 Paper-only execution model. These values never configure or
    # route a Live broker order.
    paper_oms_fee_bps: float = Field(default=10.0, ge=0.0, le=500.0)
    paper_oms_spread_bps: float = Field(default=5.0, ge=0.0, le=500.0)
    paper_oms_slippage_bps: float = Field(default=3.0, ge=0.0, le=500.0)
    paper_oms_max_volume_participation: float = Field(default=0.01, gt=0.0, le=1.0)
    paper_oms_fallback_partial_fill_ratio: float = Field(default=0.35, gt=0.0, le=1.0)
    paper_oms_auto_be_trigger_r: float = Field(default=1.0, ge=0.5, le=5.0)
    paper_oms_trailing_min_step_r: float = Field(default=0.05, ge=0.01, le=1.0)

    innovestx_api_key: str = ""
    innovestx_api_secret: str = ""
    innovestx_base_url: str = "https://api.innovestxonline.com"

    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_path: str = "C:/Program Files/MetaTrader 5/terminal64.exe"

    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"

    fcm_server_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    line_notify_token: str = ""

    trading_mode: Literal["paper", "live"] = "paper"
    default_risk_per_trade: float = 1.0
    max_daily_loss: float = 3.0
    max_open_positions: int = 5


_SETTINGS_LOCK = threading.RLock()


@lru_cache
def get_settings() -> Settings:
    return Settings()


def update_runtime_setting(key: str, value: object) -> None:
    """Thread-safe update of in-memory runtime settings."""
    with _SETTINGS_LOCK:
        cfg = get_settings()
        if hasattr(cfg, key):
            setattr(cfg, key, value)


def reload_settings() -> Settings:
    """Thread-safe reload of Settings from environment/env_file."""
    global get_settings
    with _SETTINGS_LOCK:
        get_settings.cache_clear()
        return get_settings()
