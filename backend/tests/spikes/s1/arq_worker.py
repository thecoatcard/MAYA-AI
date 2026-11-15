"""ARQ-only worker settings kept out of the Celery/redis-py 8 candidate image."""

from __future__ import annotations

import os

from arq.connections import RedisSettings

from .runtime import _arq_shutdown, _arq_startup, _execute_arq

REDIS_URL = os.environ.get("S1_REDIS_URL", "redis://redis:6379/0")
RUN_ID = os.environ.get("S1_RUN_ID", "local")
QUEUE_NAME = os.environ.get("S1_QUEUE_NAME", f"s1:{RUN_ID}")


class WorkerSettings:
    functions = [_execute_arq]
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    queue_name = QUEUE_NAME
    max_jobs = int(os.environ.get("S1_CONCURRENCY", "2"))
    job_timeout = 12
    max_tries = 2
    retry_jobs = True
    allow_abort_jobs = True
    expires_extra_ms = 1500
    poll_delay = float(os.environ.get("S1_ARQ_POLL_DELAY", "0.02"))
    on_startup = _arq_startup
    on_shutdown = _arq_shutdown
