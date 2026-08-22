import hmac
import os
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader
from loguru import logger
from app.core.config import get_settings

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def is_valid_api_key(provided_key: str | None) -> bool:
    """Safe constant-time API key verification."""
    cfg = get_settings()
    if not provided_key:
        return False
    if not cfg.app_secret_key or cfg.app_secret_key in {"changeme", "change_this_to_a_secure_random_key"}:
        # Insecure default key detected
        return False
    given = provided_key.encode("utf-8")
    expected = cfg.app_secret_key.encode("utf-8")
    if len(given) != len(expected):
        return False
    return hmac.compare_digest(given, expected)


async def verify_api_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    cfg = get_settings()
    
    # Only bypass auth if explicitly opted-in via environment flag
    if cfg.app_env == "development" and os.getenv("ALLOW_INSECURE_DEV_AUTH") == "1":
        return "dev"

    if not cfg.app_secret_key or cfg.app_secret_key in {"changeme", "change_this_to_a_secure_random_key"}:
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
