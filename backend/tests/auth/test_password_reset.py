import pytest

from app.auth.errors import InvalidCode, InvalidCredentials, InvalidToken, RateLimited
from app.auth.policy import RateLimits
from app.auth.store import AuthStore
from app.core.ratelimit import Limit
from app.core.security import PasswordPolicyViolation
from tests.support.auth import ADDRESS, GOOD_PASSWORD, NEW_PASSWORD, Harness, build_harness


async def reset_code(harness: Harness, email: str) -> str:
    message = await harness.service.request_password_reset(email, ADDRESS)
    assert message is not None
    await harness.service.deliver(message)
    return harness.email.codes_for(email)[-1]


async def test_a_known_address_receives_a_reset_code(harness: Harness) -> None:
    command = await harness.register_and_verify()
    message = await harness.service.request_password_reset(command.email, ADDRESS)
    assert message is not None
    assert message.to == command.email
    assert "reset" in message.subject.lower()


async def test_an_unknown_address_gets_no_mail_and_no_error(harness: Harness) -> None:
    assert (
        await harness.service.request_password_reset("nobody.at.all@example.com", ADDRESS) is None
    )
    assert harness.email.sent == []


async def test_reset_requests_respect_the_cooldown_and_the_send_limit(harness: Harness) -> None:
    command = await harness.register_and_verify()
    assert await harness.service.request_password_reset(command.email, ADDRESS) is not None
    assert await harness.service.request_password_reset(command.email, ADDRESS) is None
    harness.clock.advance(seconds=60)
    assert await harness.service.request_password_reset(command.email, ADDRESS) is not None


async def test_reset_requests_are_rate_limited(auth_store: AuthStore) -> None:
    harness = build_harness(auth_store, limits=RateLimits(send_per_email=Limit(2, 3600)))
    command = await harness.register_and_verify()  # one send already counted
    await harness.service.request_password_reset(command.email, ADDRESS)
    with pytest.raises(RateLimited):
        await harness.service.request_password_reset(command.email, ADDRESS)


async def test_reset_changes_the_password_and_ends_every_session(harness: Harness) -> None:
    command = await harness.register_and_verify()
    laptop, phone = await harness.login(command), await harness.login(command)
    code = await reset_code(harness, command.email)
    await harness.service.reset_password(command.email, code, NEW_PASSWORD, ADDRESS)

    for pair in (laptop, phone):
        with pytest.raises(InvalidToken):
            await harness.service.authenticate(pair.access_token)
        with pytest.raises(InvalidToken):
            await harness.service.refresh(pair.refresh_token)
    with pytest.raises(InvalidCredentials):
        await harness.login(command)
    fresh = await harness.service.login(command.username, NEW_PASSWORD, ADDRESS)
    assert (await harness.service.authenticate(fresh.access_token)).session_id


async def test_a_session_created_after_the_reset_is_not_affected(harness: Harness) -> None:
    command = await harness.register_and_verify()
    code = await reset_code(harness, command.email)
    await harness.service.reset_password(command.email, code, NEW_PASSWORD, ADDRESS)
    pair = await harness.service.login(command.username, NEW_PASSWORD, ADDRESS)
    await harness.service.authenticate(pair.access_token)


async def test_reset_also_verifies_an_address_that_was_never_confirmed(harness: Harness) -> None:
    command = await harness.register()  # never verified
    code = await reset_code(harness, command.email)
    await harness.service.reset_password(command.email, code, NEW_PASSWORD, ADDRESS)
    assert (await harness.service.login(command.username, NEW_PASSWORD, ADDRESS)).access_token


async def test_a_weak_new_password_is_rejected_without_using_up_the_code(
    harness: Harness,
) -> None:
    command = await harness.register_and_verify()
    code = await reset_code(harness, command.email)
    for weak in ("short", command.email):
        with pytest.raises(PasswordPolicyViolation):
            await harness.service.reset_password(command.email, code, weak, ADDRESS)
    await harness.service.reset_password(command.email, code, NEW_PASSWORD, ADDRESS)


