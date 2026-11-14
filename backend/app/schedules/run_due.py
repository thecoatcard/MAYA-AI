"""One-shot helper for the Windows "wake PC" mode: ``python -m app.schedules.run_due``.

The headless desktop run (``electron . --run-schedules``) starts the backend, whose scheduler
runner catches up on start and runs every due schedule (tasks need the backend's own worker). This
command then WAITS until no schedule is due or running any more (or a time cap passes), prints
one JSON line and exits: 0 = idle, 3 = cap reached with work left, 2 = nothing to wait for
(scheduler switched off). It starts no tasks itself and sends no mail itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.clock import Clock, SystemClock
from app.schedules.models import Doc
from app.schedules.store import ScheduleStore

DEFAULT_MAX_MINUTES = 25
POLL_SECONDS = 15


def outstanding(doc: Doc, now: datetime) -> dict[str, int]:
    """How many schedules are running, due now, or waiting for a retry."""
    running = sum(1 for s in doc.items if s.pending_run is not None)
    due = sum(
        1
        for s in doc.items
        if s.enabled
        and s.pending_run is None
        and s.next_run_at is not None
        and s.next_run_at <= now
    )
    retry = sum(1 for s in doc.items if s.enabled and s.retry_at is not None)
    return {"running": running, "due": due, "retry": retry}


async def wait_until_idle(
    store: ScheduleStore,
    clock: Clock,
    *,
    max_seconds: float,
    poll_seconds: float = POLL_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    waited = 0.0
    while True:
        now = clock.now()
        total = {"running": 0, "due": 0, "retry": 0}
        for workspace_id, user_id in await store.index.members():
            for key, value in outstanding(await store.load(workspace_id, user_id), now).items():
                total[key] += value
        if not any(total.values()):
            return {"idle": True, "waited_seconds": int(waited), **total}
        if waited >= max_seconds:
            return {"idle": False, "waited_seconds": int(waited), **total}
        await sleep(poll_seconds)
        waited += poll_seconds


async def _main(env_file: Path | None, max_minutes: int) -> int:
    from app.core.config import Settings
    from app.core.redis import create_redis_client
    from app.db.repositories.user_settings import UserSettingsStore
    from app.db.session import create_database_engine
    from app.schedules.store import UserIndex

    settings = Settings(_env_file=env_file)
    if not settings.schedules_enabled:
        print(json.dumps({"idle": True, "disabled": True}))
        return 2
    engine = create_database_engine(settings)
    redis = create_redis_client(settings)
    try:
        store = ScheduleStore(UserSettingsStore(engine), UserIndex(redis))
        result = await wait_until_idle(store, SystemClock(), max_seconds=max_minutes * 60)
    finally:
        await redis.aclose()
        await engine.dispose()
    print(json.dumps(result))
    return 0 if result["idle"] else 3


def main() -> None:
    parser = argparse.ArgumentParser(description="Wait for due scheduled reports to finish")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--max-minutes", type=int, default=DEFAULT_MAX_MINUTES)
    arguments = parser.parse_args()
    if arguments.env_file is not None and not arguments.env_file.is_file():
        parser.error("The requested environment file does not exist")
    sys.exit(asyncio.run(_main(arguments.env_file, max(1, arguments.max_minutes))))


if __name__ == "__main__":
    main()
