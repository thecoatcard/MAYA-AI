"""Benchmark one queue backend inside an isolated Linux Docker container."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import importlib.metadata
import json
import os
import random
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import redis.asyncio as redis_async

from .metrics import classify_cancellation_status, summarize
from .runtime import SCHEMA

DB_URL = os.environ.get("S1_DATABASE_URL", "postgresql://postgres@postgres:5432/s1_benchmark")
REDIS_URL = os.environ.get("S1_REDIS_URL", "redis://redis:6379/0")
RUN_ID = os.environ.get("S1_RUN_ID", "local")
BACKEND = os.environ.get("S1_BACKEND", "")
CONCURRENCY = int(os.environ.get("S1_CONCURRENCY", "2"))
SCHEMA_SQL = f'"{SCHEMA}"'


class WorkerProcess:
    """Owns one Linux worker process group and only signals its own PID."""

    def __init__(self, process: subprocess.Popen[bytes], backend: str, queue_name: str) -> None:
        self.process = process
        self.backend = backend
        self.queue_name = queue_name

    @classmethod
    async def start(cls, backend: str, queue_name: str) -> WorkerProcess:
        if backend not in {"celery", "arq"}:
            raise ValueError("backend must be celery or arq")
        if os.name != "posix":
            raise RuntimeError("benchmark workers must run in a Linux container")

        env = os.environ.copy()
        env["S1_BACKEND"] = backend
        env["S1_QUEUE_NAME"] = queue_name
        env["S1_CONCURRENCY"] = str(CONCURRENCY)
        if backend == "celery":
            command = [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "s1.runtime:celery_app",
                "worker",
                "--pool=prefork",
                f"--concurrency={CONCURRENCY}",
                "--loglevel=WARNING",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
                f"--queues={queue_name}",
            ]
        else:
            command = [sys.executable, "-m", "arq", "s1.arq_worker.WorkerSettings"]
        ready_key = f"s1:ready:{RUN_ID}:{backend}"
        client = redis_async.Redis.from_url(REDIS_URL)
        try:
            await client.delete(ready_key)
        finally:
            await client.aclose()
        process = await asyncio.to_thread(_spawn_worker_process, command, env)
        worker = cls(process, backend, queue_name)
        try:
            await worker.wait_ready(timeout_seconds=20)
        except Exception:
            await worker.stop()
            raise
        return worker

    async def wait_ready(self, timeout_seconds: float) -> None:
        key = f"s1:ready:{RUN_ID}:{self.backend}"
        client = redis_async.Redis.from_url(REDIS_URL)
        try:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(f"{self.backend} worker exited before ready")
                if await client.get(key) is not None:
                    return
                await asyncio.sleep(0.05)
            raise TimeoutError(f"{self.backend} worker readiness timed out")
        finally:
            await client.aclose()

    async def stop(self, force: bool = False) -> None:
        if self.process.poll() is not None:
            return
        kill_group = getattr(os, "killpg", None)
        if not callable(kill_group):
            raise RuntimeError("Linux process-group signaling is required")
        sig = getattr(signal, "SIGKILL", signal.SIGTERM) if force else signal.SIGTERM
        try:
            kill_group(self.process.pid, sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.to_thread(self.process.wait, timeout=4)
        except subprocess.TimeoutExpired:
            try:
                kill_group(self.process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except ProcessLookupError:
                pass
            await asyncio.to_thread(self.process.wait, timeout=4)


def _spawn_worker_process(command: list[str], env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


async def _connect_db() -> asyncpg.Pool:
    return await asyncpg.create_pool(DB_URL, min_size=1, max_size=8)


def _task_id(scenario: str, index: int) -> str:
    raw = f"{RUN_ID}:{BACKEND}:{scenario}:{index}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


async def _enqueue(
    pool: asyncpg.Pool,
    broker: Any,
    task_id: str,
    scenario: str,
    queue_name: str,
    step_count: int,
    sleep_ms: int,
    record_effect: bool = False,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO {SCHEMA_SQL}.jobs (
                task_id, run_id, backend, scenario, requested_steps, sleep_ms
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
            task_id,
            RUN_ID,
            BACKEND,
            scenario,
            step_count,
            sleep_ms,
        )
    if BACKEND == "celery":
        from .runtime import celery_app

        await asyncio.to_thread(
            celery_app.send_task,
            "s1.execute",
            args=[task_id, RUN_ID, step_count, sleep_ms, record_effect],
            task_id=task_id,
            queue=queue_name,
        )
    else:
        await broker.enqueue_job(
            "_execute_arq",
            task_id,
            RUN_ID,
            step_count,
            sleep_ms,
            record_effect,
            _job_id=task_id,
            _queue_name=queue_name,
            _expires=60,
        )


async def _event_loop_monitor(stop: asyncio.Event) -> float:
    interval = 0.005
    loop = asyncio.get_running_loop()
    expected = loop.time() + interval
    max_lag = 0.0
    while not stop.is_set():
        await asyncio.sleep(interval)
        now = loop.time()
        max_lag = max(max_lag, max(0.0, now - expected))
        expected = now + interval
    return max_lag * 1000


async def _wait_for(
    pool: asyncpg.Pool,
    sql: str,
    args: Sequence[Any],
    timeout_seconds: float,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    async with pool.acquire() as conn:
        while time.monotonic() < deadline:
            row = await conn.fetchrow(sql, *args)
            if row:
                return row
            await asyncio.sleep(0.025)
    raise TimeoutError("synthetic benchmark condition timed out")


async def _query_tasks(pool: asyncpg.Pool, scenario: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT task_id, status,
                   EXTRACT(EPOCH FROM (completed_at - queued_at)) * 1000 AS latency_ms,
                   event_loop_lag_ms, attempt_count, error_type
            FROM {SCHEMA_SQL}.jobs
            WHERE run_id = $1 AND backend = $2 AND scenario = $3
            ORDER BY task_id
            """,
            RUN_ID,
            BACKEND,
            scenario,
        )
    return [dict(row) for row in rows]


