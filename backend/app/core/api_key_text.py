"""Normalise an API key as written in an env file (no dependencies, importable anywhere)."""

from __future__ import annotations


def clean_api_key(raw: str) -> str:
    """One API key as written in an env file: whitespace, matching quotes and stray ``,;`` removed.

    ``"k1","k2"``, ``'k1'``, ``k1,``, a trailing newline or a CRLF all name the same key, so the
    pool never counts one key twice. Only the outer decoration is removed; the key text is not
    otherwise changed.
    """
    value = raw.strip()
    while value:
        trimmed = value.strip().strip(",;").strip()
        if len(trimmed) >= 2 and trimmed[0] == trimmed[-1] and trimmed[0] in "\"'`":
            trimmed = trimmed[1:-1]
        if trimmed == value:
            break
        value = trimmed
    return value
