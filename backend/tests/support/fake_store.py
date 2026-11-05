"""In-memory AuthStore that behaves like the PostgreSQL one where it matters.

It mimics workspace scoping (a transaction without the right workspace sees and writes
nothing), the unique constraints, the single live challenge per purpose, rollback on error and
the conditional updates that make one-time codes and refresh tokens single use. The same store
tests also run against the real database, so the two are kept honest against each other.
"""

import copy
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.auth.models import (
    AccountIdentity,
    AccountRecord,
    ChallengeRecord,
    RotatedRefresh,
    SessionRecord,
)
from app.auth.store import AccountConflict, AuthTransaction, ChallengeConflict


@dataclass
class UserRow:
    id: UUID
    workspace_id: UUID
    username: str
    email: str
    full_name: str
    hashed_password: str
    email_verified_at: datetime | None
    password_changed_at: datetime
    role: str


@dataclass
class ChallengeRow:
    id: UUID
    workspace_id: UUID
    user_id: UUID
    purpose: str
    code_hmac: bytes
    attempts: int
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None
    invalidated_at: datetime | None = None

    @property
    def live(self) -> bool:
        return self.consumed_at is None and self.invalidated_at is None


@dataclass
class SessionRow:
    id: UUID
    workspace_id: UUID
    user_id: UUID
    refresh_jti: UUID
    refresh_generation: int
    refresh_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None
    revoked_reason: str | None = None


class FakeAuthStore:
    def __init__(self) -> None:
        self.workspaces: dict[UUID, str] = {}
        self.users: dict[UUID, UserRow] = {}
        self.challenges: list[ChallengeRow] = []
        self.sessions: dict[UUID, SessionRow] = {}

    @asynccontextmanager
    async def transaction(self, workspace_id: UUID | None) -> AsyncIterator[AuthTransaction]:
        snapshot = copy.deepcopy((self.workspaces, self.users, self.challenges, self.sessions))
        try:
            yield _FakeTransaction(self, workspace_id)
        except BaseException:
            self.workspaces, self.users, self.challenges, self.sessions = snapshot
            raise

    # Test conveniences (read without a transaction).
    def user(self, username: str) -> UserRow:
        return next(user for user in self.users.values() if user.username == username)

    def challenges_of(self, user_id: UUID) -> list[ChallengeRow]:
        return [row for row in self.challenges if row.user_id == user_id]

    def sessions_of(self, user_id: UUID) -> list[SessionRow]:
        return [row for row in self.sessions.values() if row.user_id == user_id]


