import asyncio

import pytest

from app.auth.errors import (
    EmailNotVerified,
    InvalidCode,
    InvalidCredentials,
    RateLimited,
    ServiceUnavailable,
)
from app.auth.models import AccountRecord
from app.auth.policy import RateLimits
from app.auth.store import AuthStore
from app.core.ratelimit import Limit
from app.core.security import PasswordPolicyViolation
from tests.support.auth import ADDRESS, CODE_PATTERN, GOOD_PASSWORD, Harness, build_harness

OTHER_ADDRESS = "198.51.100.9"


async def stored_account(harness: Harness, username: str) -> AccountRecord | None:
    async with harness.store.transaction(None) as tx:
        return await tx.find_account_by_username(username)


# ----------------------------------------------------------------------- registration


async def test_registration_creates_an_unverified_account_and_mails_a_code(
    harness: Harness,
) -> None:
    command = harness.command()
    message = await harness.service.register(command, ADDRESS)
    assert message is not None
    assert message.to == command.email
    assert "verification" in message.subject.lower()
    assert CODE_PATTERN.search(message.body)
    account = await stored_account(harness, command.username)
    assert account is not None
    assert account.email_verified_at is None
    assert account.role == "admin"
    assert account.hashed_password.startswith("$argon2id$")
    assert command.password not in account.hashed_password
    with pytest.raises(EmailNotVerified):
        await harness.login(command)


async def test_each_registration_gets_its_own_workspace(harness: Harness) -> None:
    first, second = await harness.register(), await harness.register()
    one = await stored_account(harness, first.username)
    two = await stored_account(harness, second.username)
    assert one is not None and two is not None
    assert one.workspace_id != two.workspace_id


async def test_identifiers_are_normalized_before_use(harness: Harness) -> None:
    command = await harness.register_and_verify()
    tokens = await harness.service.login(command.username.upper(), command.password, ADDRESS)
    assert tokens.access_token


@pytest.mark.parametrize("password", ["short", "x" * 11, "a" * 129, "ﷺ" * 8])
async def test_weak_passwords_are_rejected_without_creating_anything(
    harness: Harness, password: str
) -> None:
    command = harness.command(password=password)
    with pytest.raises(PasswordPolicyViolation):
        await harness.service.register(command, ADDRESS)
    assert await stored_account(harness, command.username) is None
    assert harness.email.sent == []


async def test_a_password_equal_to_the_username_or_email_is_rejected(harness: Harness) -> None:
    username, email = harness.identity()
    for secret in (email, username):
        command = harness.command(username=username, email=email, password=secret)
        with pytest.raises(PasswordPolicyViolation):
            await harness.service.register(command, ADDRESS)
    assert await stored_account(harness, username) is None


async def test_duplicate_of_a_verified_account_sends_nothing_and_changes_nothing(
    harness: Harness,
) -> None:
    original = await harness.register_and_verify()
    sent_before = len(harness.email.sent)
    same_email = harness.command(email=original.email)
    same_username = harness.command(username=original.username)
    assert await harness.service.register(same_email, ADDRESS) is None
    assert await harness.service.register(same_username, ADDRESS) is None
    assert await stored_account(harness, same_email.username) is None
    assert len(harness.email.sent) == sent_before
    assert (await harness.login(original)).access_token


async def test_duplicate_of_an_unverified_account_resends_a_code_after_the_cooldown(
    harness: Harness,
) -> None:
    original = await harness.register()
    clash = harness.command(email=original.email)
    assert await harness.service.register(clash, ADDRESS) is None  # inside the cooldown
    harness.clock.advance(seconds=61)
    message = await harness.service.register(clash, ADDRESS)
    assert message is not None
    assert message.to == original.email
    assert await stored_account(harness, clash.username) is None  # the squatter's data is unused
    await harness.service.deliver(message)
    codes = harness.email.codes_for(original.email)
    assert len(codes) == 2
    await harness.service.verify_email(original.email, codes[-1], GOOD_PASSWORD, ADDRESS)
    assert (await harness.login(original)).access_token


async def test_registration_requires_working_e_mail(auth_store: AuthStore) -> None:
    harness = build_harness(auth_store, email=False)
    command = harness.command()
    with pytest.raises(ServiceUnavailable):
        await harness.service.register(command, ADDRESS)
    assert await stored_account(harness, command.username) is None


