"""One-time codes: generation and keyed MAC storage format. Plaintext codes are never stored."""

import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from datetime import timedelta
from typing import Literal
from uuid import UUID

OTP_DIGITS = 6
OTP_LIFETIME = timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5

OtpPurpose = Literal["email_verification", "password_reset"]
_CODE_FORMAT = re.compile(r"[0-9]{6}")


def generate_code(randbelow: Callable[[int], int] = secrets.randbelow) -> str:
    return f"{randbelow(10**OTP_DIGITS):0{OTP_DIGITS}d}"


def code_mac(key: bytes, purpose: str, challenge_id: UUID, code: str) -> bytes:
    """Bind the MAC to purpose and challenge so a code cannot be replayed elsewhere."""
    message = b"\x00".join((b"maya-otp-v1", purpose.encode(), challenge_id.bytes, code.encode()))
    return hmac.new(key, message, hashlib.sha256).digest()


def code_matches(key: bytes, purpose: str, challenge_id: UUID, code: str, expected: bytes) -> bool:
    if _CODE_FORMAT.fullmatch(code) is None:
        return False
    return hmac.compare_digest(code_mac(key, purpose, challenge_id, code), expected)


def identifier_digest(key: bytes, label: str, value: str) -> str:
    """Keyed digest used for rate-limit keys, so raw e-mail addresses never reach Redis."""
    message = b"\x00".join((b"maya-identifier-v1", label.encode(), value.encode()))
    return hmac.new(key, message, hashlib.sha256).hexdigest()
