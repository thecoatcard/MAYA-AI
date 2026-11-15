import pytest

from app.auth.identifiers import normalize_display_text, normalize_email, normalize_username


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alice.Example@Example.COM", "alice.example@example.com"),
        ("  bob+tag@sub.example.org ", "bob+tag@sub.example.org"),
        ("a@b.co", "a@b.co"),
        ("a@example.com\n", "a@example.com"),  # surrounding whitespace is trimmed, not kept
    ],
)
def test_valid_addresses_are_lower_cased_and_trimmed(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "plain",
        "@example.com",
        "a@",
        "a@b",
        "a@b.c",
        "a b@example.com",
        "a@exa mple.com",
        'a"b@example.com',
        "a@example.com\r\nBcc: victim@example.com",
        "ünï@example.com",
        "a@exämple.com",
        "a@-example.com",
        "a@example..com",
        ("x" * 65) + "@example.com",
        "a@" + ("x" * 250) + ".com",
        "a@example.com, b@example.com",
        "<a@example.com>",
    ],
)
def test_malformed_or_dangerous_addresses_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_email(raw)


@pytest.mark.parametrize(
    ("raw", "expected"), [("Alice", "alice"), (" bob_1 ", "bob_1"), ("a.b-c", "a.b-c")]
)
def test_usernames_are_trimmed_and_lower_cased(raw: str, expected: str) -> None:
    assert normalize_username(raw) == expected


@pytest.mark.parametrize("raw", ["", "ab", "x" * 33, "has space", "a@b", "ünï", "a\nb", "名前です"])
def test_invalid_usernames_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_username(raw)


def test_display_text_is_trimmed_and_may_be_unicode() -> None:
    assert normalize_display_text("  Zoë Müller 名前 ") == "Zoë Müller 名前"


@pytest.mark.parametrize("raw", ["", "   ", "x" * 256, "bell\x07", "line\nbreak", "nul\x00", "​"])
def test_display_text_rejects_empty_oversized_and_control_characters(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_display_text(raw)
