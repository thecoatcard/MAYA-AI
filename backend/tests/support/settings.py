from typing import Any

from app.core.config import Settings

DATABASE_URL = "postgresql+asyncpg://maya_app:example-only@localhost:5432/maya"
REDIS_URL = "redis://localhost:6379/0"
JWT_KEY = "test-only-jwt-signing-key-0123456789abcdef"
OTP_KEY = "test-only-otp-hmac-key-0123456789abcdefgh"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": DATABASE_URL,
        "redis_url": REDIS_URL,
        "jwt_signing_key": JWT_KEY,
        "otp_hmac_key": OTP_KEY,
    }
    values.update(overrides)
    return Settings(**values)
