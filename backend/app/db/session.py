from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


def create_database_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
        pool_timeout=settings.probe_timeout_seconds,
        connect_args={"timeout": settings.probe_timeout_seconds},
        hide_parameters=True,
    )
