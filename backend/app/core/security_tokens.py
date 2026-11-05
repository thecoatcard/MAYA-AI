"""Signed access and refresh tokens (HS256). Expiry is checked against the injected clock."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt

from app.core.clock import Clock

ALGORITHM = "HS256"
ISSUER = "maya-api"
ACCESS_AUDIENCE = "maya-api-access"
REFRESH_AUDIENCE = "maya-api-refresh"
CLOCK_SKEW_SECONDS = 5
_REQUIRED_CLAIMS = ["exp", "iat", "sub", "jti", "iss", "aud"]


class TokenError(Exception):
    """Any invalid token. Deliberately carries no detail about why."""

    def __init__(self) -> None:
        super().__init__("Invalid token")


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    workspace_id: UUID
    role: str
    session_id: UUID
    token_id: UUID
    expires_at: datetime


@dataclass(frozen=True)
class RefreshClaims:
    user_id: UUID
    workspace_id: UUID
    session_id: UUID
    token_id: UUID
    generation: int
    expires_at: datetime


class TokenCodec:
    def __init__(self, key: bytes, clock: Clock, access_ttl: timedelta) -> None:
        self._key = key
        self._clock = clock
        self._access_ttl = access_ttl

    @property
    def access_ttl(self) -> timedelta:
        return self._access_ttl

    def issue_access(
        self, *, user_id: UUID, workspace_id: UUID, role: str, session_id: UUID, token_id: UUID
    ) -> tuple[str, datetime]:
        now = self._clock.now()
        expires_at = now + self._access_ttl
        claims = self._base_claims(user_id, token_id, ACCESS_AUDIENCE, "access", now, expires_at)
        claims.update({"workspace_id": str(workspace_id), "role": role, "sid": str(session_id)})
        return jwt.encode(claims, self._key, algorithm=ALGORITHM), expires_at

    def issue_refresh(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        session_id: UUID,
        token_id: UUID,
        generation: int,
        expires_at: datetime,
    ) -> str:
        claims = self._base_claims(
            user_id, token_id, REFRESH_AUDIENCE, "refresh", self._clock.now(), expires_at
        )
        claims.update(
            {"workspace_id": str(workspace_id), "sid": str(session_id), "gen": generation}
        )
        return jwt.encode(claims, self._key, algorithm=ALGORITHM)

    def decode_access(self, token: str) -> AccessClaims:
        payload = self._decode(token, ACCESS_AUDIENCE, "access", allow_expired=False)
        try:
            role = payload["role"]
            if not isinstance(role, str) or not role:
                raise TokenError
            return AccessClaims(
                user_id=UUID(payload["sub"]),
                workspace_id=UUID(payload["workspace_id"]),
                role=role,
                session_id=UUID(payload["sid"]),
                token_id=UUID(payload["jti"]),
                expires_at=_timestamp(payload["exp"]),
            )
        except (KeyError, ValueError, TypeError, AttributeError):
            raise TokenError from None

    def decode_refresh(self, token: str, *, allow_expired: bool = False) -> RefreshClaims:
        payload = self._decode(token, REFRESH_AUDIENCE, "refresh", allow_expired=allow_expired)
        try:
            generation = payload["gen"]
            if not _is_integer(generation) or generation < 0:
                raise TokenError
            return RefreshClaims(
                user_id=UUID(payload["sub"]),
                workspace_id=UUID(payload["workspace_id"]),
                session_id=UUID(payload["sid"]),
                token_id=UUID(payload["jti"]),
                generation=generation,
                expires_at=_timestamp(payload["exp"]),
            )
        except (KeyError, ValueError, TypeError, AttributeError):
            raise TokenError from None

    @staticmethod
    def _base_claims(
        user_id: UUID,
        token_id: UUID,
        audience: str,
        token_type: str,
        now: datetime,
        expires_at: datetime,
    ) -> dict[str, Any]:
        return {
            "iss": ISSUER,
            "aud": audience,
            "sub": str(user_id),
            "jti": str(token_id),
            "typ": token_type,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }

    def _decode(
        self, token: str, audience: str, token_type: str, *, allow_expired: bool
    ) -> dict[str, Any]:
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._key,
                algorithms=[ALGORITHM],
                audience=audience,
                issuer=ISSUER,
                options={
                    "require": _REQUIRED_CLAIMS,
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except (jwt.PyJWTError, ValueError, TypeError):
            raise TokenError from None
        now = int(self._clock.now().timestamp())
        expires, issued = payload["exp"], payload["iat"]
        if payload.get("typ") != token_type or "nbf" in payload:
            raise TokenError
        if not _is_integer(expires) or not _is_integer(issued):
            raise TokenError
        if issued > now + CLOCK_SKEW_SECONDS:
            raise TokenError
        if not allow_expired and now >= expires:
            raise TokenError
        return payload


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)
