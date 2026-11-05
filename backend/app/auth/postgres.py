"""PostgreSQL implementation of the authentication store.

All SQL is static text with bind parameters. It runs as the restricted runtime role, so it can
only name the columns that role may read (never ``users.hashed_password``; that comes from the
definer lookup functions).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.auth.models import (
    AccountIdentity,
    AccountRecord,
    ChallengeRecord,
    RotatedRefresh,
    SessionRecord,
)
from app.auth.store import AccountConflict, AuthTransaction, ChallengeConflict
from app.db.tenant import tenant_transaction

FIND_BY_USERNAME = text(
    "SELECT user_id, workspace_id, username, email, email_verified_at, role, hashed_password, "
    "password_changed_at FROM public.auth_find_account_by_username(:value)"
)
FIND_BY_EMAIL = text(
    "SELECT user_id, workspace_id, username, email, email_verified_at, role "
    "FROM public.auth_find_account_by_email(:value)"
)

INSERT_WORKSPACE = text(
    "INSERT INTO workspaces (id, name, slug, created_at) VALUES (:id, :name, :slug, :now) "
    "ON CONFLICT DO NOTHING RETURNING id"
)
INSERT_USER = text(
    "INSERT INTO users (id, workspace_id, username, email, full_name, hashed_password, role, "
    "password_changed_at, created_at) "
    "VALUES (:id, :workspace_id, :username, :email, :full_name, :hashed_password, :role, "
    ":now, :now) "
    "ON CONFLICT DO NOTHING RETURNING id"
)
LATEST_CHALLENGE = text(
    "SELECT max(created_at) FROM auth_otp_challenges "
    "WHERE user_id = :user_id AND purpose = :purpose"
)
INVALIDATE_CHALLENGES = text(
    "UPDATE auth_otp_challenges SET invalidated_at = :now WHERE user_id = :user_id "
    "AND purpose = :purpose AND consumed_at IS NULL AND invalidated_at IS NULL"
)
INSERT_CHALLENGE = text(
    "INSERT INTO auth_otp_challenges (id, workspace_id, user_id, purpose, code_hmac, expires_at, "
    "created_at) VALUES (:id, :workspace_id, :user_id, :purpose, :code_hmac, :expires_at, :now)"
)
RECORD_ATTEMPT = text(
    "UPDATE auth_otp_challenges SET attempts = attempts + 1 "
    "WHERE user_id = :user_id AND purpose = :purpose AND consumed_at IS NULL "
    "AND invalidated_at IS NULL AND expires_at > :now AND attempts < :max_attempts "
    "RETURNING id, user_id, purpose, code_hmac, attempts, expires_at"
)
CONSUME_CHALLENGE = text(
    "UPDATE auth_otp_challenges SET consumed_at = :now WHERE id = :id "
    "AND consumed_at IS NULL AND invalidated_at IS NULL RETURNING id"
)
MARK_VERIFIED = text(
    "UPDATE users SET email_verified_at = :now WHERE id = :user_id AND email_verified_at IS NULL"
)
SET_PASSWORD = text(
    "UPDATE users SET hashed_password = :hashed_password, password_changed_at = :now "
    "WHERE id = :user_id"
)
REHASH_PASSWORD = text(
    "UPDATE users SET hashed_password = :hashed_password "
    "WHERE id = :user_id AND password_changed_at = :expected RETURNING id"
)
# Only if the password version that was verified is still the current one.
INSERT_SESSION = text(
    "INSERT INTO auth_sessions (id, workspace_id, user_id, refresh_jti, refresh_expires_at, "
    "absolute_expires_at, created_at, last_used_at) "
    "SELECT CAST(:id AS uuid), CAST(:workspace_id AS uuid), CAST(:user_id AS uuid), "
    "CAST(:refresh_jti AS uuid), CAST(:refresh_expires_at AS timestamptz), "
    "CAST(:absolute_expires_at AS timestamptz), CAST(:now AS timestamptz), "
    "CAST(:now AS timestamptz) "
    "WHERE EXISTS (SELECT 1 FROM users WHERE id = :user_id "
    "AND password_changed_at = :expected) RETURNING id"
)
LIVE_SESSION = text(
    "SELECT s.id, s.user_id, s.workspace_id, u.role, s.refresh_jti, s.refresh_expires_at, "
    "s.absolute_expires_at FROM auth_sessions AS s JOIN users AS u ON u.id = s.user_id "
    "WHERE s.id = :id AND s.user_id = :user_id AND s.revoked_at IS NULL "
    "AND s.absolute_expires_at > :now"
)
ROTATE_REFRESH = text(
    "UPDATE auth_sessions SET refresh_jti = :new_jti, "
    "refresh_generation = refresh_generation + 1, "
    "refresh_expires_at = LEAST(CAST(:sliding AS timestamptz), absolute_expires_at), "
    "last_used_at = :now "
    "WHERE id = :id AND user_id = :user_id AND refresh_jti = :current_jti "
    "AND revoked_at IS NULL AND refresh_expires_at > :now AND absolute_expires_at > :now "
    "RETURNING refresh_expires_at, refresh_generation"
)
REFRESH_GENERATION = text(
    "SELECT refresh_generation FROM auth_sessions WHERE id = :id AND user_id = :user_id"
)
REVOKE_SESSION = text(
    "UPDATE auth_sessions SET revoked_at = :now, revoked_reason = :reason "
    "WHERE id = :id AND user_id = :user_id AND revoked_at IS NULL"
)
REVOKE_USER_SESSIONS = text(
    "UPDATE auth_sessions SET revoked_at = :now, revoked_reason = :reason "
    "WHERE user_id = :user_id AND revoked_at IS NULL"
)


def _identity(row: Any) -> AccountIdentity:
    return AccountIdentity(
        user_id=row.user_id,
        workspace_id=row.workspace_id,
        username=row.username,
        email=row.email,
        email_verified_at=row.email_verified_at,
        role=row.role,
    )


def _account(row: Any) -> AccountRecord:
    return AccountRecord(
        user_id=row.user_id,
        workspace_id=row.workspace_id,
        username=row.username,
        email=row.email,
        email_verified_at=row.email_verified_at,
        role=row.role,
        hashed_password=row.hashed_password,
        password_changed_at=row.password_changed_at,
    )


class PostgresAuthTransaction:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def find_account_by_username(self, username: str) -> AccountRecord | None:
        row = (await self._connection.execute(FIND_BY_USERNAME, {"value": username})).first()
        return None if row is None else _account(row)

    async def find_account_by_email(self, email: str) -> AccountIdentity | None:
        row = (await self._connection.execute(FIND_BY_EMAIL, {"value": email})).first()
        return None if row is None else _identity(row)

    async def create_account(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        workspace_name: str,
        slug: str,
        username: str,
        email: str,
        full_name: str,
        hashed_password: str,
        role: str,
        now: datetime,
    ) -> None:
        workspace = await self._connection.execute(
            INSERT_WORKSPACE, {"id": workspace_id, "name": workspace_name, "slug": slug, "now": now}
        )
        if workspace.first() is None:
            raise AccountConflict
        user = await self._connection.execute(
            INSERT_USER,
            {
                "id": user_id,
                "workspace_id": workspace_id,
                "username": username,
                "email": email,
                "full_name": full_name,
                "hashed_password": hashed_password,
                "role": role,
                "now": now,
            },
        )
        if user.first() is None:
            raise AccountConflict

    async def latest_challenge_created_at(self, user_id: UUID, purpose: str) -> datetime | None:
        result = await self._connection.execute(
            LATEST_CHALLENGE, {"user_id": user_id, "purpose": purpose}
        )
        value: datetime | None = result.scalar_one()
        return value

    async def invalidate_live_challenges(self, user_id: UUID, purpose: str, now: datetime) -> None:
        await self._connection.execute(
            INVALIDATE_CHALLENGES, {"user_id": user_id, "purpose": purpose, "now": now}
        )

    async def create_challenge(
        self,
        *,
        challenge_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        purpose: str,
        code_hmac: bytes,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        try:
            await self._connection.execute(
                INSERT_CHALLENGE,
                {
                    "id": challenge_id,
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "purpose": purpose,
                    "code_hmac": code_hmac,
                    "expires_at": expires_at,
                    "now": now,
                },
            )
        except IntegrityError:
            raise ChallengeConflict from None

    async def record_challenge_attempt(
        self, user_id: UUID, purpose: str, now: datetime, max_attempts: int
    ) -> ChallengeRecord | None:
        row = (
            await self._connection.execute(
                RECORD_ATTEMPT,
                {"user_id": user_id, "purpose": purpose, "now": now, "max_attempts": max_attempts},
            )
        ).first()
        if row is None:
            return None
        return ChallengeRecord(
            id=row.id,
            user_id=row.user_id,
            purpose=row.purpose,
            code_hmac=bytes(row.code_hmac),
            attempts=row.attempts,
            expires_at=row.expires_at,
        )

    async def consume_challenge(self, challenge_id: UUID, now: datetime) -> bool:
        result = await self._connection.execute(CONSUME_CHALLENGE, {"id": challenge_id, "now": now})
        return result.first() is not None

    async def mark_email_verified(self, user_id: UUID, now: datetime) -> None:
        await self._connection.execute(MARK_VERIFIED, {"user_id": user_id, "now": now})

    async def set_password(self, user_id: UUID, hashed_password: str, now: datetime) -> None:
        await self._connection.execute(
            SET_PASSWORD, {"user_id": user_id, "hashed_password": hashed_password, "now": now}
        )

    async def rehash_password(
        self, user_id: UUID, hashed_password: str, expected_password_changed_at: datetime
    ) -> bool:
        result = await self._connection.execute(
            REHASH_PASSWORD,
            {
                "user_id": user_id,
                "hashed_password": hashed_password,
                "expected": expected_password_changed_at,
            },
        )
        return result.first() is not None

    async def create_session(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        refresh_jti: UUID,
        refresh_expires_at: datetime,
        absolute_expires_at: datetime,
        expected_password_changed_at: datetime,
        now: datetime,
    ) -> bool:
        result = await self._connection.execute(
            INSERT_SESSION,
            {
                "id": session_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "refresh_jti": refresh_jti,
                "refresh_expires_at": refresh_expires_at,
                "absolute_expires_at": absolute_expires_at,
                "expected": expected_password_changed_at,
                "now": now,
            },
        )
        return result.first() is not None

    async def get_live_session(
        self, session_id: UUID, user_id: UUID, now: datetime
    ) -> SessionRecord | None:
        row = (
            await self._connection.execute(
                LIVE_SESSION, {"id": session_id, "user_id": user_id, "now": now}
            )
        ).first()
        if row is None:
            return None
        return SessionRecord(
            id=row.id,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            role=row.role,
            refresh_jti=row.refresh_jti,
            refresh_expires_at=row.refresh_expires_at,
            absolute_expires_at=row.absolute_expires_at,
        )

    async def rotate_refresh(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        current_jti: UUID,
        new_jti: UUID,
        sliding_expires_at: datetime,
        now: datetime,
    ) -> RotatedRefresh | None:
        row = (
            await self._connection.execute(
                ROTATE_REFRESH,
                {
                    "id": session_id,
                    "user_id": user_id,
                    "current_jti": current_jti,
                    "new_jti": new_jti,
                    "sliding": sliding_expires_at,
                    "now": now,
                },
            )
        ).first()
        if row is None:
            return None
        return RotatedRefresh(row.refresh_expires_at, row.refresh_generation)

    async def refresh_generation_of(self, session_id: UUID, user_id: UUID) -> int | None:
        result = await self._connection.execute(
            REFRESH_GENERATION, {"id": session_id, "user_id": user_id}
        )
        row = result.first()
        return None if row is None else int(row.refresh_generation)

    async def revoke_session(
        self, session_id: UUID, user_id: UUID, reason: str, now: datetime
    ) -> bool:
        result = await self._connection.execute(
            REVOKE_SESSION, {"id": session_id, "user_id": user_id, "reason": reason, "now": now}
        )
        return result.rowcount == 1

    async def revoke_user_sessions(self, user_id: UUID, reason: str, now: datetime) -> int:
        result = await self._connection.execute(
            REVOKE_USER_SESSIONS, {"user_id": user_id, "reason": reason, "now": now}
        )
        return int(result.rowcount)


class PostgresAuthStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def transaction(self, workspace_id: UUID | None) -> AsyncIterator[AuthTransaction]:
        async with tenant_transaction(self._engine, workspace_id) as connection:
            yield PostgresAuthTransaction(connection)
