"""The authentication flows: register, verify e-mail, login, reset password, refresh, logout.

Design rules enforced here (the reasons are in docs/security/AUTHENTICATION.md):

* Anything that could reveal whether an account exists answers identically for known and
  unknown accounts and spends comparable work (a password hash is computed or verified either way).
* One-time codes are stored only as keyed MACs; a wrong guess is counted and *committed*
  before the failure is reported.
* A transaction that must keep its effect never raises: the outcome is returned out of the
  ``async with`` block and acted on afterwards.
* Raw e-mail addresses and source addresses are never used as rate-limit keys; keyed digests are.
* Login attempts are counted atomically *before* the expensive password check, so parallel
  guesses cannot all pass a check that only later records failures.
* A session is opened only if the password version that was verified is still current, so a
  login cannot outlive a concurrent password reset.
"""

import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from app.auth.errors import (
    EmailNotVerified,
    InvalidCode,
    InvalidCredentials,
    InvalidToken,
    RateLimited,
    ServiceUnavailable,
)
from app.auth.events import AuthEvents
from app.auth.identifiers import normalize_email, normalize_username
from app.auth.messages import code_email
from app.auth.models import AccountIdentity, AccountRecord, Principal, TokenPair
from app.auth.policy import AuthPolicy
from app.auth.store import AccountConflict, AuthStore, AuthTransaction, ChallengeConflict
from app.core.clock import Clock
from app.core.email import EmailDeliveryError, EmailSender, OutboundEmail
from app.core.ratelimit import Limit, RateLimiter, RateLimiterUnavailable
from app.core.security import PasswordService, validate_password_policy
from app.core.security_otp import (
    OTP_LIFETIME,
    OTP_MAX_ATTEMPTS,
    OtpPurpose,
    code_mac,
    code_matches,
    generate_code,
    identifier_digest,
)
from app.core.security_tokens import TokenCodec, TokenError

WORKSPACE_ADMIN_ROLE = "admin"
_DUMMY_MAC = bytes(32)

OnSuccess = Callable[[AuthTransaction, datetime], Awaitable[None]]


@dataclass(frozen=True)
class RegisterCommand:
    username: str
    email: str
    password: str
    full_name: str
    workspace_name: str | None = None


