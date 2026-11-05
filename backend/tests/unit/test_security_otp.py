from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.core.security_otp import (
    OTP_DIGITS,
    OTP_LIFETIME,
    OTP_MAX_ATTEMPTS,
    code_mac,
    code_matches,
    generate_code,
    identifier_digest,
)

KEY = b"k" * 32
OTHER_KEY = b"o" * 32
CHALLENGE = UUID("11111111-1111-1111-1111-111111111111")


def test_owner_approved_constants() -> None:
    assert OTP_DIGITS == 6
    assert OTP_MAX_ATTEMPTS == 5
    assert OTP_LIFETIME.total_seconds() == 600


def test_codes_are_six_digits_and_keep_leading_zeros() -> None:
    assert generate_code(lambda _: 7) == "000007"
    assert generate_code(lambda _: 0) == "000000"
    assert generate_code(lambda _: 999_999) == "999999"
    for _ in range(50):
        code = generate_code()
        assert len(code) == 6
        assert code.isdecimal()


def test_generation_asks_for_the_full_six_digit_space() -> None:
    asked: list[int] = []

    def capture(bound: int) -> int:
        asked.append(bound)
        return 1

    generate_code(capture)
    assert asked == [1_000_000]


def test_mac_is_not_the_code_and_is_deterministic() -> None:
    mac = code_mac(KEY, "email_verification", CHALLENGE, "123456")
    assert b"123456" not in mac
    assert len(mac) == 32
    assert mac == code_mac(KEY, "email_verification", CHALLENGE, "123456")


def _mac(
    key: bytes = KEY,
    purpose: str = "email_verification",
    challenge: UUID = CHALLENGE,
    code: str = "123456",
) -> bytes:
    return code_mac(key, purpose, challenge, code)


@pytest.mark.parametrize(
    "variant",
    [
        _mac(key=OTHER_KEY),
        _mac(purpose="password_reset"),
        _mac(challenge=uuid4()),
        _mac(code="123457"),
    ],
)
def test_mac_changes_with_key_purpose_challenge_and_code(variant: bytes) -> None:
    assert variant != _mac()


def test_matching_accepts_only_the_right_code_purpose_and_challenge() -> None:
    stored = code_mac(KEY, "password_reset", CHALLENGE, "004211")
    assert code_matches(KEY, "password_reset", CHALLENGE, "004211", stored)
    assert not code_matches(KEY, "password_reset", CHALLENGE, "004212", stored)
    assert not code_matches(KEY, "email_verification", CHALLENGE, "004211", stored)
    assert not code_matches(KEY, "password_reset", uuid4(), "004211", stored)
    assert not code_matches(OTHER_KEY, "password_reset", CHALLENGE, "004211", stored)


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "12 456", "abcdef", "١٢٣٤٥٦", " 123456"])
def test_malformed_codes_never_match(bad: str) -> None:
    stored = code_mac(KEY, "password_reset", CHALLENGE, "123456")
    assert not code_matches(KEY, "password_reset", CHALLENGE, bad, stored)


def test_comparison_is_constant_time() -> None:
    stored = code_mac(KEY, "password_reset", CHALLENGE, "123456")
    with patch("app.core.security_otp.hmac.compare_digest", return_value=True) as spy:
        assert code_matches(KEY, "password_reset", CHALLENGE, "123456", stored)
    spy.assert_called_once()


def test_identifier_digest_hides_the_value_and_depends_on_key_and_label() -> None:
    digest = identifier_digest(KEY, "email", "alice@example.com")
    assert "alice" not in digest
    assert digest == identifier_digest(KEY, "email", "alice@example.com")
    assert digest != identifier_digest(OTHER_KEY, "email", "alice@example.com")
    assert digest != identifier_digest(KEY, "address", "alice@example.com")
    assert digest != identifier_digest(KEY, "email", "bob@example.com")