async def test_a_reset_code_works_once(harness: Harness) -> None:
    command = await harness.register_and_verify()
    code = await reset_code(harness, command.email)
    await harness.service.reset_password(command.email, code, NEW_PASSWORD, ADDRESS)
    with pytest.raises(InvalidCode):
        await harness.service.reset_password(command.email, code, GOOD_PASSWORD + "2", ADDRESS)


async def test_wrong_codes_are_counted_and_five_kill_the_code(harness: Harness) -> None:
    command = await harness.register_and_verify()
    code = await reset_code(harness, command.email)
    wrong = "000000" if code != "000000" else "000001"
    for _ in range(5):
        with pytest.raises(InvalidCode):
            await harness.service.reset_password(command.email, wrong, NEW_PASSWORD, ADDRESS)
    with pytest.raises(InvalidCode):
        await harness.service.reset_password(command.email, code, NEW_PASSWORD, ADDRESS)
    assert (await harness.login(command)).access_token  # the old password is untouched


async def test_reset_codes_expire_after_ten_minutes(harness: Harness) -> None:
    command = await harness.register_and_verify()
    code = await reset_code(harness, command.email)
    harness.clock.advance(minutes=10)
    with pytest.raises(InvalidCode):
        await harness.service.reset_password(command.email, code, NEW_PASSWORD, ADDRESS)


async def test_a_new_reset_code_replaces_the_previous_one(harness: Harness) -> None:
    command = await harness.register_and_verify()
    first = await reset_code(harness, command.email)
    harness.clock.advance(seconds=61)
    second = await reset_code(harness, command.email)
    if first != second:
        with pytest.raises(InvalidCode):
            await harness.service.reset_password(command.email, first, NEW_PASSWORD, ADDRESS)
    await harness.service.reset_password(command.email, second, NEW_PASSWORD, ADDRESS)


async def test_unknown_address_and_wrong_code_fail_the_same_way(harness: Harness) -> None:
    command = await harness.register_and_verify()
    await reset_code(harness, command.email)
    with pytest.raises(InvalidCode) as unknown:
        await harness.service.reset_password(
            "nobody.at.all@example.com", "123456", NEW_PASSWORD, ADDRESS
        )
    with pytest.raises(InvalidCode) as wrong:
        await harness.service.reset_password(command.email, "000000", NEW_PASSWORD, ADDRESS)
    assert str(unknown.value) == str(wrong.value)


async def test_the_codes_of_the_two_purposes_are_not_interchangeable(harness: Harness) -> None:
    command = await harness.register()  # has a verification code
    verification_code = harness.email.codes_for(command.email)[-1]
    with pytest.raises(InvalidCode):
        await harness.service.reset_password(
            command.email, verification_code, NEW_PASSWORD, ADDRESS
        )
    reset = await reset_code(harness, command.email)
    with pytest.raises(InvalidCode):
        await harness.service.verify_email(command.email, reset, GOOD_PASSWORD, ADDRESS)
    await harness.service.verify_email(command.email, verification_code, GOOD_PASSWORD, ADDRESS)


async def test_a_successful_reset_voids_other_outstanding_codes(harness: Harness) -> None:
    command = await harness.register()
    verification_code = harness.email.codes_for(command.email)[-1]
    code = await reset_code(harness, command.email)
    await harness.service.reset_password(command.email, code, NEW_PASSWORD, ADDRESS)
    with pytest.raises(InvalidCode):  # already verified now, and the old code is void anyway
        await harness.service.verify_email(command.email, verification_code, GOOD_PASSWORD, ADDRESS)


async def test_concurrent_resets_with_one_code_succeed_exactly_once(harness: Harness) -> None:
    import asyncio

    command = await harness.register_and_verify()
    code = await reset_code(harness, command.email)
    results = await asyncio.gather(
        *(
            harness.service.reset_password(command.email, code, f"{NEW_PASSWORD} {index}", ADDRESS)
            for index in range(4)
        ),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
