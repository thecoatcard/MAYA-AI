"""Persistence port for authentication.

Every method is one atomic step inside a transaction opened by ``AuthStore.transaction``. The
transaction commits when its block ends normally and rolls back when the block raises, so a
step whose effect must survive a failed attempt (counting a wrong one-time code) is decided
inside the block and acted on after it.
"""

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.auth.models import (
    AccountIdentity,
    AccountRecord,
    ChallengeRecord,
    RotatedRefresh,
    SessionRecord,
)


class AccountConflict(Exception):
    """The username or e-mail is already registered. Never shown to the client."""


class ChallengeConflict(Exception):
    """Another live challenge for the same account and purpose won a concurrent race."""


class AuthTransaction(Protocol):
    async def find_account_by_username(self, username: str) -> AccountRecord | None: ...

    async def find_account_by_email(self, email: str) -> AccountIdentity | None: ...

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
        """Insert the workspace and its first user; raise ``AccountConflict`` if either exists."""
        ...

    async def latest_challenge_created_at(self, user_id: UUID, purpose: str) -> datetime | None: ...

    async def invalidate_live_challenges(
        self, user_id: UUID, purpose: str, now: datetime
    ) -> None: ...

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
        """Store a challenge; raise ``ChallengeConflict`` if a live one already exists."""
        ...

    async def record_challenge_attempt(
        self, user_id: UUID, purpose: str, now: datetime, max_attempts: int
    ) -> ChallengeRecord | None:
        """Count one attempt against the live, unexpired challenge; None if there is none left."""
        ...

    async def consume_challenge(self, challenge_id: UUID, now: datetime) -> bool:
        """Mark a live challenge used. Exactly one concurrent caller gets True."""
        ...

    async def mark_email_verified(self, user_id: UUID, now: datetime) -> None: ...

    async def set_password(self, user_id: UUID, hashed_password: str, now: datetime) -> None: ...

    async def rehash_password(
        self, user_id: UUID, hashed_password: str, expected_password_changed_at: datetime
    ) -> bool:
        """Store a stronger hash of the same password; False if the password changed meanwhile."""
        ...

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
        """Open a session; False if the password changed since it was verified (a reset won)."""
        ...

    async def get_live_session(
        self, session_id: UUID, user_id: UUID, now: datetime
    ) -> SessionRecord | None: ...

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
        """Swap the live refresh id atomically and raise the generation; None if not allowed."""
        ...

    async def refresh_generation_of(self, session_id: UUID, user_id: UUID) -> int | None:
        """The session's current refresh generation, or None if there is no such session."""
        ...

    async def revoke_session(
        self, session_id: UUID, user_id: UUID, reason: str, now: datetime
    ) -> bool: ...

    async def revoke_user_sessions(self, user_id: UUID, reason: str, now: datetime) -> int: ...


class AuthStore(Protocol):
    def transaction(
        self, workspace_id: UUID | None
    ) -> AbstractAsyncContextManager[AuthTransaction]:
        """Open a transaction scoped to a workspace, or unscoped (only account lookups work)."""
        ...
