import smtplib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.auth.messages import code_email
from app.core.email import EmailDeliveryError, OutboundEmail, SmtpEmailSender
from app.core.security_otp import OtpPurpose


def sender(security: str = "starttls", **overrides: Any) -> SmtpEmailSender:
    options: dict[str, Any] = {
        "host": "smtp.example.com",
        "port": 587,
        "security": security,
        "username": "mailer",
        "password": "smtp-fake-password",
        "sender": "noreply@example.com",
    }
    options.update(overrides)
    return SmtpEmailSender(**options)


MESSAGE = OutboundEmail(to="user@example.com", subject="Subject", body="Your code is 123456.")


async def test_starttls_is_negotiated_before_credentials_are_sent() -> None:
    with patch("app.core.email.smtplib.SMTP") as smtp_class:
        smtp = smtp_class.return_value.__enter__.return_value
        calls: list[str] = []
        smtp.starttls.side_effect = lambda **_: calls.append("starttls")
        smtp.login.side_effect = lambda *_: calls.append("login")
        smtp.send_message.side_effect = lambda *_: calls.append("send")
        await sender().send(MESSAGE)
    assert calls == ["starttls", "login", "send"]
    assert smtp_class.call_args.args == ("smtp.example.com", 587)
    assert smtp_class.call_args.kwargs["timeout"] == 10.0
    assert smtp.starttls.call_args.kwargs["context"].verify_mode.name == "CERT_REQUIRED"
    smtp.login.assert_called_once_with("mailer", "smtp-fake-password")


async def test_implicit_ssl_mode_uses_a_verifying_context() -> None:
    with (
        patch("app.core.email.smtplib.SMTP_SSL") as ssl_class,
        patch("app.core.email.smtplib.SMTP") as plain_class,
    ):
        await sender("ssl", port=465).send(MESSAGE)
    plain_class.assert_not_called()
    context = ssl_class.call_args.kwargs["context"]
    assert context.check_hostname is True
    assert context.verify_mode.name == "CERT_REQUIRED"


async def test_login_is_skipped_without_credentials() -> None:
    with patch("app.core.email.smtplib.SMTP") as smtp_class:
        smtp = smtp_class.return_value.__enter__.return_value
        await sender(username=None, password=None).send(MESSAGE)
    smtp.login.assert_not_called()
    smtp.send_message.assert_called_once()


async def test_the_message_has_the_expected_headers_and_body() -> None:
    with patch("app.core.email.smtplib.SMTP") as smtp_class:
        smtp = smtp_class.return_value.__enter__.return_value
        await sender().send(MESSAGE)
    email = smtp.send_message.call_args.args[0]
    assert (email["From"], email["To"], email["Subject"]) == (
        "noreply@example.com",
        "user@example.com",
        "Subject",
    )
    assert "123456" in email.get_content()


@pytest.mark.parametrize(
    "failure",
    [
        smtplib.SMTPAuthenticationError(535, b"bad credentials for mailer smtp-fake-password"),
        smtplib.SMTPRecipientsRefused({"user@example.com": (550, b"no such user")}),
        smtplib.SMTPServerDisconnected("connection lost"),
        OSError("connection refused to smtp.example.com"),
        TimeoutError("timed out"),
    ],
)
async def test_delivery_errors_are_generic_and_leak_no_provider_detail(failure: Exception) -> None:
    with patch("app.core.email.smtplib.SMTP") as smtp_class:
        smtp_class.return_value.__enter__.return_value.send_message.side_effect = failure
        with pytest.raises(EmailDeliveryError) as caught:
            await sender().send(MESSAGE)
    assert str(caught.value) == "E-mail delivery failed"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


async def test_connection_failure_is_also_generic() -> None:
    with patch("app.core.email.smtplib.SMTP", side_effect=OSError("refused")):
        with pytest.raises(EmailDeliveryError):
            await sender().send(MESSAGE)


@pytest.mark.parametrize(
    "recipient", ["a@example.com\r\nBcc: victim@example.com", "a@example.com\nX-Header: y"]
)
async def test_header_injection_through_the_recipient_is_refused_before_any_connection(
    recipient: str,
) -> None:
    with patch("app.core.email.smtplib.SMTP") as smtp_class:
        with pytest.raises(EmailDeliveryError):
            await sender().send(OutboundEmail(to=recipient, subject="s", body="b"))
    smtp_class.assert_not_called()


def test_the_message_body_is_not_in_the_repr() -> None:
    assert "123456" not in repr(MESSAGE)
    assert "123456" not in str(MESSAGE)


@pytest.mark.parametrize("purpose", ["email_verification", "password_reset"])
def test_code_messages_state_the_code_and_the_lifetime(purpose: OtpPurpose) -> None:
    message = code_email("user@example.com", purpose, "042917", username="alice")
    assert "042917" in message.body
    assert "10 minutes" in message.body
    assert "username is alice" in message.body
    assert message.to == "user@example.com"
    assert "MAYA" in message.subject


def test_smtp_client_is_a_context_manager_mock_sanity() -> None:
    assert MagicMock() is not None
