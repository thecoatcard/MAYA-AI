import dataclasses
from unittest.mock import patch

import pytest

from app.auth.errors import EmailNotVerified, InvalidCredentials, InvalidToken, RateLimited
from app.auth.policy import RateLimits
from app.auth.store import AuthStore
from app.core.ratelimit import Limit
from app.core.security import PasswordService
from tests.support.auth import ADDRESS, GOOD_PASSWORD, Harness, build_harness
from tests.support.passwords import FAST_PARAMETERS

OTHER_ADDRESS = "198.51.100.9"


async def test_login_returns_a_token_pair_for_a_verified_account(harness: Harness) -> None:
    command = await harness.register_and_verify()
    pair = await harness.login(command)
    assert pair.expires_in == 900
    access = harness.tokens.decode_access(pair.access_token)
    refresh = harness.tokens.decode_refresh(pair.refresh_token)
    assert access.role == "admin"
    assert access.session_id == refresh.session_id
    assert access.workspace_id == refresh.workspace_id
    assert access.user_id == refresh.user_id
    principal = await harness.service.authenticate(pair.access_token)
    assert (principal.user_id, principal.workspace_id) == (access.user_id, access.workspace_id)


async def test_each_login_is_its_own_session(harness: Harness) -> None:
    command = await harness.register_and_verify()
    first, second = await harness.login(command), await harness.login(command)
    assert (
        harness.tokens.decode_access(first.access_token).session_id
        != harness.tokens.decode_access(second.access_token).session_id
    )


async def test_wrong_password_and_unknown_user_fail_identically(harness: Harness) -> None:
    command = await harness.register_and_verify()
    with pytest.raises(InvalidCredentials) as wrong:
        await harness.service.login(command.username, GOOD_PASSWORD + "x", ADDRESS)
    with pytest.raises(InvalidCredentials) as unknown:
        await harness.service.login("nobody_at_all", GOOD_PASSWORD, ADDRESS)
    assert str(wrong.value) == str(unknown.value)


async def test_an_unknown_user_still_costs_a_password_verification(harness: Harness) -> None:
    with patch.object(
        harness.passwords, "verify_unknown_account", wraps=harness.passwords.verify_unknown_account
    ) as spy:
        with pytest.raises(InvalidCredentials):
            await harness.service.login("nobody_at_all", GOOD_PASSWORD, ADDRESS)
    spy.assert_awaited_once()


async def test_unverified_account_is_refused_only_after_the_password_is_right(
    harness: Harness,
) -> None:
    command = await harness.register()
    with pytest.raises(EmailNotVerified):
        await harness.login(command)
    with pytest.raises(InvalidCredentials):  # a wrong password reveals nothing about status
        await harness.service.login(command.username, GOOD_PASSWORD + "x", ADDRESS)


async def test_repeated_failures_lock_the_account_and_address_pair_not_the_account(
    harness: Harness,
) -> None:
    command = await harness.register_and_verify()
    for _ in range(10):
        with pytest.raises(InvalidCredentials):
            await harness.service.login(command.username, "wrong password here", ADDRESS)
    with pytest.raises(RateLimited):  # even the right password is held back
        await harness.login(command)
    # Another source address is unaffected, so an attacker cannot lock the owner out.
    assert (
        await harness.service.login(command.username, command.password, OTHER_ADDRESS)
    ).access_token


async def test_the_failure_window_expires(harness: Harness) -> None:
    command = await harness.register_and_verify()
    for _ in range(10):
        with pytest.raises(InvalidCredentials):
            await harness.service.login(command.username, "wrong password here", ADDRESS)
    harness.clock.advance(minutes=15)
    assert (await harness.login(command)).access_token


async def test_failures_are_limited_per_address_across_accounts(auth_store: AuthStore) -> None:
    harness = build_harness(auth_store, limits=RateLimits(login_attempts_per_address=Limit(3, 900)))
    for index in range(3):
        with pytest.raises(InvalidCredentials):
            await harness.service.login(f"nobody_{index}", GOOD_PASSWORD, ADDRESS)
    with pytest.raises(RateLimited):
        await harness.service.login("nobody_other", GOOD_PASSWORD, ADDRESS)


async def test_successful_logins_do_not_count_as_failures(harness: Harness) -> None:
    command = await harness.register_and_verify()
    for _ in range(15):
        await harness.login(command)


async def test_login_fails_closed_when_the_limiter_is_down(harness: Harness) -> None:
    command = await harness.register_and_verify()
    harness.limiter.unavailable = True
    from app.auth.errors import ServiceUnavailable

    with pytest.raises(ServiceUnavailable):
        await harness.login(command)


async def test_an_outdated_password_hash_is_upgraded_on_login(auth_store: AuthStore) -> None:
    weak = build_harness(auth_store)
    command = await weak.register_and_verify()
    stronger = PasswordService(dataclasses.replace(FAST_PARAMETERS, time_cost=2))
    upgraded = build_harness(auth_store, passwords=stronger)
    async with auth_store.transaction(None) as tx:
        before = await tx.find_account_by_username(command.username)
    assert before is not None and stronger.needs_rehash(before.hashed_password)
    await upgraded.login(command)
    async with auth_store.transaction(None) as tx:
        after = await tx.find_account_by_username(command.username)
    assert after is not None
    assert after.hashed_password != before.hashed_password
    assert not stronger.needs_rehash(after.hashed_password)
    await upgraded.login(command)  # the new hash verifies


async def test_garbage_access_tokens_never_authenticate(harness: Harness) -> None:
    for token in ("", "abc", "a.b.c"):
        with pytest.raises(InvalidToken):
            await harness.service.authenticate(token)
