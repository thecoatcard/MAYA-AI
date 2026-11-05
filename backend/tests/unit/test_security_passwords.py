import asyncio
import dataclasses
from unittest.mock import patch

import pytest
from argon2 import PasswordHasher, Type, profiles

from app.core.security import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    USERNAME_PATTERN,
    PasswordPolicyViolation,
    PasswordService,
    normalize_password,
    validate_password_policy,
)
from tests.support.passwords import FAST_PARAMETERS, fast_password_service

GOOD = "correct horse battery staple"


async def test_hash_is_argon2id_and_verifies() -> None:
    service = fast_password_service()
    hashed = await service.hash(GOOD)
    assert hashed.startswith("$argon2id$")
    assert GOOD not in hashed
    assert await service.verify(hashed, GOOD) is True


async def test_wrong_password_and_malformed_hash_are_rejected() -> None:
    service = fast_password_service()
    hashed = await service.hash(GOOD)
    assert await service.verify(hashed, GOOD + "x") is False
    assert await service.verify("not-a-hash", GOOD) is False
    assert await service.verify("", GOOD) is False


async def test_two_hashes_of_one_password_differ() -> None:
    service = fast_password_service()
    assert await service.hash(GOOD) != await service.hash(GOOD)


async def test_unicode_is_normalized_before_hashing() -> None:
    service = fast_password_service()
    composed, decomposed = "café-password!", "café-password!"
    assert composed != decomposed
    assert await service.verify(await service.hash(composed), decomposed) is True


def test_production_profile_is_the_rfc_9106_low_memory_profile() -> None:
    parameters = PasswordService().parameters
    assert parameters == profiles.RFC_9106_LOW_MEMORY
    assert parameters.type is Type.ID
    assert (parameters.time_cost, parameters.memory_cost, parameters.parallelism) == (3, 65536, 4)


async def test_stale_parameters_request_a_rehash() -> None:
    service = fast_password_service()
    hashed = await service.hash(GOOD)
    assert service.needs_rehash(hashed) is False
    stronger = PasswordService(dataclasses.replace(FAST_PARAMETERS, time_cost=2))
    assert stronger.needs_rehash(hashed) is True
    assert stronger.needs_rehash("garbage") is True


async def test_unknown_account_still_spends_a_verification() -> None:
    service = fast_password_service()
    with patch.object(service, "_verify", wraps=service._verify) as spy:  # noqa: SLF001
        await service.verify_unknown_account(GOOD)
    spy.assert_called_once()


class _ConcurrencyTracker:
    def __init__(self, inner: PasswordHasher) -> None:
        self.inner = inner
        self.active = 0
        self.peak = 0

    def hash(self, value: str) -> str:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            return self.inner.hash(value)
        finally:
            self.active -= 1


async def test_hashing_concurrency_is_bounded() -> None:
    service = PasswordService(FAST_PARAMETERS, concurrency=1)
    tracker = _ConcurrencyTracker(PasswordHasher.from_parameters(FAST_PARAMETERS))
    with patch.object(service, "_hasher", tracker):
        await asyncio.gather(*(service.hash(GOOD) for _ in range(6)))
    assert tracker.peak == 1


def test_policy_accepts_length_without_composition_rules() -> None:
    validate_password_policy("a" * PASSWORD_MIN_LENGTH)
    validate_password_policy("a" * PASSWORD_MAX_LENGTH)
    validate_password_policy("correct horse battery staple")


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("a" * (PASSWORD_MIN_LENGTH - 1), "password_too_short"),
        ("", "password_too_short"),
        ("a" * (PASSWORD_MAX_LENGTH + 1), "password_too_long"),
    ],
)
def test_policy_rejects_length_violations(password: str, code: str) -> None:
    with pytest.raises(PasswordPolicyViolation) as caught:
        validate_password_policy(password)
    assert caught.value.code == code


def test_policy_length_uses_the_normalized_form() -> None:
    # U+FDFA expands to 18 characters under NFKC, so 8 of them exceed the maximum.
    assert len(normalize_password("ﷺ")) == 18
    with pytest.raises(PasswordPolicyViolation):
        validate_password_policy("ﷺ" * 8)


def test_policy_rejects_the_supplied_identifiers_case_insensitively() -> None:
    with pytest.raises(PasswordPolicyViolation) as caught:
        validate_password_policy(
            "Alice.Example@Example.com", forbidden=["alice.example@example.com"]
        )
    assert caught.value.code == "password_matches_identifier"
    validate_password_policy("Alice.Example@Example.com!", forbidden=["alice.example@example.com"])


@pytest.mark.parametrize("name", ["abc", "a.b-c_d", "user123", "x" * 32])
def test_username_pattern_accepts(name: str) -> None:
    assert USERNAME_PATTERN.fullmatch(name)


@pytest.mark.parametrize(
    "name", ["ab", "x" * 33, "Upper", "has space", "a@b", "tab\t", "ünï", "new\nline", ""]
)
def test_username_pattern_rejects(name: str) -> None:
    assert USERNAME_PATTERN.fullmatch(name) is None
