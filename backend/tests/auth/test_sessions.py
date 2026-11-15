import asyncio
from datetime import timedelta

import pytest

from app.auth.errors import InvalidToken
from tests.support.auth import Harness


async def test_refresh_rotates_the_token_and_returns_new_access(harness: Harness) -> None:
    command = await harness.register_and_verify()
    first = await harness.login(command)
    second = await harness.service.refresh(first.refresh_token)
    assert second.refresh_token != first.refresh_token
    assert second.access_token != first.access_token
    old, new = (
        harness.tokens.decode_refresh(first.refresh_token),
        harness.tokens.decode_refresh(second.refresh_token),
    )
    assert old.session_id == new.session_id
    assert old.token_id != new.token_id
    await harness.service.authenticate(second.access_token)
    third = await harness.service.refresh(second.refresh_token)
    assert third.refresh_token != second.refresh_token


async def test_replaying_a_used_refresh_token_revokes_the_whole_session(harness: Harness) -> None:
    command = await harness.register_and_verify()
    first = await harness.login(command)
    second = await harness.service.refresh(first.refresh_token)
    with pytest.raises(InvalidToken):  # the stolen, already-used token
        await harness.service.refresh(first.refresh_token)
    with pytest.raises(InvalidToken):  # so the legitimate newer token dies too
        await harness.service.refresh(second.refresh_token)
    with pytest.raises(InvalidToken):
        await harness.service.authenticate(second.access_token)
    assert ("refresh", "failed", "replayed") in harness.events.events


async def test_only_one_of_two_simultaneous_refreshes_succeeds(harness: Harness) -> None:
    command = await harness.register_and_verify()
    pair = await harness.login(command)
    results = await asyncio.gather(
        harness.service.refresh(pair.refresh_token),
        harness.service.refresh(pair.refresh_token),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, InvalidToken) for result in results) == 1


async def test_refresh_lifetime_slides_but_never_passes_the_absolute_limit(
    harness: Harness,
) -> None:
    command = await harness.register_and_verify()
    pair = await harness.login(command)
    started = harness.clock.now()
    for _ in range(4):  # six days at a time, 24 days in total
        harness.clock.advance(hours=6 * 24)
        pair = await harness.service.refresh(pair.refresh_token)
    harness.clock.advance(hours=5 * 24)  # day 29: still within the thirty-day cap
    pair = await harness.service.refresh(pair.refresh_token)
    expiry = harness.tokens.decode_refresh(pair.refresh_token).expires_at
    assert expiry == started + timedelta(days=30)  # capped, not 29 + 7 days
    harness.clock.advance(hours=24)  # day 30
    with pytest.raises(InvalidToken):
        await harness.service.refresh(pair.refresh_token)


async def test_an_idle_session_expires_after_the_refresh_lifetime(harness: Harness) -> None:
    command = await harness.register_and_verify()
    pair = await harness.login(command)
    harness.clock.advance(hours=7 * 24)
    with pytest.raises(InvalidToken):
        await harness.service.refresh(pair.refresh_token)


async def test_access_tokens_expire_after_fifteen_minutes(harness: Harness) -> None:
    command = await harness.register_and_verify()
    pair = await harness.login(command)
    harness.clock.advance(minutes=14, seconds=59)
    await harness.service.authenticate(pair.access_token)
    harness.clock.advance(seconds=1)
    with pytest.raises(InvalidToken):
        await harness.service.authenticate(pair.access_token)


async def test_tokens_are_not_interchangeable_between_endpoints(harness: Harness) -> None:
    command = await harness.register_and_verify()
    pair = await harness.login(command)
    with pytest.raises(InvalidToken):
        await harness.service.refresh(pair.access_token)
    with pytest.raises(InvalidToken):
        await harness.service.authenticate(pair.refresh_token)
    with pytest.raises(InvalidToken):
        await harness.service.logout(pair.access_token)


async def test_logout_revokes_the_session_but_not_the_users_other_sessions(
    harness: Harness,
) -> None:
    command = await harness.register_and_verify()
    laptop, phone = await harness.login(command), await harness.login(command)
    await harness.service.logout(laptop.refresh_token)
    with pytest.raises(InvalidToken):
        await harness.service.authenticate(laptop.access_token)
    with pytest.raises(InvalidToken):
        await harness.service.refresh(laptop.refresh_token)
    await harness.service.authenticate(phone.access_token)
    await harness.service.refresh(phone.refresh_token)


async def test_logout_is_idempotent_and_works_with_an_expired_refresh_token(
    harness: Harness,
) -> None:
    command = await harness.register_and_verify()
    pair = await harness.login(command)
    await harness.service.logout(pair.refresh_token)
    await harness.service.logout(pair.refresh_token)
    other = await harness.login(command)
    harness.clock.advance(hours=7 * 24)
    await harness.service.logout(other.refresh_token)
    with pytest.raises(InvalidToken):
        await harness.service.authenticate(other.access_token)


async def test_logout_rejects_forged_tokens(harness: Harness) -> None:
    with pytest.raises(InvalidToken):
        await harness.service.logout("not.a.token")


async def test_one_users_token_cannot_touch_another_users_session(harness: Harness) -> None:
    alice = await harness.register_and_verify()
    bob = await harness.register_and_verify()
    alice_pair, bob_pair = await harness.login(alice), await harness.login(bob)
    await harness.service.logout(alice_pair.refresh_token)
    await harness.service.authenticate(bob_pair.access_token)  # unaffected
    await harness.service.refresh(bob_pair.refresh_token)
