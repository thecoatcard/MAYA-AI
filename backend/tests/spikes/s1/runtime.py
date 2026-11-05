"""Linux-only benchmark worker functions and shared synthetic LangGraph workload."""

from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from typing import Any, TypedDict

import asyncpg  # type: ignore[import-untyped]
import redis
from celery import Celery  # type: ignore[import-untyped]
from celery.signals import worker_ready  # type: ignore[import-untyped]
from langgraph.graph import END, START, StateGraph

DATABASE_URL = os.environ.get("S1_DATABASE_URL", "postgresql://postgres@postgres:5432/s1_benchmark")
REDIS_URL = os.environ.get("S1_REDIS_URL", "redis://redis:6379/0")
RUN_ID = os.environ.get("S1_RUN_ID", "local")
QUEUE_NAME = os.environ.get("S1_QUEUE_NAME", f"s1:{RUN_ID}")
WORKER_READY_KEY = f"s1:ready:{RUN_ID}:{os.environ.get('S1_BACKEND', 'unknown')}"
SCHEMA = "s1_bench"


class WorkState(TypedDict, total=False):
    task_id: str
    run_id: str
    step_count: int
    sleep_ms: int
    record_effect: bool
    pool: asyncpg.Pool
    completed_steps: int


def _quote_schema() -> str:
    """Return the fixed schema identifier, not a user-provided SQL fragment."""
    return f'"{SCHEMA}"'


async def initialize_schema() -> None:
    """Create only the known disposable benchmark schema and tables."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_schema()}")
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_quote_schema()}.jobs (
                task_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                backend TEXT NOT NULL,
                scenario TEXT NOT NULL,
                queued_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                status TEXT NOT NULL DEFAULT 'queued',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                requested_steps INTEGER NOT NULL,
                sleep_ms INTEGER NOT NULL,
                event_loop_lag_ms DOUBLE PRECISION,
                error_type TEXT
            )
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_quote_schema()}.attempts (
                task_id TEXT NOT NULL REFERENCES {_quote_schema()}.jobs(task_id),
                attempt_number INTEGER NOT NULL,
                started_at TIMESTAMPTZ NOT NULL,
                completed_at TIMESTAMPTZ,
                status TEXT NOT NULL,
                event_loop_lag_ms DOUBLE PRECISION,
                error_type TEXT,
                PRIMARY KEY (task_id, attempt_number)
            )
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_quote_schema()}.effects (
                task_id TEXT PRIMARY KEY REFERENCES {_quote_schema()}.jobs(task_id),
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                observed_attempts INTEGER NOT NULL DEFAULT 1
            )
            """
        )
    finally:
        await conn.close()


async def drop_schema() -> None:
    """Drop only the named synthetic schema; never connect to another database."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(f"DROP SCHEMA IF EXISTS {_quote_schema()} CASCADE")
    finally:
        await conn.close()


async def _write_start(pool: asyncpg.Pool, task_id: str, run_id: str, backend: str) -> int:
    async with pool.acquire() as conn:
        async with conn.transaction():
            attempt_number = await conn.fetchval(
                f"""
                UPDATE {_quote_schema()}.jobs
                SET status = 'running',
                    attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, clock_timestamp())
                WHERE task_id = $1 AND run_id = $2 AND backend = $3
                RETURNING attempt_count
                """,
                task_id,
                run_id,
                backend,
            )
            if attempt_number is None:
                raise ValueError("benchmark task does not exist in the synthetic schema")
            await conn.execute(
                f"""
                INSERT INTO {_quote_schema()}.attempts (
                    task_id, attempt_number, started_at, status
                ) VALUES ($1, $2, clock_timestamp(), 'running')
                ON CONFLICT (task_id, attempt_number) DO NOTHING
                """,
                task_id,
                attempt_number,
            )
            return int(attempt_number)


async def _record_synthetic_effect(pool: asyncpg.Pool, task_id: str) -> None:
    """Use one idempotent synthetic effect row to observe redelivery safely."""
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO {_quote_schema()}.effects (task_id)
            VALUES ($1)
            ON CONFLICT (task_id) DO UPDATE
            SET observed_attempts = {_quote_schema()}.effects.observed_attempts + 1
            """,
            task_id,
        )


def _build_graph(step_count: int) -> Any:
    builder = StateGraph(WorkState)
    for step in range(step_count):

        async def run_step(state: WorkState, step_number: int = step) -> dict[str, int]:
            await asyncio.sleep(state["sleep_ms"] / 1000)
            if step_number == 0 and state.get("record_effect", False):
                await _record_synthetic_effect(state["pool"], state["task_id"])
            return {"completed_steps": step_number + 1}

        name = f"synthetic_step_{step}"
        builder.add_node(name, run_step)
        if step == 0:
            builder.add_edge(START, name)
        else:
            builder.add_edge(f"synthetic_step_{step - 1}", name)
    builder.add_edge(f"synthetic_step_{step_count - 1}", END)
    return builder.compile()


