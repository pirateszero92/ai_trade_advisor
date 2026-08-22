from functools import lru_cache
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "production"] = "development"
    app_secret_key: str = "ai_trade_sec_key_2026_x89a42f"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    database_url: str = "postgresql+asyncpg://trader:trader_pass@localhost:5432/ai_trade_db"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
