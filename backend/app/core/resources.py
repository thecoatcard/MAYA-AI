import asyncio
import logging
from uuid import UUID

from asyncpg import PostgresError
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.redis import create_redis_client
from app.db.session import create_database_engine
from app.db.tenant import verify_runtime_role
from app.gateway.cost_tracker import BudgetTracker
from app.gateway.key_pool import KeyPool
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import ModelRegistry, build_default_registry
from app.tasks.events import TaskEventPublisher
from app.tasks.queue import SubtaskQueue

logger = logging.getLogger("maya")


class DependencyUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Required local service unavailable")


class Resources:
    def __init__(self, settings: Settings) -> None:
        self.engine = create_database_engine(settings)
        self.redis = create_redis_client(settings)
        self.timeout = settings.probe_timeout_seconds
        self.registry: ModelRegistry = build_default_registry(settings.gemini_model_overrides)
        self.budget_tracker = BudgetTracker(self.redis, settings)
        effective = getattr(settings, "effective_gemini_api_keys", [])
        self.key_pool = KeyPool(effective) if effective else None
        self.model_gateway = ModelGateway(
            registry=self.registry,
            settings=settings,
            budget_tracker=self.budget_tracker,
            key_pool=self.key_pool,
        )
        self.subtask_queue = SubtaskQueue(redis=self.redis)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.event_publisher = TaskEventPublisher(self.redis)
        self.active_workspaces: set[UUID] = set()

    async def register_workspace(self, workspace_id: UUID) -> None:
        """Register active workspace for background task processing and reconciliation."""
        if not workspace_id:
            return
        self.active_workspaces.add(workspace_id)
        if self.redis is not None:
            try:
                await self.redis.sadd("maya:active_workspaces", str(workspace_id))
            except Exception:
                pass

    async def get_active_workspaces(self) -> set[UUID]:
        """Fetch all known active workspaces from memory and Redis."""
        from uuid import UUID

        workspaces = set(self.active_workspaces)
        if self.redis is not None:
            try:
                cached = await self.redis.smembers("maya:active_workspaces")
                for item in cached:
                    val = item.decode("utf-8") if isinstance(item, bytes) else str(item)
                    workspaces.add(UUID(val))
            except Exception:
                pass
        self.active_workspaces.update(workspaces)
        return workspaces

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
            await self.model_gateway.aclose()
        finally:
            try:
                await self.redis.aclose()
            finally:
                await self.engine.dispose()