async def _run_throughput(
    pool: asyncpg.Pool, broker: Any, queue_name: str, jobs_count: int, seed: int
) -> dict[str, Any]:
    monitor_stop = asyncio.Event()
    monitor = asyncio.create_task(_event_loop_monitor(monitor_stop))
    random_source = random.Random(seed)
    durations = [random_source.randint(8, 15) for _ in range(jobs_count)]
    started = time.perf_counter()
    try:
        for index, sleep_ms in enumerate(durations):
            await _enqueue(
                pool,
                broker,
                _task_id("throughput", index),
                "throughput",
                queue_name,
                3,
                sleep_ms,
            )
        await _wait_for(
            pool,
            f"""
            SELECT COUNT(*) AS remaining
            FROM {SCHEMA_SQL}.jobs
            WHERE run_id = $1 AND backend = $2 AND scenario = 'throughput'
                  AND status <> 'completed'
            HAVING COUNT(*) = 0
            """,
            [RUN_ID, BACKEND],
            timeout_seconds=45,
        )
    finally:
        monitor_stop.set()
    event_loop_lag_ms = await monitor
    elapsed_ms = (time.perf_counter() - started) * 1000
    rows = await _query_tasks(pool, "throughput")
    latencies = [float(row["latency_ms"]) for row in rows if row["latency_ms"] is not None]
    task_lags = [
        float(row["event_loop_lag_ms"]) for row in rows if row["event_loop_lag_ms"] is not None
    ]
    completed = sum(row["status"] == "completed" for row in rows)
    return {
        "submitted": jobs_count,
        "completed": completed,
        "failed_or_missing": jobs_count - completed,
        "wall_time_ms": elapsed_ms,
        "throughput_tasks_per_second": completed / (elapsed_ms / 1000) if elapsed_ms > 0 else 0,
        "completion_latency_ms": {
            **summarize(latencies),
            "samples": latencies,
        },
        "worker_event_loop_lag_ms": {
            **summarize(task_lags),
            "samples": task_lags,
        },
        "producer_event_loop_max_lag_ms": event_loop_lag_ms,
        "task_statuses": rows,
    }


