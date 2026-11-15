import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents import capabilities, model_policy
from app.agents.registry import create_default_fleet
from app.api.v1.errors import install_error_handlers
from app.api.v1.router import api_router
from app.api.v1.workspaces import router as workspaces_router
from app.auth.factory import build_auth_service
from app.auth.service import AuthService
from app.automation.browser_pdf import shutdown_browser_renderer, start_browser_warm_up
from app.connections.crypto import ConnectionCipher
from app.connections.ownership import ownership_from_settings
from app.connections.service import ConnectionService, EnvDefaults
from app.core.asyncio_noise import install_connection_noise_handler
from app.core.config import Settings
from app.core.http import CHAT_MAX_BODY_BYTES, MaxBodySizeMiddleware
from app.core.logging import configure_logging, request_id_context, trace_id_context
from app.core.metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    generate_latest,
    route_label,
)
from app.core.resources import Resources
from app.core.static_web import mount_static_web
from app.core.telemetry import TraceContext, tracer
from app.db.repositories.user_settings import UserSettingsStore
from app.decisions import runtime as decision_runtime
from app.decisions.factory import build_decision_service
from app.decisions.required_details_check import run_startup_check as run_decisions_startup_check
from app.decisions.step_review import set_mode as set_step_review_mode
from app.files import services as file_services
from app.gateway.key_pool import KeyPool
from app.gateway.registry import ModelRegistry
from app.gateway.startup import verify_gateway_at_startup
from app.mail.config import mailbox_from_settings
from app.mcp.client_manager import MCPClientManager
from app.mcp.connectors.google_search import GoogleSearchConnector
from app.mcp.gateway import McpGateway, builtin_spec
from app.mcp.notion_auto_approve import NotionAutoApprover
from app.mcp.permissions import ToolPermissionRegistry
from app.mcp_client import service as mcp_client_runtime
from app.mcp_client.factory import build_mcp_client
from app.research.limits import ResearchLimits
from app.research.runtime import DeepResearchRuntime
from app.runtime_tools.factory import build_runtime_tools
from app.schedules import runtime as schedule_runtime
from app.schedules.factory import ScheduleServices
from app.schedules.factory import attach_runner as attach_schedule_runner
from app.schedules.factory import build_services as build_schedule_services
from app.search.factory import build_web_search
from app.services import task_result
from app.services.task_submission import TaskSubmissionService
from app.tasks.dispatch import FleetSupport
from app.tasks.executor import execute_subtask_with_agent
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.runtime import TaskRuntime
from app.tasks.worker import SubtaskWorker

logger = logging.getLogger("maya")