class AuthService:
    def __init__(
        self,
        *,
        store: AuthStore,
        passwords: PasswordService,
        tokens: TokenCodec,
        otp_key: bytes,
        clock: Clock,
        limiter: RateLimiter,
        events: AuthEvents,
        email_sender: EmailSender | None,
        policy: AuthPolicy,
    ) -> None:
        self._store = store
        self._passwords = passwords
        self._tokens = tokens
        self._otp_key = otp_key
        self._clock = clock
        self._limiter = limiter
        self._events = events
        self._email_sender = email_sender
        self._policy = policy

    # ------------------------------------------------------------------ registration

    async def register(self, command: RegisterCommand, address: str) -> OutboundEmail | None:
        """Create an unverified account and its own workspace; None if nothing is to be sent."""
        self._require_email()
        username = normalize_username(command.username)
        email = normalize_email(command.email)
        validate_password_policy(command.password, forbidden=(username, email))
        await self._enforce_send_limits(email, address)
        hashed_password = await self._passwords.hash(command.password)
        now = self._clock.now()
        workspace_id, user_id = uuid4(), uuid4()
        try:
            async with self._store.transaction(workspace_id) as tx:
                await tx.create_account(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    workspace_name=(command.workspace_name or f"{command.full_name}'s workspace")[
                        :255
                    ],
                    slug=f"ws-{secrets.token_hex(6)}",
                    username=username,
                    email=email,
                    full_name=command.full_name,
                    hashed_password=hashed_password,
                    role=WORKSPACE_ADMIN_ROLE,
                    now=now,
                )
                code = await self._create_challenge(
                    tx, workspace_id, user_id, "email_verification", now
                )
        except AccountConflict:
            # Same answer as a fresh registration. An unverified owner of this address may be
            # sent a new code, so an abandoned or squatted registration can still be completed.
            self._events.emit("register", outcome="duplicate")
            account = await self._find_by_email(email)
            if account is None or account.email_verified_at is not None:
                return None
            return await self._send_challenge(account, "email_verification")
        self._events.emit("register", outcome="created")
        return code_email(email, "email_verification", code, username=username)

    async def resend_verification(self, email: str, address: str) -> OutboundEmail | None:
        self._require_email()
        email = normalize_email(email)
        await self._enforce_send_limits(email, address)
        account = await self._find_by_email(email)
        if account is None or account.email_verified_at is not None:
            self._events.emit("resend_verification", outcome="ignored")
            return None
        return await self._send_challenge(account, "email_verification")

    async def verify_email(self, email: str, code: str, password: str, address: str) -> None:
        """Verify an address with its code AND the account's password.

        Requiring the password stops pre-hijacking: someone who registered another person's
        address with a password of their own cannot be made the owner by that person confirming
        the code that was mailed to them, because the confirmer does not know that password.
        The password is checked before the code is touched, so guessing it burns no attempts,
        and a wrong password looks exactly like a wrong code.
        """
        email = normalize_email(email)
        await self._enforce_attempt_limits(email, address)
        account = await self._find_by_email(email)
        if account is None or account.email_verified_at is not None:
            await self._passwords.verify_unknown_account(password)
            self._spend_equal_effort(code)
            self._events.emit("verify_email", outcome="failed", reason="no_pending_account")
            raise InvalidCode
        credentials = await self._find_by_username(account.username)
        if credentials is None or not await self._passwords.verify(
            credentials.hashed_password, password
        ):
            self._spend_equal_effort(code)
            self._events.emit("verify_email", outcome="failed", reason="password")
            raise InvalidCode
        user_id = account.user_id

        async def verified(tx: AuthTransaction, now: datetime) -> None:
            await tx.mark_email_verified(user_id, now)

        if not await self._redeem(account, "email_verification", code, verified):
            self._events.emit("verify_email", outcome="failed", reason="code")
            raise InvalidCode
        self._events.emit("verify_email", outcome="verified")

    # ------------------------------------------------------------------------ login

    async def login(self, username: str, password: str, address: str) -> TokenPair:
        username = normalize_username(username)
        limits = self._policy.limits
        attempts = (
            (
                self._key("login-pair", f"{username}\x00{address}"),
                limits.login_attempts_per_account_and_address,
            ),
            (self._key("login-address", address), limits.login_attempts_per_address),
        )
        await self._enforce(attempts)  # counted first; a correct password gives the count back

        account = await self._find_by_username(username)
        if account is None:
            await self._passwords.verify_unknown_account(password)
            self._events.emit("login", outcome="failed")
            raise InvalidCredentials
        if not await self._passwords.verify(account.hashed_password, password):
            self._events.emit("login", outcome="failed")
            raise InvalidCredentials
        await self._release(attempts)  # a correct password is not a failed guess
        if account.email_verified_at is None:
            self._events.emit("login", outcome="failed", reason="email_not_verified")
            raise EmailNotVerified

        now = self._clock.now()
        session_id, refresh_jti = uuid4(), uuid4()
        absolute = now + self._policy.refresh_absolute_ttl
        refresh_expires = min(now + self._policy.refresh_ttl, absolute)
        rehash = (
            await self._passwords.hash(password)
            if self._passwords.needs_rehash(account.hashed_password)
            else None
        )
        async with self._store.transaction(account.workspace_id) as tx:
            if rehash is not None:
                await tx.rehash_password(account.user_id, rehash, account.password_changed_at)
            opened = await tx.create_session(
                session_id=session_id,
                workspace_id=account.workspace_id,
                user_id=account.user_id,
                refresh_jti=refresh_jti,
                refresh_expires_at=refresh_expires,
                absolute_expires_at=absolute,
                expected_password_changed_at=account.password_changed_at,
                now=now,
            )
        if not opened:  # the password was reset between the check and now
            self._events.emit("login", outcome="failed", reason="password_changed")
            raise InvalidCredentials
        self._events.emit("login", outcome="succeeded")
        return self._issue_tokens(
            user_id=account.user_id,
            workspace_id=account.workspace_id,
            role=account.role,
            session_id=session_id,
            refresh_jti=refresh_jti,
            generation=0,
            refresh_expires_at=refresh_expires,
        )

    # ----------------------------------------------------------------- password reset

    async def request_password_reset(self, email: str, address: str) -> OutboundEmail | None:
        self._require_email()
        email = normalize_email(email)
        await self._enforce_send_limits(email, address)
        account = await self._find_by_email(email)
        if account is None:
            self._events.emit("password_reset_request", outcome="ignored")
            return None
        return await self._send_challenge(account, "password_reset")

    async def reset_password(self, email: str, code: str, new_password: str, address: str) -> None:
        email = normalize_email(email)
        await self._enforce_attempt_limits(email, address)
        # Checked before the code is touched, so a rejected password never burns an attempt.
        validate_password_policy(new_password, forbidden=(email,))
        new_hash = await self._passwords.hash(new_password)  # same work for every address
        account = await self._find_by_email(email)
        if account is None:
            self._spend_equal_effort(code)
            self._events.emit("password_reset", outcome="failed", reason="no_account")
            raise InvalidCode
        user_id = account.user_id

        async def reset(tx: AuthTransaction, now: datetime) -> None:
            await tx.set_password(user_id, new_hash, now)
            # Proving control of the mailbox also proves the address.
            await tx.mark_email_verified(user_id, now)
            await tx.revoke_user_sessions(user_id, "password_reset", now)
            for purpose in ("email_verification", "password_reset"):
                await tx.invalidate_live_challenges(user_id, purpose, now)

        if not await self._redeem(account, "password_reset", code, reset):
            self._events.emit("password_reset", outcome="failed", reason="code")
            raise InvalidCode
        self._events.emit("password_reset", outcome="succeeded")

    # -------------------------------------------------------------- sessions and tokens

    async def refresh(self, refresh_token: str) -> TokenPair:
        try:
            claims = self._tokens.decode_refresh(refresh_token)
        except TokenError:
            raise InvalidToken from None
        now = self._clock.now()
        new_jti = uuid4()
        replayed = False
        role: str | None = None
        async with self._store.transaction(claims.workspace_id) as tx:
            rotated = await tx.rotate_refresh(
                session_id=claims.session_id,
                user_id=claims.user_id,
                current_jti=claims.token_id,
                new_jti=new_jti,
                sliding_expires_at=now + self._policy.refresh_ttl,
                now=now,
            )
            if rotated is None:
                # A token from an earlier generation than the session's current one has already
                # been exchanged: someone holds a copy. Revoke the whole session.
                current = await tx.refresh_generation_of(claims.session_id, claims.user_id)
                if current is not None and claims.generation < current:
                    replayed = True
                    await tx.revoke_session(
                        claims.session_id, claims.user_id, "reuse_detected", now
                    )
            else:
                session = await tx.get_live_session(claims.session_id, claims.user_id, now)
                role = None if session is None else session.role
        if rotated is None or role is None:
            self._events.emit(
                "refresh", outcome="failed", reason="replayed" if replayed else "not_current"
            )
            raise InvalidToken
        self._events.emit("refresh", outcome="rotated")
        return self._issue_tokens(
            user_id=claims.user_id,
            workspace_id=claims.workspace_id,
            role=role,
            session_id=claims.session_id,
            refresh_jti=new_jti,
            generation=rotated.generation,
            refresh_expires_at=rotated.refresh_expires_at,
        )

    async def logout(self, refresh_token: str) -> None:
        """Revoke the session of a refresh token. Idempotent; an expired token still works."""
        try:
            claims = self._tokens.decode_refresh(refresh_token, allow_expired=True)
        except TokenError:
            raise InvalidToken from None
        async with self._store.transaction(claims.workspace_id) as tx:
            await tx.revoke_session(claims.session_id, claims.user_id, "logout", self._clock.now())
        self._events.emit("logout", outcome="succeeded")

    async def authenticate(self, access_token: str) -> Principal:
        """Verify an access token and that its session is still live (not revoked or expired)."""
        try:
            claims = self._tokens.decode_access(access_token)
        except TokenError:
            raise InvalidToken from None
        async with self._store.transaction(claims.workspace_id) as tx:
            session = await tx.get_live_session(
                claims.session_id, claims.user_id, self._clock.now()
            )
        if session is None or session.workspace_id != claims.workspace_id:
            raise InvalidToken
        return Principal(
            user_id=session.user_id,
            workspace_id=session.workspace_id,
            role=session.role,
            session_id=session.id,
        )

    # ------------------------------------------------------------------------ delivery

    async def deliver(self, message: OutboundEmail) -> None:
        """Send a message after the response (background task). Failures are not user-visible."""
        if self._email_sender is None:
            return
        try:
            await self._email_sender.send(message)
        except EmailDeliveryError as error:
            self._events.emit("email_delivery", outcome="failed", reason=error.category)
        except Exception:
            self._events.emit("email_delivery", outcome="failed")
        else:
            self._events.emit("email_delivery", outcome="sent")

    async def warm_up(self) -> None:
        """Pay the one-time cost of the placeholder hash at startup, not on a first request."""
        await self._passwords.verify_unknown_account("warm-up")

    # ---------------------------------------------------------------------- internals

    def _require_email(self) -> None:
        if self._email_sender is None:
            raise ServiceUnavailable

    def _key(self, label: str, value: str) -> str:
        return f"{label}:{identifier_digest(self._otp_key, label, value)}"

    async def _find_by_email(self, email: str) -> AccountIdentity | None:
        async with self._store.transaction(None) as tx:
            return await tx.find_account_by_email(email)

    async def _find_by_username(self, username: str) -> AccountRecord | None:
        async with self._store.transaction(None) as tx:
            return await tx.find_account_by_username(username)

    async def _create_challenge(
        self,
        tx: AuthTransaction,
        workspace_id: UUID,
        user_id: UUID,
        purpose: OtpPurpose,
        now: datetime,
    ) -> str:
        await tx.invalidate_live_challenges(user_id, purpose, now)
        challenge_id = uuid4()
        code = generate_code()
        await tx.create_challenge(
            challenge_id=challenge_id,
            workspace_id=workspace_id,
            user_id=user_id,
            purpose=purpose,
            code_hmac=code_mac(self._otp_key, purpose, challenge_id, code),
            expires_at=now + OTP_LIFETIME,
            now=now,
        )
        return code

    async def _send_challenge(
        self, account: AccountIdentity, purpose: OtpPurpose
    ) -> OutboundEmail | None:
        """Issue a fresh code unless one was issued within the cooldown."""
        now = self._clock.now()
        try:
            async with self._store.transaction(account.workspace_id) as tx:
                latest = await tx.latest_challenge_created_at(account.user_id, purpose)
                if latest is not None and now < latest + self._policy.resend_cooldown:
                    self._events.emit("code_issue", outcome="cooldown", reason=purpose)
                    return None
                code = await self._create_challenge(
                    tx, account.workspace_id, account.user_id, purpose, now
                )
        except ChallengeConflict:
            self._events.emit("code_issue", outcome="cooldown", reason=purpose)
            return None
        self._events.emit("code_issue", outcome="issued", reason=purpose)
        return code_email(account.email, purpose, code, username=account.username)

    async def _redeem(
        self, account: AccountIdentity, purpose: OtpPurpose, code: str, on_success: OnSuccess
    ) -> bool:
        """Count an attempt, check the code and, if right, consume it and apply the effect.

        Returns instead of raising so the attempt counter is committed even when the code is
        wrong. Concurrent right codes: only the caller whose ``consume_challenge`` wins succeeds.
        """
        now = self._clock.now()
        async with self._store.transaction(account.workspace_id) as tx:
            challenge = await tx.record_challenge_attempt(
                account.user_id, purpose, now, OTP_MAX_ATTEMPTS
            )
            if challenge is None:
                return False
            if not code_matches(self._otp_key, purpose, challenge.id, code, challenge.code_hmac):
                return False
            if not await tx.consume_challenge(challenge.id, now):
                return False
            await on_success(tx, now)
            return True

    def _spend_equal_effort(self, code: str) -> None:
        code_matches(self._otp_key, "email_verification", UUID(int=0), code, _DUMMY_MAC)

    async def _enforce_send_limits(self, email: str, address: str) -> None:
        limits = self._policy.limits
        await self._enforce(
            (
                (self._key("send-email", email), limits.send_per_email),
                (self._key("send-address", address), limits.send_per_address),
            )
        )

    async def _enforce_attempt_limits(self, email: str, address: str) -> None:
        limits = self._policy.limits
        await self._enforce(
            (
                (self._key("attempt-email", email), limits.attempt_per_email),
                (self._key("attempt-email-daily", email), limits.attempt_per_email_daily),
                (self._key("attempt-address", address), limits.attempt_per_address),
            )
        )

    async def _enforce(self, checks: Sequence[tuple[str, Limit]]) -> None:
        exceeded: Limit | None = None
        for key, limit in checks:  # every counter is updated even when an earlier one tripped
            if not await self._guard(self._limiter.hit(key, limit)) and exceeded is None:
                exceeded = limit
        if exceeded is not None:
            self._events.emit("rate_limit", outcome="blocked")
            raise self._rate_limited(exceeded)

    async def _release(self, checks: Sequence[tuple[str, Limit]]) -> None:
        """Give counts back. A failure here only costs the user an attempt, so it is ignored."""
        for key, limit in checks:
            try:
                await self._limiter.release(key, limit)
            except RateLimiterUnavailable:
                continue

    async def _guard(self, operation: Awaitable[bool]) -> bool:
        try:
            return await operation
        except RateLimiterUnavailable:
            raise ServiceUnavailable from None

    def _rate_limited(self, limit: Limit) -> RateLimited:
        elapsed = int(self._clock.now().timestamp()) % limit.window_seconds
        return RateLimited(retry_after=limit.window_seconds - elapsed)

    def _issue_tokens(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        role: str,
        session_id: UUID,
        refresh_jti: UUID,
        generation: int,
        refresh_expires_at: datetime,
    ) -> TokenPair:
        access, _ = self._tokens.issue_access(
            user_id=user_id,
            workspace_id=workspace_id,
            role=role,
            session_id=session_id,
            token_id=uuid4(),
        )
        refresh = self._tokens.issue_refresh(
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=session_id,
            token_id=refresh_jti,
            generation=generation,
            expires_at=refresh_expires_at,
        )
        return TokenPair(
            access_token=access,
            refresh_token=refresh,
            expires_in=int(self._tokens.access_ttl.total_seconds()),
        )