async def _run_cancellation(pool: asyncpg.Pool, broker: Any, queue_name: str) -> dict[str, Any]:
    task_id = _task_id("cancellation", 0)
    await _enqueue(pool, broker, task_id, "cancellation", queue_name, 3, 5000)
    await _wait_for(
        pool,
        f"""
        SELECT task_id FROM {SCHEMA_SQL}.jobs
        WHERE task_id = $1 AND status = 'running'
        """,
        [task_id],
        timeout_seconds=10,
    )
    sent_at = time.perf_counter()
    if BACKEND == "arq":
        job_type = importlib.import_module("arq.jobs").Job
        job = job_type(task_id, broker, _queue_name=queue_name)
        abort_acknowledged: bool | None = await job.abort(timeout=8, poll_delay=0.05)
    else:
        from .runtime import celery_app

        await asyncio.to_thread(
            celery_app.control.revoke,
            task_id,
            terminate=True,
            signal="SIGTERM",
        )
        # Celery revoke broadcasts without waiting for a worker reply by default.
        abort_acknowledged = None
    observed_status: str | None = None
    observed_attempts = 0
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT status, attempt_count FROM {SCHEMA_SQL}.jobs WHERE task_id = $1",
                task_id,
            )
        if row:
            observed_status = str(row["status"])
            observed_attempts = int(row["attempt_count"])
            if observed_status in {"aborted", "completed", "failed"}:
                break
        await asyncio.sleep(0.05)
    return {
        "native_control": "ARQ Job.abort" if BACKEND == "arq" else "Celery revoke(terminate=True)",
        "control_request_completed": True,
        "revoke_request_returned": BACKEND == "celery",
        "worker_abort_acknowledged": abort_acknowledged,
        "terminal_cancellation_observed": observed_status == "aborted",
        "cancellation_outcome": classify_cancellation_status(observed_status),
        "time_to_observed_terminal_ms": (time.perf_counter() - sent_at) * 1000
        if observed_status in {"aborted", "completed", "failed"}
        else None,
        "status_after_observation_window": observed_status,
        "attempts_after_observation_window": observed_attempts,
        "observation_window_seconds": 4,
    }


async def _run_worker_failure(pool: asyncpg.Pool, broker: Any, queue_name: str) -> dict[str, Any]:
    task_id = _task_id("worker_failure", 0)
    await _enqueue(
        pool,
        broker,
        task_id,
        "worker_failure",
        queue_name,
        3,
        3000,
        record_effect=True,
    )
    await _wait_for(
        pool,
        f"""
        SELECT e.task_id FROM {SCHEMA_SQL}.effects e
        JOIN {SCHEMA_SQL}.jobs j ON j.task_id = e.task_id
        WHERE e.task_id = $1 AND j.status = 'running'
        """,
        [task_id],
        timeout_seconds=15,
    )
    return {"task_id": task_id, "injected_after_synthetic_effect": True}


