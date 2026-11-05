import asyncio
import logging

from asyncpg import PostgresError
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.redis import create_redis_client
from app.db.session import create_database_engine
from app.db.tenant import verify_runtime_role

logger = logging.getLogger("maya")


class DependencyUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Required local service unavailable")


class Resources:
    def __init__(self, settings: Settings) -> None:
        self.engine = create_database_engine(settings)
        self.redis = create_redis_client(settings)
        self.timeout = settings.probe_timeout_seconds

    async def check(self) -> None:
        try:
            async with asyncio.timeout(self.timeout):
                async with self.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                if not await self.redis.ping():
                    raise DependencyUnavailable()
        except (SQLAlchemyError, PostgresError, RedisError, OSError, TimeoutError):
            logger.error("dependency_probe_failed")
            raise DependencyUnavailable() from None
        except DependencyUnavailable:
            logger.error("dependency_probe_failed")
            raise

    async def verify_runtime_role(self) -> None:
        """Startup guard: the API must run as the restricted role (see ``app.db.tenant``)."""
        try:
            async with asyncio.timeout(self.timeout):
                await verify_runtime_role(self.engine)
        except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
            logger.error("dependency_probe_failed")
            raise DependencyUnavailable() from None

    async def close(self) -> None:
        try:
            await self.redis.aclose()
        finally:
            await self.engine.dispose()
