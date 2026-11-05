import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from app.api.v1.health import router
from app.core.config import Settings
from app.core.logging import configure_logging, request_id_context, trace_id_context
from app.core.resources import Resources

logger = logging.getLogger("maya")


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings if settings is not None else Settings()
    configure_logging(configuration)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resources = Resources(configuration)
        app.state.resources = resources
        try:
            await resources.check()
            logger.info("application_started")
            yield
        finally:
            await resources.close()
            logger.info("application_stopped")

    app = FastAPI(title="MAYA AI", version="0.1.0", debug=False, lifespan=lifespan)

    @app.middleware("http")
    async def correlate_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = str(uuid4())
        request_token = request_id_context.set(request_id)
        trace_token = trace_id_context.set(uuid4().hex)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logger.info("http_request_completed status=%d", response.status_code)
            return response
        finally:
            request_id_context.reset(request_token)
            trace_id_context.reset(trace_token)

    app.include_router(router)
    return app
