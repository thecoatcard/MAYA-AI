"""Normalization of user-supplied identifiers. Everything is compared in normalized form."""

import re
import unicodedata

from app.core.security import USERNAME_PATTERN

EMAIL_MAX_LENGTH = 254
FULL_NAME_MAX_LENGTH = 255
_EMAIL = re.compile(
    r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
)


def normalize_username(value: str) -> str:
    username = value.strip().lower()
    if USERNAME_PATTERN.fullmatch(username) is None:
        raise ValueError("invalid_username")
    return username


def normalize_email(value: str) -> str:
    """Lower-case ASCII address. Deliverability is proved by the one-time code, not by syntax."""
    email = value.strip().lower()
    if len(email) > EMAIL_MAX_LENGTH or _EMAIL.fullmatch(email) is None:
        raise ValueError("invalid_email")
    return email


def normalize_display_text(value: str) -> str:
    text = value.strip()
    if not text or len(text) > FULL_NAME_MAX_LENGTH:
        raise ValueError("invalid_text")
    if any(unicodedata.category(character).startswith("C") for character in text):
        raise ValueError("invalid_text")
    return text