async def test_registration_fails_closed_when_the_rate_limiter_is_down(harness: Harness) -> None:
    harness.limiter.unavailable = True
    command = harness.command()
    with pytest.raises(ServiceUnavailable):
        await harness.service.register(command, ADDRESS)
    assert await stored_account(harness, command.username) is None


async def test_sends_are_limited_per_e_mail_address_and_recover_with_the_window(
    harness: Harness,
) -> None:
    original = await harness.register()
    for _ in range(4):  # the registration was hit one; four more reach the limit of five
        await harness.service.register(harness.command(email=original.email), ADDRESS)
    with pytest.raises(RateLimited) as caught:
        await harness.service.register(harness.command(email=original.email), ADDRESS)
    assert caught.value.retry_after == 3600
    harness.clock.advance(hours=1)
    await harness.service.register(harness.command(email=original.email), ADDRESS)


async def test_sends_are_limited_per_source_address(auth_store: AuthStore) -> None:
    harness = build_harness(
        auth_store,
        limits=RateLimits(send_per_address=Limit(3, 3600), send_per_email=Limit(99, 3600)),
    )
    for _ in range(3):
        await harness.register()
    with pytest.raises(RateLimited):
        await harness.service.register(harness.command(), ADDRESS)
    await harness.service.register(harness.command(), OTHER_ADDRESS)


async def test_concurrent_registrations_of_one_address_create_exactly_one_account(
    harness: Harness,
) -> None:
    _, email = harness.identity()
    commands = [harness.command(email=email) for _ in range(5)]
    results = await asyncio.gather(
        *(harness.service.register(command, ADDRESS) for command in commands),
        return_exceptions=True,
    )
    assert not [result for result in results if isinstance(result, Exception)]
    created = [c for c in commands if await stored_account(harness, c.username) is not None]
    assert len(created) == 1


# ------------------------------------------------------------------------ verification


async def test_the_mailed_code_verifies_the_address_and_enables_login(harness: Harness) -> None:
    command = await harness.register()
    code = harness.email.codes_for(command.email)[-1]
    await harness.service.verify_email(command.email, code, GOOD_PASSWORD, ADDRESS)
    account = await stored_account(harness, command.username)
    assert account is not None and account.email_verified_at is not None
    assert (await harness.login(command)).access_token


async def test_a_code_works_once(harness: Harness) -> None:
    command = await harness.register()
    code = harness.email.codes_for(command.email)[-1]
    await harness.service.verify_email(command.email, code, GOOD_PASSWORD, ADDRESS)
    with pytest.raises(InvalidCode):
        await harness.service.verify_email(command.email, code, GOOD_PASSWORD, ADDRESS)


async def test_a_wrong_code_is_rejected_and_the_right_one_still_works_within_five_attempts(
    harness: Harness,
) -> None:
    command = await harness.register()
    code = harness.email.codes_for(command.email)[-1]
    wrong = "000000" if code != "000000" else "000001"
    for _ in range(4):
        with pytest.raises(InvalidCode):
            await harness.service.verify_email(command.email, wrong, GOOD_PASSWORD, ADDRESS)
    await harness.service.verify_email(
        command.email, code, GOOD_PASSWORD, ADDRESS
    )  # the fifth attempt


async def test_after_five_wrong_attempts_even_the_right_code_is_dead(harness: Harness) -> None:
    command = await harness.register()
    code = harness.email.codes_for(command.email)[-1]
    wrong = "000000" if code != "000000" else "000001"
    for _ in range(5):
        with pytest.raises(InvalidCode):
            await harness.service.verify_email(command.email, wrong, GOOD_PASSWORD, ADDRESS)
    with pytest.raises(InvalidCode):
        await harness.service.verify_email(command.email, code, GOOD_PASSWORD, ADDRESS)
    with pytest.raises(EmailNotVerified):
        await harness.login(command)


async def test_failed_attempts_are_remembered_across_requests(harness: Harness) -> None:
    """The counter is committed even though the request fails (it is not rolled back)."""
    command = await harness.register()
    wrong = "000000" if harness.email.codes_for(command.email)[-1] != "000000" else "000001"
    with pytest.raises(InvalidCode):
        await harness.service.verify_email(command.email, wrong, GOOD_PASSWORD, ADDRESS)
    account = await stored_account(harness, command.username)
    assert account is not None
    async with harness.store.transaction(account.workspace_id) as tx:
        challenge = await tx.record_challenge_attempt(
            account.user_id, "email_verification", harness.clock.now(), 5
        )
    assert challenge is not None
    assert challenge.attempts == 2  # the failed request's attempt plus this probe


