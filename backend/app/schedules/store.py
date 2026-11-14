"""Storage of the schedule document in the per-user settings JSON store (key ``schedules``).

One JSON text value holds every schedule, the run mode, verified mail recipients and drafts. All
writes are optimistic compare-and-set on the stored text, so two writers (the Settings page and
the runner) never overwrite each other: the loser re-reads and re-applies its change.

Runners cannot list users across tenants (row-level security), so every write registers the
``workspace:user`` pair in an index (Redis set, plus an in-process set). ``GET /schedules`` also
registers the caller, which repairs the index after a Redis flush.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Final, Protocol, TypeVar
from uuid import UUID

from pydantic import ValidationError

from app.schedules.models import Doc

logger = logging.getLogger("maya.schedules")

SETTINGS_KEY: Final = "schedules"
INDEX_KEY: Final = "maya:schedules:users"
MAX_DOC_BYTES: Final = 200_000
_ATTEMPTS: Final = 8

T = TypeVar("T")


class SettingsBackend(Protocol):
    async def get_text(self, workspace_id: UUID, user_id: UUID, key: str) -> str | None: ...

    async def compare_and_set_text(
        self, workspace_id: UUID, user_id: UUID, key: str, expected: str | None, value: str
    ) -> bool: ...


class StoreConflict(RuntimeError):
    """The document kept changing under us; the caller may retry the request."""


class UserIndex:
    """Which users have schedules (for the runner). Redis when available, else in process."""

    def __init__(self, redis: Any | None = None) -> None:
        self._redis = redis
        self._local: set[str] = set()

    @staticmethod
    def _member(workspace_id: UUID, user_id: UUID) -> str:
        return f"{workspace_id}:{user_id}"

    async def register(self, workspace_id: UUID, user_id: UUID) -> None:
        member = self._member(workspace_id, user_id)
        self._local.add(member)
        if self._redis is not None:
            try:
                await self._redis.sadd(INDEX_KEY, member)
            except Exception:  # noqa: BLE001 - the in-process copy still works
                logger.warning("schedule_index_write_failed")

    async def unregister(self, workspace_id: UUID, user_id: UUID) -> None:
        member = self._member(workspace_id, user_id)
        self._local.discard(member)
        if self._redis is not None:
            try:
                await self._redis.srem(INDEX_KEY, member)
            except Exception:  # noqa: BLE001
                logger.warning("schedule_index_write_failed")

    async def members(self) -> list[tuple[UUID, UUID]]:
        found = set(self._local)
        if self._redis is not None:
            try:
                for item in await self._redis.smembers(INDEX_KEY):
                    found.add(item.decode() if isinstance(item, bytes) else str(item))
            except Exception:  # noqa: BLE001
                logger.warning("schedule_index_read_failed")
        pairs: list[tuple[UUID, UUID]] = []
        for member in sorted(found):
            try:
                workspace, user = member.split(":", 1)
                pairs.append((UUID(workspace), UUID(user)))
            except ValueError:
                continue
        return pairs


def dump(doc: Doc) -> str:
    return json.dumps(doc.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False)


def parse(raw: str | None) -> Doc:
    if not raw:
        return Doc()
    try:
        return Doc.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        logger.warning("schedule_document_unreadable")
        return Doc()


class ScheduleStore:
    def __init__(self, backend: SettingsBackend, index: UserIndex | None = None) -> None:
        self._backend = backend
        self.index = index or UserIndex()

    async def load(self, workspace_id: UUID, user_id: UUID) -> Doc:
        return parse(await self._backend.get_text(workspace_id, user_id, SETTINGS_KEY))

    async def update(self, workspace_id: UUID, user_id: UUID, change: Callable[[Doc], T]) -> T:
        """Apply ``change`` to the current document and store it atomically (retrying on a race).

        ``change`` runs on a fresh copy each attempt, so it must be a pure function of the doc.
        Any exception it raises aborts without writing.
        """
        for _ in range(_ATTEMPTS):
            raw = await self._backend.get_text(workspace_id, user_id, SETTINGS_KEY)
            doc = parse(raw)
            result = change(doc)
            doc.rev += 1
            text = dump(doc)
            if len(text.encode()) > MAX_DOC_BYTES:
                raise ValueError("Schedules take too much space; delete one first.")
            if await self._backend.compare_and_set_text(
                workspace_id, user_id, SETTINGS_KEY, raw, text
            ):
                if doc.items:
                    await self.index.register(workspace_id, user_id)
                else:
                    await self.index.unregister(workspace_id, user_id)
                return result
        raise StoreConflict("The schedules changed while saving. Try again.")
