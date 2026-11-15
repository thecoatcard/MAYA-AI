"""Who may use the SERVER-LEVEL connections (MAYA_GITHUB_TOKEN, MAYA_NOTION_*, MAYA_MAIL_*, Google).

Those values belong to the deployment owner. Without this check every user who has no connection of
their own would silently reach the owner's Notion, GitHub and mailbox. The owner is named by
``MAYA_ENV_CONNECTIONS_USERS`` (comma-separated account emails or user ids). Fail closed: an empty
list means nobody, and any lookup error means "not the owner" (errors are never cached).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text

from app.db.tenant import tenant_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

EmailLookup = Callable[[UUID, UUID], Awaitable[str | None]]

# The caller's own row: visible under their tenant session (row level security), no migration.
_OWN_EMAIL = text("SELECT email FROM users WHERE id = :user_id")


def parse_allowed(value: str | None) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in (value or "").split(",") if part.strip())


class DeploymentOwnership:
    def __init__(
        self,
        lookup: EmailLookup | None,
        allowed: frozenset[str] | set[str] | str,
        *,
        ttl: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lookup = lookup
        self._allowed = parse_allowed(allowed) if isinstance(allowed, str) else frozenset(allowed)
        self._ttl = ttl
        self._clock = clock
        self._cache: dict[tuple[UUID, UUID], tuple[bool, float]] = {}

    @property
    def configured(self) -> bool:
        """True when at least one owner is named."""
        return bool(self._allowed)

    async def is_owner(self, workspace_id: UUID, user_id: UUID) -> bool:
        if not self._allowed:
            return False  # fail closed: nobody until the owner names themselves
        if str(user_id).lower() in self._allowed:
            return True
        key = (workspace_id, user_id)
        hit = self._cache.get(key)
        now = self._clock()
        if hit is not None and hit[1] > now:
            return hit[0]
        if self._lookup is None:
            return False
        try:
            email = await self._lookup(workspace_id, user_id)
        except Exception:  # noqa: BLE001 - any failure means "not the owner", never cached
            logger.warning("deployment_owner_lookup_failed")
            return False
        owner = bool(email) and email.strip().lower() in self._allowed  # type: ignore[union-attr]
        self._cache[key] = (owner, now + self._ttl)
        return owner


def db_email_lookup(engine: AsyncEngine) -> EmailLookup:
    async def lookup(workspace_id: UUID, user_id: UUID) -> str | None:
        async with tenant_session(engine, workspace_id, user_id) as session:
            row = (await session.execute(_OWN_EMAIL, {"user_id": user_id})).first()
        return None if row is None else str(row.email)

    return lookup


def ownership_from_settings(settings: Any, engine: AsyncEngine | None) -> DeploymentOwnership:
    allowed = parse_allowed(getattr(settings, "env_connections_users", ""))
    return DeploymentOwnership(db_email_lookup(engine) if engine is not None else None, allowed)
