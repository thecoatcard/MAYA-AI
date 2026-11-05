from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class AccountIdentity:
    """What the e-mail lookup returns: enough to address a code, never a credential."""

    user_id: UUID
    workspace_id: UUID
    username: str
    email: str
    email_verified_at: datetime | None
    role: str


@dataclass(frozen=True)
class AccountRecord(AccountIdentity):
    """What the username lookup returns for login. ``password_changed_at`` marks the password
    version that was verified, so a login cannot outlive a concurrent reset."""

    hashed_password: str
    password_changed_at: datetime


@dataclass(frozen=True)
class RotatedRefresh:
    refresh_expires_at: datetime
    generation: int


@dataclass(frozen=True)
class ChallengeRecord:
    id: UUID
    user_id: UUID
    purpose: str
    code_hmac: bytes
    attempts: int
    expires_at: datetime


@dataclass(frozen=True)
class SessionRecord:
    id: UUID
    user_id: UUID
    workspace_id: UUID
    role: str
    refresh_jti: UUID
    refresh_expires_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, as verified against a live session."""

    user_id: UUID
    workspace_id: UUID
    role: str
    session_id: UUID


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
