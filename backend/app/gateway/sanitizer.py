"""Input sanitization and validation for Model Gateway requests."""

import re
from typing import Any

from app.gateway.exceptions import InputSanitizationError

# Strip ASCII control characters except \t (0x09), \n (0x0A), and \r (0x0D)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str, max_chars: int | None = None) -> str:
    """Validate and sanitize a string input.

    - Verifies text is valid UTF-8 and contains no lone surrogates.
    - Strips non-printable ASCII control characters while preserving \\n, \\r, and \\t.
    - Enforces max_chars limit if specified.

    Raises:
        InputSanitizationError: If text contains lone surrogates or exceeds max_chars.
    """
    if not isinstance(text, str):
        raise InputSanitizationError(f"Expected string input, got {type(text).__name__}")

    # Validate UTF-8 encodability (reject lone surrogates)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InputSanitizationError(
            f"Input contains invalid UTF-8 or lone surrogate characters: {exc}"
        ) from exc

    # Enforce length limit if configured
    if max_chars is not None and len(text) > max_chars:
        raise InputSanitizationError(
            f"Input text length ({len(text)} chars) exceeds maximum limit of {max_chars} chars"
        )

    # Strip control characters
    return _CONTROL_CHARS_RE.sub("", text)


def sanitize_content_item(item: Any, max_chars: int | None = None) -> Any:
    """Recursively sanitize a single content item or structured payload."""
    if isinstance(item, str):
        return sanitize_text(item, max_chars=max_chars)
    if isinstance(item, list):
        return [sanitize_content_item(x, max_chars=max_chars) for x in item]
    if isinstance(item, dict):
        return {
            (sanitize_text(k, max_chars=max_chars) if isinstance(k, str) else k): (
                sanitize_content_item(v, max_chars=max_chars)
            )
            for k, v in item.items()
        }
    return item


def sanitize_contents(contents: list[Any], max_chars: int | None = None) -> list[Any]:
    """Sanitize all items in a contents list for gateway operations."""
    return [sanitize_content_item(c, max_chars=max_chars) for c in contents]
