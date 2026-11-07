import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from app.api.v1.errors import install_error_handlers
from app.api.v1.router import api_router
from app.auth.factory import build_auth_service
from app.auth.service import AuthService
from app.core.config import Settings
from app.core.http import MaxBodySizeMiddleware
from app.core.logging import configure_logging, request_id_context, trace_id_context
from app.core.metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL, generate_latest
from app.core.resources import Resources
from app.core.telemetry import TraceContext, tracer
from app.gateway.registry import ModelRegistry
from app.gateway.startup import verify_gateway_at_startup

logger = logging.getLogger("maya")


def create_app(
    settings: Settings | None = None, *, auth_service: AuthService | None = None
) -> FastAPI:
    configuration = settings if settings is not None else Settings()
    configure_logging(configuration)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resources = Resources(configuration)
        app.state.resources = resources
        service: AuthService | None = None
        outbox_task: asyncio.Task[None] | None = None
        worker_task: asyncio.Task[None] | None = None
        reconciler_task: asyncio.Task[None] | None = None
        try:
            await resources.check()
            await resources.verify_runtime_role()
            # Tests replace Resources with mocks that carry no real registry; verify only when
            # the real gateway-bearing Resources is in use.
            if isinstance(getattr(resources, "registry", None), ModelRegistry):
                await verify_gateway_at_startup(
                    resources.registry, configuration, client=resources.model_gateway.client
                )
            service = auth_service or build_auth_service(configuration, resources)
            app.state.auth_service = service
            await service.warm_up()

            if hasattr(resources, "session_factory") and hasattr(resources, "event_publisher"):
                from app.db.tenant import tenant_session
                from app.tasks.outbox import OutboxDispatcher

                async def _poll_outbox() -> None:
                    dispatcher = OutboxDispatcher(resources.event_publisher)
                    while True:
                        try:
                            await asyncio.sleep(0.5)
                            workspaces = await resources.get_active_workspaces()
                            for ws_id in workspaces:
                                async with tenant_session(resources.engine, ws_id) as session:
                                    await dispatcher.dispatch_pending_events(
                                        session, workspace_id=ws_id
                                    )
                                    await session.commit()
                        except asyncio.CancelledError:
                            break
                        except Exception as poll_err:
                            logger.debug("outbox_poller_tick_error: %s", poll_err)
                            await asyncio.sleep(1.0)

                outbox_task = asyncio.create_task(_poll_outbox())

            if hasattr(resources, "session_factory") and hasattr(resources, "subtask_queue"):
                from datetime import UTC, datetime

                from sqlalchemy import select

                from app.agents.registry import create_default_fleet
                from app.db.models import PlanStatus, Subtask, SubtaskStatus, Task
                from app.db.tenant import tenant_session
                from app.services.projection_service import resolve_subtask_uuid
                from app.tasks.events import TaskEventType
                from app.tasks.executor import execute_subtask_with_agent
                from app.tasks.outbox import write_outbox_event
                from app.tasks.queue import SubtaskJob
                from app.tasks.worker import SubtaskWorker

                gw = getattr(resources, "model_gateway", None)
                fleet = create_default_fleet(
                    model_gateway=gw,
                    brave_api_key=getattr(configuration, "brave_api_key", None),
                )
                worker = SubtaskWorker(
                    queue=resources.subtask_queue,
                    permission_registry=None,
                    fleet_registry=fleet,
                )

                async def _agent_handler(
                    job: SubtaskJob, input_data: dict[str, Any]
                ) -> dict[str, Any]:
                    return await execute_subtask_with_agent(job, input_data, gateway=gw)

                # Continuous reconciler loop: monitors active tasks and unblocks ready subtasks
                async def _reconciler_loop() -> None:
                    while True:
                        try:
                            await asyncio.sleep(2.0)
                            workspaces = await resources.get_active_workspaces()
                            for ws_id in workspaces:
                                try:
                                    async with tenant_session(resources.engine, ws_id) as session:
                                        stmt = select(Task).where(
                                            Task.status.in_(["planning", "pending", "executing"]),
                                            Task.workspace_id == ws_id,
                                        )
                                        res = await session.execute(stmt)
                                        tasks = list(res.scalars().all())
                                        for t in tasks:
                                            st_stmt = select(Subtask).where(
                                                Subtask.task_id == t.id,
                                                Subtask.workspace_id == ws_id,
                                            )
                                            st_res = await session.execute(st_stmt)
                                            subtasks = list(st_res.scalars().all())
                                            if not subtasks:
                                                continue

                                            completed_ids = {
                                                str(s.id)
                                                for s in subtasks
                                                if s.status == SubtaskStatus.COMPLETED
                                            }
                                            all_done = len(completed_ids) == len(subtasks)
                                            if all_done:
                                                if t.status != "completed":
                                                    t.status = "completed"
                                                    t.plan_status = PlanStatus.COMPLETED
                                                    t.completed_at = datetime.now(UTC)
                                                    await write_outbox_event(
                                                        session=session,
                                                        workspace_id=ws_id,
                                                        task_id=t.id,
                                                        event_type=TaskEventType.TASK_COMPLETED,
                                                        payload={
                                                            "task_id": str(t.id),
                                                            "status": "completed",
                                                        },
                                                    )
                                                continue

                                            for s in subtasks:
                                                if s.status == SubtaskStatus.PENDING:
                                                    deps = s.dependencies or []
                                                    deps_satisfied = True
                                                    for d in deps:
                                                        d_str = str(d)
                                                        if d_str not in completed_ids:
                                                            resolved_d = str(
                                                                resolve_subtask_uuid(t.id, d_str)
                                                            )
                                                            if resolved_d not in completed_ids:
                                                                deps_satisfied = False
                                                                break
                                                    if deps_satisfied:
                                                        agent_str = getattr(
                                                            s.assigned_agent,
                                                            "value",
                                                            str(s.assigned_agent),
                                                        )
                                                        await resources.subtask_queue.enqueue(
                                                            SubtaskJob(
                                                                workspace_id=t.workspace_id,
                                                                user_id=t.user_id,
                                                                task_id=t.id,
                                                                subtask_id=s.id,
                                                                assigned_agent=agent_str,
                                                            )
                                                        )
                                        if hasattr(resources, "event_publisher"):
                                            from app.tasks.outbox import OutboxDispatcher

                                            dispatcher = OutboxDispatcher(resources.event_publisher)
                                            await dispatcher.dispatch_pending_events(
                                                session, workspace_id=ws_id
                                            )
                                        await session.commit()
                                except Exception as ws_err:
                                    logger.debug(
                                        "reconciler_workspace_error ws=%s: %s", ws_id, ws_err
                                    )
                        except asyncio.CancelledError:
                            break
                        except Exception as loop_err:
                            logger.error("reconciler_loop_error: %s", loop_err, exc_info=True)
                            await asyncio.sleep(2.0)

                # Subtask queue consumer loop
                async def _worker_consumer_loop() -> None:
                    while True:
                        try:
                            job = await resources.subtask_queue.dequeue(timeout_seconds=1.0)
                            if job is None:
                                continue
                            async with tenant_session(
                                resources.engine, job.workspace_id, job.user_id
                            ) as session:
                                await worker.execute_job(
                                    session=session,
                                    job=job,
                                    agent_handler=_agent_handler,
                                )
                                if hasattr(resources, "event_publisher"):
                                    from app.tasks.outbox import OutboxDispatcher

                                    dispatcher = OutboxDispatcher(resources.event_publisher)
                                    await dispatcher.dispatch_pending_events(
                                        session, workspace_id=job.workspace_id
                                    )
                                await session.commit()
                            resources.subtask_queue.task_done()
                        except asyncio.CancelledError:
                            break
                        except Exception as w_err:
                            logger.error("subtask_worker_loop_error: %s", w_err, exc_info=True)
                            await asyncio.sleep(1.0)

                worker_task = asyncio.create_task(_worker_consumer_loop())
                reconciler_task = asyncio.create_task(_reconciler_loop())

            logger.info("application_started")
            yield
        finally:
            if reconciler_task is not None:
                reconciler_task.cancel()
                try:
                    await reconciler_task
                except asyncio.CancelledError:
                    pass
            if worker_task is not None:
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass
            if outbox_task is not None:
                outbox_task.cancel()
                try:
                    await outbox_task
                except asyncio.CancelledError:
                    pass
            try:
                # Injected services remain owned by their caller (notably deterministic tests).
                if auth_service is None and service is not None:
                    await service.aclose()
            finally:
                await resources.close()
            logger.info("application_stopped")

    app = FastAPI(title="MAYA AI", version="0.1.0", debug=False, lifespan=lifespan)
    # Routes read the configuration the application was created with, not a second copy built
    # from the process environment (which would ignore an env file passed to ``python -m app``).
    app.state.settings = configuration

    @app.middleware("http")
    async def correlate_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = str(uuid4())
        correlation_id = request.headers.get("X-Correlation-ID") or request_id
        incoming_traceparent = request.headers.get("traceparent")
        parent_context = TraceContext.from_traceparent(incoming_traceparent)
        trace_context = TraceContext.generate(parent=parent_context)
        trace_id = trace_context.trace_id

        request_token = request_id_context.set(request_id)
        trace_token = trace_id_context.set(trace_id)
        start_time = time.time()
        try:
            span = tracer.start_span(
                f"http.{request.method.lower()}",
                parent=trace_context,
                attributes={
                    "http.method": request.method,
                    "http.target": request.url.path,
                    "http.client_ip": request.client.host if request.client else "unknown",
                },
            )
            response = await call_next(request)
            duration = time.time() - start_time
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["traceparent"] = trace_context.to_traceparent()
            span.set_attribute("http.status_code", response.status_code)
            span.finish()
            tracer.recorded_spans.append(span)

            endpoint_path = request.url.path
            status_str = str(response.status_code)
            HTTP_REQUESTS_TOTAL.inc(
                labels={"method": request.method, "endpoint": endpoint_path, "status": status_str}
            )
            HTTP_REQUEST_DURATION_SECONDS.observe(
                duration, labels={"method": request.method, "endpoint": endpoint_path}
            )

            logger.info("http_request_completed status=%d", response.status_code)
            return response
        finally:
            request_id_context.reset(request_token)
            trace_id_context.reset(trace_token)

    @app.get("/metrics", include_in_schema=False)
    async def get_metrics() -> Response:
        """Prometheus metrics scrape endpoint."""
        body = generate_latest()
        return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")

    app.add_middleware(MaxBodySizeMiddleware)
    install_error_handlers(app)
    app.include_router(api_router)
    return app
