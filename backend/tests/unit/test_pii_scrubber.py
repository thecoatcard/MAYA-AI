"""Unit tests for deterministic PII and secret scrubber."""

from app.memory.scrubber import PiiScrubber, verify_luhn


def test_verify_luhn_valid_cards() -> None:
    # Valid Visa
    assert verify_luhn("4532015112830366") is True
    # Valid MasterCard
    assert verify_luhn("5424180123456789") is True
    # Valid Amex (15 digits)
    assert verify_luhn("378282246310005") is True
    # Valid with hyphens / spaces
    assert verify_luhn("4532-0151-1283-0366") is True
    assert verify_luhn("5424 1801 2345 6789") is True


def test_verify_luhn_invalid_cards() -> None:
    # Invalid checksum
    assert verify_luhn("4532015112830367") is False
    assert verify_luhn("1234567890123456") is False
    # Too short
    assert verify_luhn("123456789") is False
    # Non-digit
    assert verify_luhn("abcd-efgh-ijkl") is False


def test_scrub_credit_cards() -> None:
    scrubber = PiiScrubber()
    text = "Please charge my Visa 4532-0151-1283-0366 or my Amex 378282246310005 for renewal."
    result = scrubber.scrub(text)

    assert "[REDACTED_CREDIT_CARD]" in result.clean_text
    assert "4532-0151-1283-0366" not in result.clean_text
    assert "378282246310005" not in result.clean_text
    assert result.redactions_count == 2
    assert "credit_card" in result.detected_types


def test_scrub_ssn() -> None:
    scrubber = PiiScrubber()
    text = "Employee John Doe with SSN 123-45-6789 reported for orientation."
    result = scrubber.scrub(text)

    assert "[REDACTED_SSN]" in result.clean_text
    assert "123-45-6789" not in result.clean_text
    assert result.redactions_count == 1
    assert "ssn" in result.detected_types


def test_scrub_aws_keys() -> None:
    scrubber = PiiScrubber()
    text = "Deploy using AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE for credentials."
    result = scrubber.scrub(text)

    assert "[REDACTED_SECRET]" in result.clean_text
    assert "AKIAIOSFODNN7EXAMPLE" not in result.clean_text
    assert result.redactions_count == 1
    assert "secret" in result.detected_types


def test_scrub_github_tokens() -> None:
    scrubber = PiiScrubber()
    token = "ghp_1234567890abcdefghijklmnopqrstuvwxyzAB"  # secret-scan:allow
    text = f"Clone with token {token}."  # secret-scan:allow
    result = scrubber.scrub(text)

    assert "[REDACTED_SECRET]" in result.clean_text
    assert token not in result.clean_text
    assert result.redactions_count == 1
    assert "secret" in result.detected_types


def test_scrub_bearer_and_jwt_tokens() -> None:
    scrubber = PiiScrubber()
    # Dummy JWT structure: header.payload.signature
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    text = f"Authorization: Bearer {jwt}"
    result = scrubber.scrub(text)

    assert "[REDACTED_SECRET]" in result.clean_text
    assert jwt not in result.clean_text
    assert result.redactions_count >= 1
    assert "secret" in result.detected_types


def test_scrub_pem_private_keys() -> None:
    scrubber = PiiScrubber()
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"  # secret-scan:allow
        "MIIEowIBAAKCAQEA04sV2w6K...dummy...dummy\n"
        "-----END RSA PRIVATE KEY-----"
    )
    text = f"Server certificate key:\n{pem}\nKeep it secure."
    result = scrubber.scrub(text)

    assert "[REDACTED_SECRET]" in result.clean_text
    assert "-----BEGIN RSA PRIVATE KEY-----" not in result.clean_text  # secret-scan:allow
    assert result.redactions_count == 1
    assert "secret" in result.detected_types


def test_preserve_clean_conversational_text() -> None:
    scrubber = PiiScrubber()
    clean_sample = (
        "Today is 2026-11-07. The server load is 42% on port 8080.\n"
        "Meeting scheduled at 14:00 with team-lead. "
        "Reference ID is TASK-987654321."
    )
    result = scrubber.scrub(clean_sample)

    assert result.clean_text == clean_sample
    assert result.redactions_count == 0
    assert len(result.detected_types) == 0


def test_does_not_redact_invalid_credit_card_numbers() -> None:
    scrubber = PiiScrubber()
    # 16 digits that fail Luhn checksum:
    invalid_cc = "1234-5678-9012-3456"
    text = f"Random serial sequence: {invalid_cc} in database."
    result = scrubber.scrub(text)

    assert result.clean_text == text
    assert result.redactions_count == 0