class _FakeTransaction:
    def __init__(self, store: FakeAuthStore, workspace_id: UUID | None) -> None:
        self._store = store
        self._workspace = workspace_id

    def _identity(self, user: UserRow) -> AccountIdentity:
        return AccountIdentity(
            user_id=user.id,
            workspace_id=user.workspace_id,
            username=user.username,
            email=user.email,
            email_verified_at=user.email_verified_at,
            role=user.role,
        )

    def _account(self, user: UserRow) -> AccountRecord:
        return AccountRecord(
            user_id=user.id,
            workspace_id=user.workspace_id,
            username=user.username,
            email=user.email,
            email_verified_at=user.email_verified_at,
            role=user.role,
            hashed_password=user.hashed_password,
            password_changed_at=user.password_changed_at,
        )

    def _visible(self, workspace_id: UUID) -> bool:
        return self._workspace is not None and workspace_id == self._workspace

    async def find_account_by_username(self, username: str) -> AccountRecord | None:
        for user in self._store.users.values():  # definer function: ignores the context
            if user.username == username:
                return self._account(user)
        return None

    async def find_account_by_email(self, email: str) -> AccountIdentity | None:
        for user in self._store.users.values():
            if user.email == email:
                return self._identity(user)
        return None

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
        if not self._visible(workspace_id):
            raise PermissionError("new row violates row-level security policy")
        if slug in self._store.workspaces.values():
            raise AccountConflict
        if any(u.username == username or u.email == email for u in self._store.users.values()):
            raise AccountConflict
        self._store.workspaces[workspace_id] = slug
        self._store.users[user_id] = UserRow(
            id=user_id,
            workspace_id=workspace_id,
            username=username,
            email=email,
            full_name=full_name,
            hashed_password=hashed_password,
            email_verified_at=None,
            password_changed_at=now,
            role=role,
        )

    async def latest_challenge_created_at(self, user_id: UUID, purpose: str) -> datetime | None:
        times = [
            row.created_at
            for row in self._store.challenges
            if row.user_id == user_id and row.purpose == purpose and self._visible(row.workspace_id)
        ]
        return max(times) if times else None

    async def invalidate_live_challenges(self, user_id: UUID, purpose: str, now: datetime) -> None:
        for row in self._store.challenges:
            if (
                row.user_id == user_id
                and row.purpose == purpose
                and row.live
                and self._visible(row.workspace_id)
            ):
                row.invalidated_at = now

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
        if not self._visible(workspace_id):
            raise PermissionError("new row violates row-level security policy")
        if any(
            row.user_id == user_id and row.purpose == purpose and row.live
            for row in self._store.challenges
        ):
            raise ChallengeConflict
        self._store.challenges.append(
            ChallengeRow(
                challenge_id, workspace_id, user_id, purpose, code_hmac, 0, expires_at, now
            )
        )

    async def record_challenge_attempt(
        self, user_id: UUID, purpose: str, now: datetime, max_attempts: int
    ) -> ChallengeRecord | None:
        for row in self._store.challenges:
            if (
                row.user_id == user_id
                and row.purpose == purpose
                and row.live
                and row.expires_at > now
                and row.attempts < max_attempts
                and self._visible(row.workspace_id)
            ):
                row.attempts += 1
                return ChallengeRecord(
                    row.id, row.user_id, row.purpose, row.code_hmac, row.attempts, row.expires_at
                )
        return None

    async def consume_challenge(self, challenge_id: UUID, now: datetime) -> bool:
        for row in self._store.challenges:
            if row.id == challenge_id and row.live and self._visible(row.workspace_id):
                row.consumed_at = now
                return True
        return False

    def _own_user(self, user_id: UUID) -> UserRow | None:
        user = self._store.users.get(user_id)
        return user if user is not None and self._visible(user.workspace_id) else None

    async def mark_email_verified(self, user_id: UUID, now: datetime) -> None:
        user = self._own_user(user_id)
        if user is not None and user.email_verified_at is None:
            user.email_verified_at = now

    async def set_password(self, user_id: UUID, hashed_password: str, now: datetime) -> None:
        user = self._own_user(user_id)
        if user is not None:
            user.hashed_password = hashed_password
            user.password_changed_at = now

    async def rehash_password(
        self, user_id: UUID, hashed_password: str, expected_password_changed_at: datetime
    ) -> bool:
        user = self._own_user(user_id)
        if user is None or user.password_changed_at != expected_password_changed_at:
            return False
        user.hashed_password = hashed_password
        return True

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
        if not self._visible(workspace_id):
            raise PermissionError("new row violates row-level security policy")
        user = self._own_user(user_id)
        if user is None or user.password_changed_at != expected_password_changed_at:
            return False
        self._store.sessions[session_id] = SessionRow(
            session_id,
            workspace_id,
            user_id,
            refresh_jti,
            0,
            refresh_expires_at,
            absolute_expires_at,
        )
        return True

    def _session(self, session_id: UUID, user_id: UUID) -> SessionRow | None:
        row = self._store.sessions.get(session_id)
        if row is None or row.user_id != user_id or not self._visible(row.workspace_id):
            return None
        return row

    async def get_live_session(
        self, session_id: UUID, user_id: UUID, now: datetime
    ) -> SessionRecord | None:
        row = self._session(session_id, user_id)
        user = self._store.users.get(user_id)
        if row is None or user is None or row.revoked_at is not None:
            return None
        if row.absolute_expires_at <= now:
            return None
        return SessionRecord(
            row.id,
            row.user_id,
            row.workspace_id,
            user.role,
            row.refresh_jti,
            row.refresh_expires_at,
            row.absolute_expires_at,
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
        row = self._session(session_id, user_id)
        if (
            row is None
            or row.refresh_jti != current_jti
            or row.revoked_at is not None
            or row.refresh_expires_at <= now
            or row.absolute_expires_at <= now
        ):
            return None
        row.refresh_jti = new_jti
        row.refresh_generation += 1
        row.refresh_expires_at = min(sliding_expires_at, row.absolute_expires_at)
        return RotatedRefresh(row.refresh_expires_at, row.refresh_generation)

    async def refresh_generation_of(self, session_id: UUID, user_id: UUID) -> int | None:
        row = self._session(session_id, user_id)
        return None if row is None else row.refresh_generation

    async def revoke_session(
        self, session_id: UUID, user_id: UUID, reason: str, now: datetime
    ) -> bool:
        row = self._session(session_id, user_id)
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at, row.revoked_reason = now, reason
        return True

    async def revoke_user_sessions(self, user_id: UUID, reason: str, now: datetime) -> int:
        changed = 0
        for row in self._store.sessions.values():
            if (
                row.user_id == user_id
                and row.revoked_at is None
                and self._visible(row.workspace_id)
            ):
                row.revoked_at, row.revoked_reason = now, reason
                changed += 1
        return changed