def create_app(
    settings: Settings | None = None, *, auth_service: AuthService | None = None
) -> FastAPI:
    configuration = settings if settings is not None else Settings()
    configure_logging(configuration)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        install_connection_noise_handler()
        resources = Resources(configuration)
        app.state.resources = resources
        policy = model_policy.configure(configuration)
        capabilities.configure(configuration)
        files_engine = getattr(resources, "engine", None)
        connections: ConnectionService | None = None
        capabilities.set_user_connections(None)  # never a previous app's (closed) engine
        capabilities.set_user_mailbox(None)
        capabilities.set_user_calendar(None)
        if isinstance(files_engine, AsyncEngine):
            # Per-user GitHub / Notion credentials (Settings > Connections); the environment
            # values stay the single-user default. One service for the API, tools and agents.
            key = configuration.connections_encryption_key
            connections = ConnectionService(
                UserSettingsStore(files_engine),
                ConnectionCipher.from_setting(key.get_secret_value() if key else None),
                EnvDefaults.from_settings(configuration),
                deployment_owner=ownership_from_settings(configuration, files_engine),
            )
            app.state.connection_service = connections
            _connections = connections

            async def _user_connections(workspace_id: UUID, user_id: UUID) -> tuple[bool, bool]:
                return (
                    await _connections.is_connected(workspace_id, user_id, "github"),
                    await _connections.is_connected(workspace_id, user_id, "notion"),
                )

            capabilities.set_user_connections(_user_connections)

            async def _user_mailbox(workspace_id: UUID, user_id: UUID) -> bool:
                return await _connections.resolve_mailbox(workspace_id, user_id) is not None

            capabilities.set_user_mailbox(_user_mailbox)

            async def _user_calendar(workspace_id: UUID, user_id: UUID) -> bool:
                # The server's Google Calendar belongs to the deployment owner only.
                return await _connections.is_deployment_owner(workspace_id, user_id)

            capabilities.set_user_calendar(_user_calendar)
        # Scheduled reports (ADR-0033): the service and MAYA's own mailer exist whenever there is a
        # database; the runner loop starts below, after the task runtime.
        schedule_services: ScheduleServices | None = None
        app.state.schedule_services = None
        if isinstance(files_engine, AsyncEngine):
            schedule_services = build_schedule_services(
                configuration, files_engine, getattr(resources, "redis", None)
            )
            app.state.schedule_services = schedule_services
        # The kill switch also hides the chat and voice schedule tools (runtime stays empty).
        schedule_runtime.set_current(schedule_services if configuration.schedules_enabled else None)
        # MCP client (ADR-0028): None unless MAYA_MCP_CLIENT_ENABLED; nothing is started here.
        mcp_client = build_mcp_client(configuration, connections)
        app.state.mcp_client = mcp_client
        mcp_client_runtime.set_current(mcp_client)
        app.state.file_services = file_services.build_file_services(
            configuration, files_engine if isinstance(files_engine, AsyncEngine) else None
        )
        file_services.configure(app.state.file_services)
        logger.info(
            "agent_model_policy alias=%s fallback=%s", policy.alias, policy.fallback_alias or "none"
        )
        key_pool = getattr(resources, "key_pool", None)
        if isinstance(key_pool, KeyPool):
            key_pool.log_assignments()
        # Decision model (ADR-0029): advisory and fail-open; None without a key or when off.
        decision_service = build_decision_service(configuration, key_pool)
        app.state.decision_service = decision_service
        decision_runtime.set_current(decision_service)
        set_step_review_mode(configuration.step_review_mode)
        if decision_service is not None:
            # Non-blocking self-test: one tiny call, logged as decisions_startup_check.
            async def _decisions_startup_check() -> None:
                decision_service.startup_check = await run_decisions_startup_check(decision_service)

            app.state.decisions_startup_task = asyncio.create_task(_decisions_startup_check())
        service: AuthService | None = None
        runtime: TaskRuntime | None = None
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

            if isinstance(getattr(resources, "subtask_queue", None), SubtaskQueue) and isinstance(
                getattr(resources, "engine", None), AsyncEngine
            ):
                signing_key = configuration.jwt_signing_key.get_secret_value()
                if not signing_key:
                    raise RuntimeError("MAYA_JWT_SIGNING_KEY is required to run the task worker")
                gateway = getattr(resources, "model_gateway", None)

                async def _agent_handler(
                    job: SubtaskJob, input_data: dict[str, Any]
                ) -> dict[str, Any]:
                    return await execute_subtask_with_agent(job, input_data, gateway=gateway)

                def _secret(value: Any) -> str | None:
                    return value.get_secret_value() if value is not None else None

                # One permission registry for the gate, the MCP client and every agent.
                registry = ToolPermissionRegistry()
                tool_gateway = McpGateway(MCPClientManager(permission_registry=registry))
                # Grounded search goes through the model gateway (key pool, failover, retries,
                # metrics, cost tracking); the connector holds no client of its own.
                has_search_backend = getattr(gateway, "client", None) is not None
                tool_gateway.register_spec(
                    builtin_spec(
                        # With a connection service every credential is resolved per caller (the
                        # server-level values only for the deployment owner); never pass them here.
                        github_token=(None if connections else _secret(configuration.github_token)),
                        connections=connections,
                        mailbox=None if connections else mailbox_from_settings(configuration),
                        artifacts_dir=configuration.artifacts_dir,
                        runtime_tools=build_runtime_tools(
                            configuration, storage_root=configuration.artifacts_dir
                        ),
                        google_search=(
                            GoogleSearchConnector(gateway=gateway) if has_search_backend else None
                        ),
                    )
                )
                if mcp_client is not None:
                    from app.mcp_client.gateway_spec import mcp_client_spec

                    tool_gateway.register_spec(mcp_client_spec(mcp_client))
                app.state.mcp_gateway = tool_gateway
                web_search_service, native_research = build_web_search(configuration, gateway)
                app.state.web_search_service = web_search_service
                fleet = create_default_fleet(
                    client_manager=tool_gateway.manager,
                    permission_registry=registry,
                    artifacts_base_dir=configuration.artifacts_dir,
                    # With a connection service these are resolved per caller (owner only for
                    # the server-level values); the calendar stays global but is gated by
                    # ``PMAgent._calendar_for``.
                    github_token=None if connections else _secret(configuration.github_token),
                    notion_token=None if connections else _secret(configuration.notion_token),
                    notion_database_id=configuration.notion_database_id,
                    connections=connections,
                    google_access_token=_secret(configuration.google_access_token),
                    mailbox=None if connections else mailbox_from_settings(configuration),
                    model_gateway=gateway,
                    native_search=native_research,
                    research_backend=configuration.research_backend,
                    deep_research=DeepResearchRuntime(
                        engine=resources.engine,
                        gateway=gateway,
                        web_search=web_search_service,
                        limits=ResearchLimits.from_settings(configuration),
                        enabled=configuration.deep_research_enabled,
                    ),
                )
                app.state.agent_fleet = fleet

                from app.tools.registry import get_global_registry
                from app.tools.startup import register_global_tools

                register_global_tools(
                    get_global_registry(),
                    engine=resources.engine,
                    tool_gateway=tool_gateway,
                    configuration=configuration,
                    gateway=gateway,
                )
                app.state.tool_registry = get_global_registry()

                runtime = TaskRuntime(
                    engine=resources.engine,
                    queue=resources.subtask_queue,
                    publisher=resources.event_publisher,
                    worker=SubtaskWorker(
                        queue=resources.subtask_queue,
                        permission_registry=registry,
                        fleet_registry=fleet,
                        jwt_signing_key=signing_key,
                        auto_approver=NotionAutoApprover(
                            UserSettingsStore(resources.engine), signing_key
                        ),
                        tool_invoker=tool_gateway,
                        fleet_support=FleetSupport(
                            gateway=gateway,
                            artifacts_dir=configuration.artifacts_dir,
                            allow_coding=False,
                        ),
                    ),
                    agent_handler=_agent_handler,
                    concurrency=configuration.task_worker_concurrency,
                )
                app.state.task_runtime = runtime
                # "What the task got" chat message + spoken summary (grounded, template fallback).
                task_result.set_current(task_result.TaskResultService(resources.engine, gateway))
                await runtime.start()
                if schedule_services is not None and configuration.schedules_enabled:
                    # The first tick is the catch-up for runs missed while MAYA was closed.
                    schedule_runner = attach_schedule_runner(
                        schedule_services,
                        configuration,
                        resources.engine,
                        getattr(resources, "redis", None),
                        TaskSubmissionService(
                            engine=resources.engine, gateway=gateway, queue=resources.subtask_queue
                        ),
                    )
                    schedule_runner.start()

            if configuration.browser_pdf_warm_up:
                # Background start of the PDF browser: returns at once, never fails startup.
                start_browser_warm_up()
            logger.info("application_started")
            yield
        finally:
            schedule_runtime.set_current(None)
            if schedule_services is not None:
                await schedule_services.aclose()
            if runtime is not None:
                await runtime.stop()
            # Close the shared headless browser (a no-op when no PDF was rendered).
            shutdown_browser_renderer()
            file_services.reset()
            decision_runtime.set_current(None)
            task_result.set_current(None)
            if decision_service is not None:
                await decision_service.aclose()
            # The resolver holds this app's engine: a later app (tests) must not inherit it.
            capabilities.set_user_connections(None)
            capabilities.set_user_mailbox(None)
            if mcp_client is not None:
                await mcp_client.shutdown()
            mcp_client_runtime.set_current(None)
            tool_gateway_ref = getattr(app.state, "mcp_gateway", None)
            if tool_gateway_ref is not None:
                await tool_gateway_ref.close()
            try:
                # Injected services remain owned by their caller (notably deterministic tests).
                if auth_service is None and service is not None:
                    await service.aclose()
            finally:
                await resources.close()
            logger.info("application_stopped")

    docs_urls = configuration.fastapi_docs_kwargs()
    app = FastAPI(
        title="MAYA AI",
        version="0.1.0",
        debug=False,
        lifespan=lifespan,
        docs_url=docs_urls["docs_url"],
        redoc_url=docs_urls["redoc_url"],
        openapi_url=docs_urls["openapi_url"],
    )
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

            endpoint_path = route_label(request.scope)
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

    app.add_middleware(
        MaxBodySizeMiddleware,
        path_limits={
            "/api/v1/files/uploads": configuration.upload_total_max_bytes + 1024 * 1024,
            "/api/v1/chat/message": CHAT_MAX_BODY_BYTES,
        },
    )
    install_error_handlers(app)
    app.include_router(api_router)
    app.include_router(workspaces_router, prefix="/api/v1/workspaces", tags=["workspaces"])
    mount_static_web(app, configuration)
    return app