@lru_cache(maxsize=8)
def _compiled_graph(step_count: int) -> Any:
    return _build_graph(step_count)


async def _measure_event_loop_lag(stop: asyncio.Event) -> float:
    interval = 0.005
    loop = asyncio.get_running_loop()
    expected = loop.time() + interval
    maximum_lag = 0.0
    while not stop.is_set():
        await asyncio.sleep(interval)
        now = loop.time()
        maximum_lag = max(maximum_lag, max(0.0, now - expected))
        expected = now + interval
    return maximum_lag * 1000


async def execute_graph(
    pool: asyncpg.Pool,
    task_id: str,
    run_id: str,
    backend: str,
    step_count: int,
    sleep_ms: int,
    record_effect: bool,
) -> None:
    attempt_number = await _write_start(pool, task_id, run_id, backend)
    stop_monitor = asyncio.Event()
    monitor = asyncio.create_task(_measure_event_loop_lag(stop_monitor))
    try:
        graph = _compiled_graph(step_count)
        await graph.ainvoke(
            {
                "task_id": task_id,
                "run_id": run_id,
                "step_count": step_count,
                "sleep_ms": sleep_ms,
                "record_effect": record_effect,
                "pool": pool,
            }
        )
    except asyncio.CancelledError:
        stop_monitor.set()
        lag_ms = await monitor
        await _write_finish(pool, task_id, attempt_number, "aborted", lag_ms, None)
        raise
    except Exception as exc:
        stop_monitor.set()
        await monitor
        await _write_finish(pool, task_id, attempt_number, "failed", None, type(exc).__name__)
        raise
    stop_monitor.set()
    lag_ms = await monitor
    await _write_finish(pool, task_id, attempt_number, "completed", lag_ms, None)


async def _write_finish(
    pool: asyncpg.Pool,
    task_id: str,
    attempt_number: int,
    status: str,
    event_loop_lag_ms: float | None,
    error_type: str | None,
) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                f"""
                UPDATE {_quote_schema()}.attempts
                SET completed_at = clock_timestamp(),
                    status = $3,
                    event_loop_lag_ms = $4,
                    error_type = $5
                WHERE task_id = $1 AND attempt_number = $2
                """,
                task_id,
                attempt_number,
                status,
                event_loop_lag_ms,
                error_type,
            )
            await conn.execute(
                f"""
                UPDATE {_quote_schema()}.jobs
                SET status = $2,
                    completed_at = CASE WHEN $2 = 'completed' THEN clock_timestamp()
                                        ELSE completed_at END,
                    event_loop_lag_ms = $3,
                    error_type = $4
                WHERE task_id = $1
                """,
                task_id,
                status,
                event_loop_lag_ms,
                error_type,
            )


async def _execute_arq(
    ctx: dict[str, Any],
    task_id: str,
    run_id: str,
    step_count: int,
    sleep_ms: int,
    record_effect: bool = False,
) -> None:
    await execute_graph(
        ctx["db_pool"],
        task_id,
        run_id,
        "arq",
        step_count,
        sleep_ms,
        record_effect,
    )


celery_app = Celery("maya_s1", broker=REDIS_URL)
celery_app.conf.update(
    task_default_queue=QUEUE_NAME,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    broker_transport_options={"visibility_timeout": 3},
    task_time_limit=45,
    task_soft_time_limit=40,
    broker_connection_retry_on_startup=True,
)


def _execute_celery(
    self: Any,
    task_id: str,
    run_id: str,
    step_count: int,
    sleep_ms: int,
    record_effect: bool = False,
) -> None:
    del self

    async def execute() -> None:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=1)
        try:
            await execute_graph(
                pool,
                task_id,
                run_id,
                "celery",
                step_count,
                sleep_ms,
                record_effect,
            )
        finally:
            await pool.close()

    asyncio.run(execute())


def _mark_celery_worker_ready(**_: Any) -> None:
    client = redis.Redis.from_url(REDIS_URL)
    try:
        client.set(WORKER_READY_KEY, "ready", ex=300)
    finally:
        client.close()


celery_app.task(name="s1.execute", bind=True)(_execute_celery)
worker_ready.connect(_mark_celery_worker_ready)


async def _arq_startup(ctx: dict[str, Any]) -> None:
    ctx["db_pool"] = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=8)
    ready = redis.Redis.from_url(REDIS_URL)
    try:
        ready.set(WORKER_READY_KEY, "ready", ex=300)
    finally:
        ready.close()


async def _arq_shutdown(ctx: dict[str, Any]) -> None:
    pool = ctx.get("db_pool")
    if pool is not None:
        await pool.close()