@pytest.mark.parametrize(
    ("advance", "valid"),
    [({"minutes": 9, "seconds": 59}, True), ({"minutes": 10}, False), ({"minutes": 11}, False)],
)
async def test_codes_expire_exactly_ten_minutes_after_issue(
    harness: Harness, advance: dict[str, int], valid: bool
) -> None:
    command = await harness.register()
    code = harness.email.codes_for(command.email)[-1]
    harness.clock.advance(**advance)
    if valid:
        await harness.service.verify_email(command.email, code, GOOD_PASSWORD, ADDRESS)
    else:
        with pytest.raises(InvalidCode):
            await harness.service.verify_email(command.email, code, GOOD_PASSWORD, ADDRESS)


async def test_unknown_and_already_verified_addresses_get_the_same_failure(
    harness: Harness,
) -> None:
    verified = await harness.register_and_verify()
    for email in ("nobody.at.all@example.com", verified.email):
        with pytest.raises(InvalidCode):
            await harness.service.verify_email(email, "123456", GOOD_PASSWORD, ADDRESS)


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 456", "١٢٣٤٥٦"])
async def test_malformed_codes_are_just_wrong_codes(harness: Harness, bad: str) -> None:
    command = await harness.register()
    with pytest.raises(InvalidCode):
        await harness.service.verify_email(command.email, bad, GOOD_PASSWORD, ADDRESS)


async def test_resend_inside_the_cooldown_sends_nothing(harness: Harness) -> None:
    command = await harness.register()
    assert await harness.service.resend_verification(command.email, ADDRESS) is None
    assert len(harness.email.codes_for(command.email)) == 1


async def test_resend_after_the_cooldown_replaces_the_old_code(harness: Harness) -> None:
    command = await harness.register()
    old = harness.email.codes_for(command.email)[-1]
    harness.clock.advance(seconds=60)
    message = await harness.service.resend_verification(command.email, ADDRESS)
    assert message is not None
    await harness.service.deliver(message)
    new = harness.email.codes_for(command.email)[-1]
    if new != old:  # a one-in-a-million collision would make the old code valid again
        with pytest.raises(InvalidCode):
            await harness.service.verify_email(command.email, old, GOOD_PASSWORD, ADDRESS)
    await harness.service.verify_email(command.email, new, GOOD_PASSWORD, ADDRESS)


@pytest.mark.parametrize("target", ["unknown", "verified"])
async def test_resend_is_silent_for_unknown_and_verified_addresses(
    harness: Harness, target: str
) -> None:
    email = "nobody.at.all@example.com"
    if target == "verified":
        email = (await harness.register_and_verify()).email
    sent = len(harness.email.sent)
    assert await harness.service.resend_verification(email, ADDRESS) is None
    assert len(harness.email.sent) == sent


async def test_verification_attempts_are_rate_limited(auth_store: AuthStore) -> None:
    harness = build_harness(auth_store, limits=RateLimits(attempt_per_email=Limit(3, 3600)))
    command = await harness.register()
    for _ in range(3):
        with pytest.raises(InvalidCode):
            await harness.service.verify_email(command.email, "000000", GOOD_PASSWORD, ADDRESS)
    with pytest.raises(RateLimited):
        await harness.service.verify_email(command.email, "000000", GOOD_PASSWORD, ADDRESS)


async def test_concurrent_right_codes_succeed_exactly_once(harness: Harness) -> None:
    command = await harness.register()
    code = harness.email.codes_for(command.email)[-1]
    results = await asyncio.gather(
        *(
            harness.service.verify_email(command.email, code, GOOD_PASSWORD, ADDRESS)
            for _ in range(4)
        ),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert all(isinstance(result, InvalidCode) for result in results if result is not None)


async def test_wrong_password_never_reveals_pending_status(harness: Harness) -> None:
    command = await harness.register()
    with pytest.raises(InvalidCredentials):
        await harness.service.login(command.username, GOOD_PASSWORD + "x", ADDRESS)
