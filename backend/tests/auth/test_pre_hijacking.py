"""Verifying an address needs the code AND the account's password (pre-hijacking defence)."""

from unittest.mock import patch

import pytest

from app.auth.errors import InvalidCode, InvalidCredentials
from tests.support.auth import ADDRESS, GOOD_PASSWORD, NEW_PASSWORD, Harness


async def test_a_wrong_password_looks_like_a_wrong_code_and_burns_no_attempt(
    harness: Harness,
) -> None:
    command = await harness.register()
    code = harness.email.codes_for(command.email)[-1]
    for _ in range(10):  # far more than the five code attempts, all with the wrong password
        with pytest.raises(InvalidCode):
            await harness.service.verify_email(command.email, code, "not the password!!", ADDRESS)
    await harness.service.verify_email(command.email, code, GOOD_PASSWORD, ADDRESS)
    assert (await harness.login(command)).access_token


async def test_the_right_password_does_not_help_without_the_right_code(harness: Harness) -> None:
    command = await harness.register()
    code = harness.email.codes_for(command.email)[-1]
    wrong = "000000" if code != "000000" else "000001"
    for _ in range(5):
        with pytest.raises(InvalidCode):
            await harness.service.verify_email(command.email, wrong, GOOD_PASSWORD, ADDRESS)
    with pytest.raises(InvalidCode):  # the code is dead after five wrong guesses
        await harness.service.verify_email(command.email, code, GOOD_PASSWORD, ADDRESS)


async def test_verification_failures_all_look_the_same(harness: Harness) -> None:
    command = await harness.register()
    code = harness.email.codes_for(command.email)[-1]
    messages = set()
    for email, guess, password in (
        (command.email, code, "not the password!!"),
        (command.email, "000000" if code != "000000" else "000001", GOOD_PASSWORD),
        ("nobody.at.all@example.com", code, GOOD_PASSWORD),
    ):
        with pytest.raises(InvalidCode) as caught:
            await harness.service.verify_email(email, guess, password, ADDRESS)
        messages.add(str(caught.value))
    assert len(messages) == 1


@pytest.mark.parametrize("target", ["unknown", "verified"])
async def test_an_unusable_target_still_costs_a_password_verification(
    harness: Harness, target: str
) -> None:
    email = "nobody.at.all@example.com"
    if target == "verified":
        email = (await harness.register_and_verify()).email
    with patch.object(
        harness.passwords, "verify_unknown_account", wraps=harness.passwords.verify_unknown_account
    ) as spy:
        with pytest.raises(InvalidCode):
            await harness.service.verify_email(email, "123456", GOOD_PASSWORD, ADDRESS)
    spy.assert_awaited_once()


async def test_someone_who_pre_registered_a_victims_address_does_not_end_up_owning_it(
    harness: Harness,
) -> None:
    """The attack: register the victim's address with the attacker's own password."""
    attacker = await harness.register()  # the code goes to the victim's mailbox
    harness.clock.advance(seconds=61)
    victim = harness.command(email=attacker.email, password=NEW_PASSWORD)
    message = await harness.service.register(victim, ADDRESS)  # a duplicate: resends a code
    assert message is not None
    await harness.service.deliver(message)
    code = harness.email.codes_for(attacker.email)[-1]

    # The victim confirms the code with the password they chose; it is not the squatter's.
    with pytest.raises(InvalidCode):
        await harness.service.verify_email(attacker.email, code, NEW_PASSWORD, ADDRESS)
    # The squatter cannot confirm either: the code only exists in the victim's mailbox.
    with pytest.raises(InvalidCode):
        await harness.service.verify_email(attacker.email, "000000", attacker.password, ADDRESS)
    with pytest.raises(InvalidCredentials):  # and an unverified account cannot log in at all
        await harness.service.login("nobody_at_all", attacker.password, ADDRESS)

    # The mailbox owner takes the account over by password recovery. The e-mail tells them the
    # username, which the squatter chose.
    harness.clock.advance(seconds=61)
    reset = await harness.service.request_password_reset(attacker.email, ADDRESS)
    assert reset is not None
    await harness.service.deliver(reset)
    assert f"username is {attacker.username}" in reset.body
    reset_code = harness.email.codes_for(attacker.email)[-1]
    await harness.service.reset_password(attacker.email, reset_code, NEW_PASSWORD, ADDRESS)

    pair = await harness.service.login(attacker.username, NEW_PASSWORD, ADDRESS)
    assert pair.access_token
    with pytest.raises(InvalidCredentials):  # the squatter's password no longer works
        await harness.service.login(attacker.username, attacker.password, ADDRESS)


async def test_the_verification_message_tells_the_owner_about_the_password_and_username(
    harness: Harness,
) -> None:
    command = harness.command()
    message = await harness.service.register(command, ADDRESS)
    assert message is not None
    assert "password you chose" in message.body
    assert command.username in message.body
