"""Regressions for defects found in the independent security review of Phase 02."""

import asyncio
import dataclasses

import pytest

from app.auth.errors import InvalidCode, InvalidCredentials, InvalidToken, RateLimited
from app.auth.policy import RateLimits
from app.auth.store import AuthStore
from app.core.ratelimit import Limit
from app.core.security import PasswordService
from tests.support.auth import ADDRESS, GOOD_PASSWORD, NEW_PASSWORD, Harness, build_harness
from tests.support.passwords import FAST_PARAMETERS


async def test_a_token_stolen_several_rotations_ago_still_revokes_the_session(
    harness: Harness,
) -> None:
    """Reuse detection must not depend on the stolen token being the immediate predecessor."""
    command = await harness.register_and_verify()
    stolen = (await harness.login(command)).refresh_token
    # The thief exchanges the copy twice before the owner comes back.
    second = await harness.service.refresh(stolen)
    third = await harness.service.refresh(second.refresh_token)
    # The owner's own, now two generations old, token arrives.
    with pytest.raises(InvalidToken):
        await harness.service.refresh(stolen)
    assert ("refresh", "failed", "replayed") in harness.events.events
    with pytest.raises(InvalidToken):  # the thief's newest token died with the session
        await harness.service.refresh(third.refresh_token)
    with pytest.raises(InvalidToken):
        await harness.service.authenticate(third.access_token)


async def test_every_rotation_raises_the_generation(harness: Harness) -> None:
    command = await harness.register_and_verify()
    pair = await harness.login(command)
    generations = [harness.tokens.decode_refresh(pair.refresh_token).generation]
    for _ in range(3):
        pair = await harness.service.refresh(pair.refresh_token)
        generations.append(harness.tokens.decode_refresh(pair.refresh_token).generation)
    assert generations == [0, 1, 2, 3]


async def test_parallel_password_guesses_are_bounded_by_the_limit_not_by_cpu(
    harness: Harness,
) -> None:
    command = await harness.register_and_verify()
    verified = 0
    original = harness.passwords.verify

    async def counting_verify(hashed: str, password: str) -> bool:
        nonlocal verified
        verified += 1
        return await original(hashed, password)

    harness.passwords.verify = counting_verify  # type: ignore[method-assign]
    results = await asyncio.gather(
        *(
            harness.service.login(command.username, f"wrong password number {n}", ADDRESS)
            for n in range(40)
        ),
        return_exceptions=True,
    )
    assert verified == 10  # only the permitted attempts ever reached the password check
    assert sum(isinstance(result, InvalidCredentials) for result in results) == 10
    assert sum(isinstance(result, RateLimited) for result in results) == 30


async def test_a_correct_login_gives_its_attempt_back(auth_store: AuthStore) -> None:
    harness = build_harness(
        auth_store, limits=RateLimits(login_attempts_per_account_and_address=Limit(2, 900))
    )
    command = await harness.register_and_verify()
    for _ in range(6):  # more successes than the limit: each one is refunded
        await harness.login(command)
    with pytest.raises(InvalidCredentials):
        await harness.service.login(command.username, "wrong password here", ADDRESS)
    with pytest.raises(InvalidCredentials):
        await harness.service.login(command.username, "wrong password here", ADDRESS)
    with pytest.raises(RateLimited):
        await harness.login(command)


async def test_a_login_cannot_open_a_session_after_a_concurrent_password_reset(
    harness: Harness,
) -> None:
    command = await harness.register_and_verify()
    message = await harness.service.request_password_reset(command.email, ADDRESS)
    assert message is not None
    await harness.service.deliver(message)
    code = harness.email.codes_for(command.email)[-1]
    original = harness.passwords.verify

    async def verify_then_get_reset(hashed: str, password: str) -> bool:
        valid = await original(hashed, password)  # the old password is right...
        harness.clock.advance(seconds=1)  # (a frozen clock would give the reset the same version)
        await harness.service.reset_password(command.email, code, NEW_PASSWORD, ADDRESS)
        return valid  # ...but the reset commits before the session is opened

    harness.passwords.verify = verify_then_get_reset  # type: ignore[method-assign]
    with pytest.raises(InvalidCredentials):
        await harness.login(command)
    assert ("login", "failed", "password_changed") in harness.events.events
    harness.passwords.verify = original  # type: ignore[method-assign]
    assert (await harness.service.login(command.username, NEW_PASSWORD, ADDRESS)).access_token


async def test_a_stale_hash_upgrade_never_overwrites_a_reset_password(
    auth_store: AuthStore,
) -> None:
    seeding = build_harness(auth_store)
    command = await seeding.register_and_verify()
    stronger = PasswordService(dataclasses.replace(FAST_PARAMETERS, time_cost=2))
    harness = build_harness(auth_store, passwords=stronger)  # the old hash needs an upgrade
    message = await harness.service.request_password_reset(command.email, ADDRESS)
    assert message is not None
    await harness.service.deliver(message)
    code = harness.email.codes_for(command.email)[-1]
    original = stronger.verify

    async def verify_then_get_reset(hashed: str, password: str) -> bool:
        valid = await original(hashed, password)
        harness.clock.advance(seconds=1)
        await harness.service.reset_password(command.email, code, NEW_PASSWORD, ADDRESS)
        return valid

    stronger.verify = verify_then_get_reset  # type: ignore[method-assign]
    with pytest.raises(InvalidCredentials):
        await harness.login(command)
    stronger.verify = original  # type: ignore[method-assign]
    # The reset password is still the one that works; the old password's upgrade was discarded.
    assert (await harness.service.login(command.username, NEW_PASSWORD, ADDRESS)).access_token
    with pytest.raises(InvalidCredentials):
        await harness.service.login(command.username, GOOD_PASSWORD, ADDRESS)


async def test_the_daily_cap_limits_code_guessing_across_hours(auth_store: AuthStore) -> None:
    harness = build_harness(auth_store, limits=RateLimits(attempt_per_email_daily=Limit(6, 86400)))
    command = await harness.register()
    for _ in range(2):  # six wrong guesses spread over three hours stay under the hourly limit
        for _ in range(3):
            with pytest.raises(InvalidCode):
                await harness.service.verify_email(command.email, "000000", GOOD_PASSWORD, ADDRESS)
        harness.clock.advance(hours=1)
    with pytest.raises(RateLimited) as caught:
        await harness.service.verify_email(command.email, "000000", GOOD_PASSWORD, ADDRESS)
    assert caught.value.retry_after > 3600
