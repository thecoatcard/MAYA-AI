"""Unit tests for RFC 6238 TOTP computation, verification, and RFC test vectors."""

from app.core.totp import (
    build_provisioning_uri,
    compute_totp,
    generate_totp_secret,
    verify_totp,
)

# RFC 6238 Appendix B: Test vector secret (20 bytes: b"12345678901234567890")
# Base32 representation of ASCII '12345678901234567890' is 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
RFC_SECRET_ASCII = b"12345678901234567890"
RFC_SECRET_BASE32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_rfc_6238_test_vectors() -> None:
    """Verify against official RFC 6238 Appendix B test vectors (SHA-1, 8-digit or 6-digit)."""
    # Test timestamps and their corresponding 6-digit codes
    test_cases = [
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
    ]

    for t, expected in test_cases:
        code_bytes = compute_totp(RFC_SECRET_ASCII, timestamp=float(t))
        code_b32 = compute_totp(RFC_SECRET_BASE32, timestamp=float(t))
        assert code_bytes == expected, f"Failed at t={t}"
        assert code_b32 == expected, f"Failed at t={t} for Base32"


def test_secret_generation() -> None:
    secret = generate_totp_secret()
    assert isinstance(secret, str)
    assert len(secret) >= 32
    # Verify generated secret computes valid 6-digit TOTP
    code = compute_totp(secret)
    assert len(code) == 6
    assert code.isdigit()


def test_verification_valid_window() -> None:
    secret = generate_totp_secret()
    t = 1000.0

    current_code = compute_totp(secret, timestamp=t)
    assert verify_totp(current_code, secret, timestamp=t) is True

    # Code from 25 seconds ago (within current or immediately preceding step)
    prev_code = compute_totp(secret, timestamp=t - 25.0)
    assert verify_totp(prev_code, secret, timestamp=t, skew_steps=1) is True

    # Code from 25 seconds in future (within immediately following step)
    next_code = compute_totp(secret, timestamp=t + 25.0)
    assert verify_totp(next_code, secret, timestamp=t, skew_steps=1) is True


def test_verification_outside_skew_window_fails() -> None:
    secret = generate_totp_secret()
    t = 1000.0

    # 90 seconds in the past is outside skew=1 (which only permits +-30s)
    old_code = compute_totp(secret, timestamp=t - 90.0)
    assert verify_totp(old_code, secret, timestamp=t, skew_steps=1) is False

    # 90 seconds in future is outside skew=1
    future_code = compute_totp(secret, timestamp=t + 90.0)
    assert verify_totp(future_code, secret, timestamp=t, skew_steps=1) is False


def test_invalid_and_malformed_codes_rejected() -> None:
    secret = generate_totp_secret()
    assert verify_totp("12345", secret) is False  # 5 digits
    assert verify_totp("1234567", secret) is False  # 7 digits
    assert verify_totp("abcdef", secret) is False  # non-numeric
    assert verify_totp("", secret) is False  # empty
    assert verify_totp("000000", secret) is False  # wrong code


def test_provisioning_uri_format() -> None:
    secret = "JBSWY3DPEHPK3PXP"  # secret-scan:allow
    uri = build_provisioning_uri(secret, label="alice@example.com", issuer="Maya AI")
    assert uri.startswith("otpauth://totp/Maya%20AI:alice@example.com") or uri.startswith(
        "otpauth://totp/Maya AI:alice@example.com"
    )
    assert "secret=JBSWY3DPEHPK3PXP" in uri
    assert "issuer=Maya AI" in uri


def test_anti_replay_guard() -> None:
    from uuid import uuid4

    import pytest

    from app.core.totp import (
        InMemoryTOTPReplayGuard,
        TOTPReplayError,
        verify_totp_hardened,
    )

    secret = generate_totp_secret()
    guard = InMemoryTOTPReplayGuard()
    user_id = uuid4()
    t = 1000.0
    code = compute_totp(secret, timestamp=t)

    # First verification succeeds
    assert (
        verify_totp_hardened(code, secret, user_id=user_id, replay_guard=guard, timestamp=t) is True
    )

    # Replay of the same code within the window must raise TOTPReplayError
    with pytest.raises(TOTPReplayError):
        verify_totp_hardened(code, secret, user_id=user_id, replay_guard=guard, timestamp=t)


def test_rate_limiter_and_lockout() -> None:
    from uuid import uuid4

    import pytest

    from app.core.totp import (
        InMemoryTOTPRateLimiter,
        TOTPLockedOutError,
        verify_totp_hardened,
    )

    secret = generate_totp_secret()
    limiter = InMemoryTOTPRateLimiter()
    user_id = uuid4()
    t = 1000.0

    # 4 bad attempts fail without lockout
    for _ in range(4):
        assert (
            verify_totp_hardened(
                "000000", secret, user_id=user_id, rate_limiter=limiter, timestamp=t
            )
            is False
        )

    # 5th bad attempt triggers lockout
    assert (
        verify_totp_hardened("000000", secret, user_id=user_id, rate_limiter=limiter, timestamp=t)
        is False
    )

    # Next attempt (even with valid code) raises TOTPLockedOutError
    valid_code = compute_totp(secret, timestamp=t)
    with pytest.raises(TOTPLockedOutError):
        verify_totp_hardened(valid_code, secret, user_id=user_id, rate_limiter=limiter, timestamp=t)


def test_encryption_at_rest() -> None:
    from app.core.totp import decrypt_totp_secret, encrypt_totp_secret

    secret = generate_totp_secret()
    master_key = "example-master-key-32-chars-long!"
    encrypted = encrypt_totp_secret(secret, master_key)

    assert encrypted != secret
    decrypted = decrypt_totp_secret(encrypted, master_key)
    assert decrypted == secret
