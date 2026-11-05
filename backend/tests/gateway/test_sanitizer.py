"""Unit tests for Model Gateway input sanitization."""

import pytest

from app.gateway.exceptions import InputSanitizationError
from app.gateway.sanitizer import (
    sanitize_content_item,
    sanitize_contents,
    sanitize_text,
)


def test_sanitize_valid_text() -> None:
    text = "Hello, world! 123 😊"
    assert sanitize_text(text) == text


def test_control_characters_stripped_but_whitespace_preserved() -> None:
    # \t (9), \n (10), \r (13) must be preserved; \x00, \x08, \x1b (27), \x7f (127) must be stripped
    text = "Hello\x00\x08World!\tLine1\nLine2\r\nEscape:\x1b[31mDone\x7f"
    sanitized = sanitize_text(text)
    assert sanitized == "HelloWorld!\tLine1\nLine2\r\nEscape:[31mDone"
    assert "\x00" not in sanitized
    assert "\x08" not in sanitized
    assert "\x1b" not in sanitized
    assert "\x7f" not in sanitized
    assert "\t" in sanitized
    assert "\n" in sanitized
    assert "\r" in sanitized


def test_lone_surrogate_raises_input_sanitization_error() -> None:
    # \ud800 is a high surrogate in the UTF-16 surrogate range (U+D800 to U+DFFF)
    lone_surrogate = "Invalid\ud800Surrogate"
    with pytest.raises(InputSanitizationError, match="lone surrogate"):
        sanitize_text(lone_surrogate)


def test_max_chars_limit_enforced() -> None:
    text = "12345"
    assert sanitize_text(text, max_chars=5) == "12345"

    with pytest.raises(InputSanitizationError, match="exceeds maximum limit of 4 chars"):
        sanitize_text(text, max_chars=4)


def test_non_string_input_raises_error() -> None:
    with pytest.raises(InputSanitizationError, match="Expected string input"):
        sanitize_text(123)  # type: ignore[arg-type]


def test_sanitize_content_item_recursive() -> None:
    payload = {
        "text": "Hello\x00World",
        "nested": {
            "prompt": "Line1\nLine2\x07",
            "numbers": [1, 2, "item\x08"],
        },
    }
    cleaned = sanitize_content_item(payload)
    assert cleaned == {
        "text": "HelloWorld",
        "nested": {
            "prompt": "Line1\nLine2",
            "numbers": [1, 2, "item"],
        },
    }


def test_sanitize_contents_list() -> None:
    contents = [
        "First item\x00",
        {"role": "user", "text": "Hello\x08"},
        123,
    ]
    cleaned = sanitize_contents(contents)
    assert cleaned == [
        "First item",
        {"role": "user", "text": "Hello"},
        123,
    ]
