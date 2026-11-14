"""Per-user settings (migration 0015). Every statement names the workspace and the user."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.tenant import tenant_session

CLASSIFIER_KEY = "chat_intent_classifier"
MEMORY_IN_CHAT_KEY = "memory_in_chat"  # default ON: only an explicit false turns it off
WORKSPACE_FOLDER_KEY = "workspace_folder"  # the connected folder (text), ADR-0022

_READ = text(
    "SELECT settings ->> :key FROM user_settings WHERE workspace_id = :ws AND user_id = :user"
)
_UPSERT = text(
    "INSERT INTO user_settings (workspace_id, user_id, settings) "
    "VALUES (:ws, :user, jsonb_build_object(CAST(:key AS text), CAST(:value AS boolean))) "
    "ON CONFLICT (workspace_id, user_id) DO UPDATE SET "
    "settings = user_settings.settings || EXCLUDED.settings, updated_at = now()"
)
_UPSERT_TEXT = text(
    "INSERT INTO user_settings (workspace_id, user_id, settings) "
    "VALUES (:ws, :user, jsonb_build_object(CAST(:key AS text), CAST(:value AS text))) "
    "ON CONFLICT (workspace_id, user_id) DO UPDATE SET "
    "settings = user_settings.settings || EXCLUDED.settings, updated_at = now()"
)
# Compare-and-set (see ``compare_and_set_text``): absent key -> insert / merge only when the key
# is still absent; present key -> update only when the stored text equals the expected text.
_CAS_INSERT = text(
    "INSERT INTO user_settings (workspace_id, user_id, settings) "
    "VALUES (:ws, :user, jsonb_build_object(CAST(:key AS text), CAST(:value AS text))) "
    "ON CONFLICT (workspace_id, user_id) DO UPDATE SET "
    "settings = user_settings.settings || EXCLUDED.settings, updated_at = now() "
    "WHERE (user_settings.settings ->> CAST(:key AS text)) IS NULL"
)
_CAS_UPDATE = text(
    "UPDATE user_settings SET "
    "settings = settings || jsonb_build_object(CAST(:key AS text), CAST(:value AS text)), "
    "updated_at = now() "
    "WHERE workspace_id = :ws AND user_id = :user "
    "AND (settings ->> CAST(:key AS text)) = CAST(:expected AS text)"
)
_REMOVE_KEY = text(
    "UPDATE user_settings SET settings = settings - CAST(:key AS text), updated_at = now() "
    "WHERE workspace_id = :ws AND user_id = :user"
)


class UserSettingsStore:
    """Reads and writes the caller's own settings; also the chat ``ChatPreferences``."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def intent_classifier_enabled(self, workspace_id: UUID, user_id: UUID) -> bool:
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            value = (
                await session.execute(
                    _READ, {"ws": workspace_id, "user": user_id, "key": CLASSIFIER_KEY}
                )
            ).scalar_one_or_none()
        return value == "true"

    async def memory_in_chat_enabled(self, workspace_id: UUID, user_id: UUID) -> bool:
        """Whether chat may save and recall memories for this user. On unless set to false."""
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            value = (
                await session.execute(
                    _READ, {"ws": workspace_id, "user": user_id, "key": MEMORY_IN_CHAT_KEY}
                )
            ).scalar_one_or_none()
        return value != "false"

    async def set_memory_in_chat(self, workspace_id: UUID, user_id: UUID, enabled: bool) -> None:
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            await session.execute(
                _UPSERT,
                {"ws": workspace_id, "user": user_id, "key": MEMORY_IN_CHAT_KEY, "value": enabled},
            )
            await session.commit()

    async def set_intent_classifier(self, workspace_id: UUID, user_id: UUID, enabled: bool) -> None:
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            await session.execute(
                _UPSERT,
                {"ws": workspace_id, "user": user_id, "key": CLASSIFIER_KEY, "value": enabled},
            )
            await session.commit()

    async def get_workspace_folder(self, workspace_id: UUID, user_id: UUID) -> str | None:
        """The connected folder path the user stored, or ``None`` (the folder service validates)."""
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            value = (
                await session.execute(
                    _READ, {"ws": workspace_id, "user": user_id, "key": WORKSPACE_FOLDER_KEY}
                )
            ).scalar_one_or_none()
        return value if isinstance(value, str) and value else None

    async def set_workspace_folder(
        self, workspace_id: UUID, user_id: UUID, path: str | None
    ) -> None:
        """Store the connected folder, or remove the key when ``path`` is ``None``."""
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            if path is None:
                await session.execute(
                    _REMOVE_KEY,
                    {"ws": workspace_id, "user": user_id, "key": WORKSPACE_FOLDER_KEY},
                )
            else:
                await session.execute(
                    _UPSERT_TEXT,
                    {
                        "ws": workspace_id,
                        "user": user_id,
                        "key": WORKSPACE_FOLDER_KEY,
                        "value": path,
                    },
                )
            await session.commit()

    # Generic text keys, used by ``app.connections`` for encrypted credential blobs. The keys are
    # chosen by code, never by a request; reads and writes still name the workspace and the user.

    async def get_text(self, workspace_id: UUID, user_id: UUID, key: str) -> str | None:
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            value = (
                await session.execute(_READ, {"ws": workspace_id, "user": user_id, "key": key})
            ).scalar_one_or_none()
        return value if isinstance(value, str) and value else None

    async def set_text(self, workspace_id: UUID, user_id: UUID, key: str, value: str) -> None:
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            await session.execute(
                _UPSERT_TEXT, {"ws": workspace_id, "user": user_id, "key": key, "value": value}
            )
            await session.commit()

    async def compare_and_set_text(
        self, workspace_id: UUID, user_id: UUID, key: str, expected: str | None, value: str
    ) -> bool:
        """Store ``value`` only when the key still holds ``expected`` (``None`` = absent).

        The optimistic-concurrency primitive for JSON documents kept in one key (scheduled
        reports). Returns False when another writer changed the key first; the caller re-reads.
        """
        params = {"ws": workspace_id, "user": user_id, "key": key, "value": value}
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            if expected is None:
                result = await session.execute(_CAS_INSERT, params)
            else:
                result = await session.execute(_CAS_UPDATE, {**params, "expected": expected})
            await session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def remove_key(self, workspace_id: UUID, user_id: UUID, key: str) -> None:
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            await session.execute(_REMOVE_KEY, {"ws": workspace_id, "user": user_id, "key": key})
            await session.commit()
