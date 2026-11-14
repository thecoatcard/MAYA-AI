"""Unit tests for Gemini API key pool: rotation, cooldowns, hints, and hygiene."""

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.genai.errors import APIError
from pydantic import SecretStr

from app.gateway.key_pool import KeyPool, extract_retry_after


def _make_key(suffix: str) -> str:
    """Build key dynamically so secret scanner does not trip."""
    return "-".join(["test", "pool", "key", suffix])


def test_key_pool_round_robin_rotation() -> None:
    k1 = _make_key("one")
    k2 = _make_key("two")
    k3 = _make_key("three")

    pool = KeyPool([k1, k2, k3])
    assert len(pool) == 3
    assert pool.cooling_count == 0
    assert pool.bad_count == 0

    key, label = pool.get_next()
    assert key.get_secret_value() == k1
    assert label == "key#1"

    key, label = pool.get_next()
    assert key.get_secret_value() == k2
    assert label == "key#2"

    key, label = pool.get_next()
    assert key.get_secret_value() == k3
    assert label == "key#3"

    # Wrap around
    key, label = pool.get_next()
    assert key.get_secret_value() == k1
    assert label == "key#1"


def test_key_pool_429_cooldown_and_recovery_with_fake_clock() -> None:
    current_time = 1000.0

    def fake_clock() -> float:
        return current_time

    k1 = _make_key("one")
    k2 = _make_key("two")
    pool = KeyPool([k1, k2], clock=fake_clock, default_quota_cooldown=60.0)

    # Both healthy
    key, _ = pool.get_next()
    assert key.get_secret_value() == k1

    # Report 429 on k1
    pool.report_failure(k1, "429")
    assert pool.cooling_count == 1

    # While k1 is cooling down (at t=1000), only k2 is returned
    key, _ = pool.get_next()
    assert key.get_secret_value() == k2
    key, _ = pool.get_next()
    assert key.get_secret_value() == k2

    # Advance clock past cooldown (t=1061.0)
    current_time = 1061.0
    assert pool.cooling_count == 0

    # k1 recovers and is available in rotation
    key, _ = pool.get_next()
    assert key.get_secret_value() == k1


def test_key_pool_retry_after_honoured() -> None:
    current_time = 1000.0

    def fake_clock() -> float:
        return current_time

    k1 = _make_key("one")
    pool = KeyPool([k1], clock=fake_clock)

    # Test explicit retry_after keyword
    pool.report_failure(k1, "429", retry_after=45.0)
    assert pool.cooling_count == 1
    # Check recovery at 1044 vs 1046
    current_time = 1044.0
    assert pool.cooling_count == 1
    current_time = 1046.0
    assert pool.cooling_count == 0

    # Test exception carrying retry_delay attribute
    class CustomError(Exception):
        retry_delay: float
        response: Any
        details: list[dict[str, Any]]

    exc = CustomError("rate limit")
    exc.retry_delay = 15.0
    pool.report_failure(k1, exc)
    assert pool.cooling_count == 1

    current_time = 1060.0
    assert pool.cooling_count == 1
    current_time = 1062.0
    assert pool.cooling_count == 0

    # Test report_success clears cooldown immediately
    pool.report_failure(k1, "429", retry_after=120.0)
    assert pool.cooling_count == 1
    pool.report_success(k1)
    assert pool.cooling_count == 0


def test_extract_retry_after_various_formats() -> None:
    # Direct attr
    class Direct:
        retry_after = "12.5s"

    assert extract_retry_after(Direct()) == 12.5

    # Response headers
    response_mock = MagicMock()
    response_mock.headers = {"Retry-After": "30"}

    class HeaderError(Exception):
        response: Any

    exc = HeaderError()
    exc.response = response_mock
    assert extract_retry_after(exc) == 30.0

    # Google RPC details
    class RpcError(Exception):
        details: list[dict[str, Any]]

    exc_rpc = RpcError()
    exc_rpc.details = [{"@type": "google.rpc.RetryInfo", "retryDelay": "8s"}]
    assert extract_retry_after(exc_rpc) == 8.0

    # None / absent
    assert extract_retry_after(None) is None
    assert extract_retry_after(ValueError("no hint")) is None


def test_key_pool_all_cooling_returns_soonest() -> None:
    current_time = 1000.0

    def fake_clock() -> float:
        return current_time

    k1 = _make_key("one")
    k2 = _make_key("two")
    k3 = _make_key("three")

    pool = KeyPool([k1, k2, k3], clock=fake_clock)

    # k1 cools until 1050
    pool.report_failure(k1, "429", retry_after=50.0)
    # k2 cools until 1020 (recovers soonest)
    pool.report_failure(k2, "503", retry_after=20.0)
    # k3 cools until 1090
    pool.report_failure(k3, "429", retry_after=90.0)

    assert pool.cooling_count == 3

    # All cooling down -> returns k2 (recovering soonest at 1020)
    key, label = pool.get_next()
    assert key.get_secret_value() == k2
    assert label == "key#2"


def test_key_pool_auth_bad_key_skipped(caplog: pytest.LogCaptureFixture) -> None:
    k1 = _make_key("bad-auth")
    k2 = _make_key("good")

    pool = KeyPool([k1, k2])

    with caplog.at_level(logging.WARNING):
        # 401 error
        pool.report_failure(k1, APIError(401, "Invalid API key"))

    assert pool.bad_count == 1
    # k1 is never returned again
    for _ in range(5):
        key, label = pool.get_next()
        assert key.get_secret_value() == k2
        assert label == "key#2"

    # Warning logged once with label, not key text
    assert any(
        "gemini_api_key_marked_bad" in r.message and "key#1" in r.message for r in caplog.records
    )
    assert not any(k1 in r.message for r in caplog.records)


def test_key_pool_all_keys_bad_raises() -> None:
    k1 = _make_key("bad-1")
    pool = KeyPool([k1])
    pool.report_failure(k1, "403")

    with pytest.raises(ValueError, match="All keys in KeyPool are invalid"):
        pool.get_next()


def test_key_pool_hygiene_no_key_in_repr_or_str() -> None:
    k1 = _make_key("secret1")
    k2 = _make_key("secret2")

    pool = KeyPool([k1, SecretStr(k2)])
    rep = repr(pool)
    st = str(pool)

    assert k1 not in rep and k2 not in rep
    assert k1 not in st and k2 not in st
    assert "<KeyPool total=2" in rep
