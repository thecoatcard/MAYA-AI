"""RFC 6238 TOTP Service for Step-Up Authentication.

Includes Replay & Brute-Force Guards (Decisions D4, D5).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import struct
import time
import urllib.parse
from typing import Final
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

DEFAULT_TIMESTEP_SECONDS: Final[int] = 30
DEFAULT_DIGITS: Final[int] = 6
DEFAULT_SKEW_STEPS: Final[int] = 1
MAX_CONSECUTIVE_FAILURES: Final[int] = 5
LOCKOUT_DURATION_SECONDS: Final[int] = 900  # 15 minutes


class InvalidTOTPError(Exception):
    """Raised when a TOTP code is invalid or expired."""

    def __init__(self, message: str = "Invalid or expired step-up authentication code") -> None:
        super().__init__(message)


class TOTPLockedOutError(InvalidTOTPError):
    """Raised when an account is temporarily locked out due to excessive failed attempts."""

    def __init__(self, retry_after_seconds: int = LOCKOUT_DURATION_SECONDS) -> None:
        super().__init__(
            f"Account temporarily locked out due to multiple failed verification attempts. "
            f"Please try again in {retry_after_seconds // 60} minutes."
        )
        self.retry_after_seconds = retry_after_seconds


class TOTPReplayError(InvalidTOTPError):
    """Raised when a TOTP code has already been consumed within the valid time-step window."""

    def __init__(self) -> None:
        super().__init__("Authentication code has already been used and cannot be replayed")


def generate_totp_secret(num_bytes: int = 20) -> str:
    """Generate a high-entropy Base32 secret string (RFC 6238 recommends at least 160 bits)."""
    random_bytes = secrets.token_bytes(num_bytes)
    return base64.b32encode(random_bytes).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    """Decode a Base32 secret, handling padding automatically."""
    normalized = secret.strip().replace(" ", "").upper()
    missing_padding = len(normalized) % 8
    if missing_padding:
        normalized += "=" * (8 - missing_padding)
    try:
        return base64.b32decode(normalized, casefold=True)
    except Exception as err:
        raise ValueError(f"Invalid Base32 TOTP secret: {err}") from err


def compute_totp(
    secret: str | bytes,
    timestamp: float | None = None,
    timestep_seconds: int = DEFAULT_TIMESTEP_SECONDS,
    digits: int = DEFAULT_DIGITS,
) -> str:
    """Compute standard RFC 6238 TOTP code for a given timestamp."""
    secret_bytes = _decode_secret(secret) if isinstance(secret, str) else secret
    current_time = timestamp if timestamp is not None else time.time()
    counter = int(current_time // timestep_seconds)

    # 8-byte big-endian counter
    counter_bytes = struct.pack(">Q", counter)

    # HMAC-SHA1
    hmac_digest = hmac.new(secret_bytes, counter_bytes, hashlib.sha1).digest()

    # Dynamic truncation (RFC 4226 §5.3)
    offset = hmac_digest[-1] & 0x0F
    code_int = struct.unpack(">I", hmac_digest[offset : offset + 4])[0] & 0x7FFFFFFF
    code = code_int % (10**digits)

    return f"{code:0{digits}d}"


def verify_totp(
    code: str,
    secret: str | bytes,
    timestamp: float | None = None,
    timestep_seconds: int = DEFAULT_TIMESTEP_SECONDS,
    skew_steps: int = DEFAULT_SKEW_STEPS,
    digits: int = DEFAULT_DIGITS,
) -> bool:
    """Verify a TOTP code within an allowed window of time steps (clock skew).

    Uses constant-time comparison to prevent timing side channels.
    """
    valid, _ = verify_totp_with_step(
        code,
        secret,
        timestamp=timestamp,
        timestep_seconds=timestep_seconds,
        skew_steps=skew_steps,
        digits=digits,
    )
    return valid


def verify_totp_with_step(
    code: str,
    secret: str | bytes,
    timestamp: float | None = None,
    timestep_seconds: int = DEFAULT_TIMESTEP_SECONDS,
    skew_steps: int = DEFAULT_SKEW_STEPS,
    digits: int = DEFAULT_DIGITS,
) -> tuple[bool, int | None]:
    """Verify a TOTP code and return (is_valid, matched_step_counter)."""
    clean_code = code.strip().replace(" ", "")
    if len(clean_code) != digits or not clean_code.isdigit():
        return False, None

    current_time = timestamp if timestamp is not None else time.time()
    base_counter = int(current_time // timestep_seconds)
    secret_bytes = _decode_secret(secret) if isinstance(secret, str) else secret

    # Check intervals [counter - skew_steps, counter + skew_steps]
    for step_offset in range(-skew_steps, skew_steps + 1):
        step_counter = base_counter + step_offset
        test_time = step_counter * timestep_seconds
        expected_code = compute_totp(
            secret_bytes,
            timestamp=test_time,
            timestep_seconds=timestep_seconds,
            digits=digits,
        )
        if hmac.compare_digest(clean_code, expected_code):
            return True, step_counter

    return False, None


class InMemoryTOTPReplayGuard:
    """In-memory fallback for tracking consumed TOTP time-steps per user."""

    def __init__(self) -> None:
        self._consumed: dict[tuple[str, int], float] = {}

    def mark_or_check_replay(self, user_id: str | UUID, step: int, ttl_seconds: int = 90) -> bool:
        """Return True if step was successfully recorded, False if already consumed (replay)."""
        now = time.time()
        # Clean expired
        expired = [k for k, exp in self._consumed.items() if exp <= now]
        for k in expired:
            self._consumed.pop(k, None)

        key = (str(user_id), step)
        if key in self._consumed:
            return False
        self._consumed[key] = now + ttl_seconds
        return True


class InMemoryTOTPRateLimiter:
    """In-memory rate limiter tracking consecutive verification failures and temporary lockout."""

    def __init__(self) -> None:
        self._failures: dict[str, int] = {}
        self._lockouts: dict[str, float] = {}

    def check_lockout(self, user_id: str | UUID) -> None:
        uid = str(user_id)
        now = time.time()
        lockout_expiry = self._lockouts.get(uid)
        if lockout_expiry is not None:
            if lockout_expiry > now:
                remaining = int(lockout_expiry - now)
                raise TOTPLockedOutError(retry_after_seconds=remaining)
            del self._lockouts[uid]

    def record_failure(self, user_id: str | UUID) -> None:
        uid = str(user_id)
        count = self._failures.get(uid, 0) + 1
        self._failures[uid] = count
        if count >= MAX_CONSECUTIVE_FAILURES:
            self._lockouts[uid] = time.time() + LOCKOUT_DURATION_SECONDS
            self._failures.pop(uid, None)

    def record_success(self, user_id: str | UUID) -> None:
        uid = str(user_id)
        self._failures.pop(uid, None)
        self._lockouts.pop(uid, None)


# Default process-level instances for local/in-memory usage
DEFAULT_REPLAY_GUARD = InMemoryTOTPReplayGuard()
DEFAULT_RATE_LIMITER = InMemoryTOTPRateLimiter()


def verify_totp_hardened(
    code: str,
    secret: str | bytes,
    *,
    user_id: str | UUID,
    replay_guard: InMemoryTOTPReplayGuard | None = None,
    rate_limiter: InMemoryTOTPRateLimiter | None = None,
    timestamp: float | None = None,
    timestep_seconds: int = DEFAULT_TIMESTEP_SECONDS,
    skew_steps: int = DEFAULT_SKEW_STEPS,
) -> bool:
    """Harden TOTP verification with progressive lockout and anti-replay guards."""
    guard = replay_guard or DEFAULT_REPLAY_GUARD
    limiter = rate_limiter or DEFAULT_RATE_LIMITER

    # 1. Enforce lockout check
    limiter.check_lockout(user_id)

    # 2. Check code against current and skew steps
    valid, matched_step = verify_totp_with_step(
        code,
        secret,
        timestamp=timestamp,
        timestep_seconds=timestep_seconds,
        skew_steps=skew_steps,
    )

    if not valid or matched_step is None:
        limiter.record_failure(user_id)
        _audit_log_totp(user_id, success=False, reason="invalid_code")
        return False

    # 3. Check anti-replay guard
    is_fresh = guard.mark_or_check_replay(user_id, matched_step)
    if not is_fresh:
        limiter.record_failure(user_id)
        _audit_log_totp(user_id, success=False, reason="replay_detected")
        raise TOTPReplayError()

    # 4. Success resets failure counters
    limiter.record_success(user_id)
    _audit_log_totp(user_id, success=True)
    return True


def _audit_log_totp(user_id: str | UUID, *, success: bool, reason: str | None = None) -> None:
    """Record structured security audit log for step-up auth with zero credential leakage."""
    logger.info(
        "TOTP verification attempt: user_id=%s, success=%s, reason=%s",
        user_id,
        success,
        reason or "verified",
    )


def encrypt_totp_secret(secret: str, master_key: bytes | str) -> str:
    """Encrypt a TOTP secret using AES-256-GCM authenticated encryption.

    Returns base64 encoded format: iv(12 bytes) + ciphertext + tag(16 bytes).
    """
    key_bytes = master_key.encode("utf-8") if isinstance(master_key, str) else master_key
    # Ensure 32-byte key
    derived_key = hashlib.sha256(key_bytes).digest()
    aesgcm = AESGCM(derived_key)
    iv = secrets.token_bytes(12)
    ciphertext = aesgcm.encrypt(iv, secret.encode("utf-8"), None)
    return base64.b64encode(iv + ciphertext).decode("ascii")


def decrypt_totp_secret(encrypted_data: str, master_key: bytes | str) -> str:
    """Decrypt a TOTP secret previously encrypted with encrypt_totp_secret."""
    key_bytes = master_key.encode("utf-8") if isinstance(master_key, str) else master_key
    derived_key = hashlib.sha256(key_bytes).digest()
    raw = base64.b64decode(encrypted_data)
    iv = raw[:12]
    ciphertext = raw[12:]
    aesgcm = AESGCM(derived_key)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)
    return plaintext.decode("utf-8")


def build_provisioning_uri(
    secret: str,
    label: str,
    issuer: str = "Maya AI",
) -> str:
    """Build an otpauth:// URL suitable for QR code scanning in authenticator apps."""
    clean_secret = secret.strip().replace(" ", "").upper()
    quoted_issuer = urllib.parse.quote(issuer)
    quoted_label = urllib.parse.quote(label, safe="@")
    return (
        f"otpauth://totp/{quoted_issuer}:{quoted_label}"
        f"?secret={clean_secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    )