async def run_benchmark(jobs_count: int, seed: int) -> dict[str, Any]:
    if BACKEND not in {"celery", "arq"}:
        raise ValueError("S1_BACKEND must be celery or arq")
    if not 1 <= jobs_count <= 500:
        raise ValueError("jobs_count must be between 1 and 500")
    if not 1 <= CONCURRENCY <= 4:
        raise ValueError("concurrency must be between 1 and 4")
    pool = await _connect_db()
    broker = None
    worker: WorkerProcess | None = None
    if BACKEND == "arq":
        arq = importlib.import_module("arq")
        connections = importlib.import_module("arq.connections")
        broker = await arq.create_pool(connections.RedisSettings.from_dsn(REDIS_URL))
    result: dict[str, Any] = {
        "backend": BACKEND,
        "run_id": RUN_ID,
        "seed": seed,
        "concurrency": CONCURRENCY,
        "queue_configuration": (
            {
                "celery": {
                    "pool": "prefork",
                    "late_ack": True,
                    "reject_on_worker_lost": True,
                    "prefetch_multiplier": 1,
                    "redis_visibility_timeout_seconds": 3,
                    "task_time_limit_seconds": 45,
                }
            }
            if BACKEND == "celery"
            else {
                "arq": {
                    "max_jobs": CONCURRENCY,
                    "job_timeout_seconds": 12,
                    "poll_delay_seconds": float(os.environ.get("S1_ARQ_POLL_DELAY", "0.02")),
                    "expires_extra_ms": 1500,
                }
            }
        ),
        "python": sys.version.split()[0],
        "package_versions": {
            package: importlib.metadata.version(package)
            for package in (
                ("celery", "kombu", "redis", "asyncpg", "langgraph")
                if BACKEND == "celery"
                else ("arq", "redis", "asyncpg", "langgraph")
            )
        },
        "started_at_utc": datetime.now(UTC).isoformat(),
    }
    try:
        throughput_queue = f"s1:{RUN_ID}:{BACKEND}:throughput"
        worker = await WorkerProcess.start(BACKEND, throughput_queue)
        result["throughput"] = await _run_throughput(
            pool, broker, throughput_queue, jobs_count, seed
        )
        await worker.stop()
        worker = None

        cancel_queue = f"s1:{RUN_ID}:{BACKEND}:cancel"
        worker = await WorkerProcess.start(BACKEND, cancel_queue)
        result["cancellation"] = await _run_cancellation(pool, broker, cancel_queue)
        await worker.stop(force=True)
        worker = None

        failure_queue = f"s1:{RUN_ID}:{BACKEND}:failure"
        worker = await WorkerProcess.start(BACKEND, failure_queue)
        failure = await _run_worker_failure(pool, broker, failure_queue)
        await worker.stop(force=True)
        worker = None
        async with pool.acquire() as conn:
            worker_killed_at = await conn.fetchval("SELECT clock_timestamp()")
        failure["worker_killed_at_utc"] = worker_killed_at.isoformat()
        restart_ready_started = time.perf_counter()
        worker = await WorkerProcess.start(BACKEND, failure_queue)
        failure["worker_restart_ready_ms"] = (time.perf_counter() - restart_ready_started) * 1000
        failure["redelivered_and_completed"] = False
        try:
            second_attempt = await _wait_for(
                pool,
                f"""
                SELECT started_at FROM {SCHEMA_SQL}.attempts
                WHERE task_id = $1 AND attempt_number = 2
                """,
                [failure["task_id"]],
                timeout_seconds=35,
            )
            async with pool.acquire() as conn:
                failure["redelivery_latency_after_kill_ms"] = await conn.fetchval(
                    "SELECT EXTRACT(EPOCH FROM ($1::timestamptz - $2::timestamptz)) * 1000",
                    second_attempt["started_at"],
                    worker_killed_at,
                )
        except TimeoutError:
            failure["redelivery_latency_after_kill_ms"] = None
        else:
            try:
                completed = await _wait_for(
                    pool,
                    f"""
                    SELECT task_id FROM {SCHEMA_SQL}.jobs
                    WHERE task_id = $1 AND status = 'completed'
                    """,
                    [failure["task_id"]],
                    timeout_seconds=20,
                )
                failure["redelivered_and_completed"] = completed is not None
            except TimeoutError:
                failure["redelivered_and_completed"] = False
        await worker.stop(force=True)
        worker = None
        async with pool.acquire() as conn:
            job_row = await conn.fetchrow(
                f"SELECT attempt_count, status FROM {SCHEMA_SQL}.jobs WHERE task_id = $1",
                failure["task_id"],
            )
            effect_row = await conn.fetchrow(
                f"SELECT COUNT(*) AS rows, MAX(observed_attempts) AS observed_attempts "
                f"FROM {SCHEMA_SQL}.effects WHERE task_id = $1",
                failure["task_id"],
            )
        failure["attempt_count"] = int(job_row["attempt_count"]) if job_row else 0
        failure["final_status"] = str(job_row["status"]) if job_row else "missing"
        failure["synthetic_effect_rows"] = int(effect_row["rows"]) if effect_row else 0
        failure["effect_attempt_observations"] = (
            int(effect_row["observed_attempts"])
            if effect_row and effect_row["observed_attempts"] is not None
            else 0
        )
        result["controlled_worker_failure"] = failure
        result["finished_at_utc"] = datetime.now(UTC).isoformat()
        return result
    finally:
        if worker is not None:
            await worker.stop(force=True)
        if broker is not None:
            await broker.aclose()
        await pool.close()


async def init_schema() -> None:
    from .runtime import initialize_schema

    await initialize_schema()


async def drop_schema() -> None:
    from .runtime import drop_schema as drop

    await drop()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db")
    subparsers.add_parser("drop-db")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--jobs", type=int, default=80)
    run_parser.add_argument("--seed", type=int, default=20261105)
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    if args.command == "init-db":
        await init_schema()
        return 0
    if args.command == "drop-db":
        await drop_schema()
        return 0
    result = await run_benchmark(args.jobs, args.seed)
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_main_async(args))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                    "backend": BACKEND,
                    "run_id": RUN_ID,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
