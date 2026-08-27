import hmac
import os
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader
from loguru import logger
from app.core.config import get_settings

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

_INSECURE_KEYS = {
    "changeme",
    "change_this_to_a_secure_random_key",
    "replace_with_a_unique_32_plus_character_secret",
    "ai_trade_sec_key_2026_x89a42f",
    "dev",
}


def is_securely_configured() -> bool:
    key = (get_settings().app_secret_key or "").strip()
    return len(key) >= 32 and key not in _INSECURE_KEYS


def is_valid_api_key(provided_key: str | None) -> bool:
    """Safe constant-time API key verification."""
    cfg = get_settings()
    if not provided_key:
        return False
    if not is_securely_configured():
        # Insecure default key detected
        return False
    given = provided_key.encode("utf-8")
    expected = (cfg.app_secret_key or "").encode("utf-8")
    return hmac.compare_digest(given, expected)


async def verify_api_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    cfg = get_settings()
    
    # Only bypass auth if explicitly opted-in via environment flag in development
    if cfg.app_env == "development" and os.getenv("ALLOW_INSECURE_DEV_AUTH") == "1":
        return "dev"

    if not is_securely_configured():
        logger.error("[Security] APP_SECRET_KEY is unconfigured or using insecure default!")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server security unconfigured: Please set a secure APP_SECRET_KEY in .env",
        )

    if not is_valid_api_key(api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return api_key
