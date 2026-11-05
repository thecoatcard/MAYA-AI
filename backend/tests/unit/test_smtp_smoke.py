"""The owner-run SMTP check, with the mail library and the keyboard mocked."""

import smtplib
import ssl
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.email import EmailDeliveryError, OutboundEmail, SmtpEmailSender, classify
from app.tools import smtp_smoke

PASSWORD = "fake-smtp-password-for-tests"
USERNAME = "fake-mailer-login"


def write_env(tmp_path: Path, **overrides: str | None) -> Path:
    values: dict[str, str | None] = {
        "MAYA_SMTP_HOST": "smtp.example.com",
        "MAYA_SMTP_PORT": "587",
        "MAYA_SMTP_SECURITY": "starttls",
        "MAYA_SMTP_USERNAME": USERNAME,
        "MAYA_SMTP_PASSWORD": PASSWORD,
        "MAYA_SMTP_FROM_ADDRESS": "mailer@example.com",
    }
    values.update(overrides)
    path = tmp_path / "private.env"
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items() if v is not None) + "\n")
    return path


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in list(os.environ):
        if name.startswith("MAYA_"):
            monkeypatch.delenv(name)


def answers(monkeypatch: pytest.MonkeyPatch, *replies: str) -> list[str]:
    asked: list[str] = []
    queue = list(replies)

    def fake_input(prompt: str = "") -> str:
        asked.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return asked


def test_a_confirmed_run_sends_exactly_one_fixed_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers(monkeypatch, "", "yes")
    sent: list[OutboundEmail] = []

    async def record(self: SmtpEmailSender, message: OutboundEmail) -> None:
        sent.append(message)

    with patch.object(SmtpEmailSender, "send", record):
        status = smtp_smoke.main(["--env-file", str(write_env(tmp_path))])
    assert status == 0
    assert [(m.to, m.subject, m.body) for m in sent] == [
        ("mailer@example.com", smtp_smoke.SUBJECT, smtp_smoke.BODY)
    ]
    assert not any(char.isdigit() for char in smtp_smoke.BODY)  # no code of any kind
    output = capsys.readouterr().out
    assert "accepted" in output
    for secret in (PASSWORD, USERNAME):
        assert secret not in output


def test_another_recipient_can_be_chosen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers(monkeypatch, "Someone.Else@Example.com", "yes")
    sent: list[OutboundEmail] = []

    async def record(self: SmtpEmailSender, message: OutboundEmail) -> None:
        sent.append(message)

    with patch.object(SmtpEmailSender, "send", record):
        assert smtp_smoke.main(["--env-file", str(write_env(tmp_path))]) == 0
    assert [m.to for m in sent] == ["someone.else@example.com"]


@pytest.mark.parametrize("reply", ["", "no", "y", "YES please", "yes!"])
def test_anything_but_a_plain_yes_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    answers(monkeypatch, "", reply)
    with patch.object(SmtpEmailSender, "send") as send:
        assert smtp_smoke.main(["--env-file", str(write_env(tmp_path))]) == 2
    send.assert_not_called()


def test_a_bad_recipient_stops_before_the_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = answers(monkeypatch, "not-an-address")
    with patch.object(SmtpEmailSender, "send") as send:
        assert smtp_smoke.main(["--env-file", str(write_env(tmp_path))]) == 2
    send.assert_not_called()
    assert len(asked) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"MAYA_SMTP_HOST": None},
        {"MAYA_SMTP_FROM_ADDRESS": None},
        {"MAYA_SMTP_FROM_ADDRESS": "not-an-address"},
        {"MAYA_SMTP_PORT": "70000"},
        {"MAYA_SMTP_SECURITY": "plain"},
        {"MAYA_SMTP_PASSWORD": None},
        {"MAYA_SMTP_USERNAME": None},
    ],
)
def test_incomplete_or_unsafe_settings_do_nothing_and_leak_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, str | None],
) -> None:
    asked = answers(monkeypatch)
    with patch.object(SmtpEmailSender, "send") as send:
        assert smtp_smoke.main(["--env-file", str(write_env(tmp_path, **overrides))]) == 2
    send.assert_not_called()
    assert asked == []  # it never even asks
    output = capsys.readouterr().out
    for secret in (PASSWORD, USERNAME, "smtp.example.com"):
        assert secret not in output


def test_a_missing_environment_file_is_reported_not_guessed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert smtp_smoke.main(["--env-file", str(tmp_path / "absent.env")]) == 2
    assert "does not exist" in capsys.readouterr().out


def test_a_failed_delivery_reports_only_the_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers(monkeypatch, "", "yes")
    failure = smtplib.SMTPAuthenticationError(535, f"bad login {USERNAME} {PASSWORD}".encode())
    with patch("app.core.email.smtplib.SMTP") as smtp_class:
        smtp_class.return_value.__enter__.return_value.login.side_effect = failure
        status = smtp_smoke.main(["--env-file", str(write_env(tmp_path))])
    assert status == 1
    output = capsys.readouterr().out
    assert "authentication" in output
    for secret in (PASSWORD, USERNAME):
        assert secret not in output


def test_the_real_sender_is_used_with_tls_and_the_configured_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers(monkeypatch, "", "yes")
    with patch("app.core.email.smtplib.SMTP") as smtp_class:
        smtp = smtp_class.return_value.__enter__.return_value
        assert smtp_smoke.main(["--env-file", str(write_env(tmp_path))]) == 0
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with(USERNAME, PASSWORD)
    smtp.send_message.assert_called_once()


# ------------------------------------------------------------- failure categories


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (smtplib.SMTPAuthenticationError(535, b"x"), "authentication"),
        (ssl.SSLError("x"), "tls"),
        (smtplib.SMTPNotSupportedError("x"), "tls"),
        (smtplib.SMTPRecipientsRefused({}), "refused"),
        (smtplib.SMTPSenderRefused(550, b"x", "a@b.c"), "refused"),
        (smtplib.SMTPServerDisconnected("x"), "connection"),
        (smtplib.SMTPConnectError(421, b"x"), "connection"),
        (ConnectionRefusedError("x"), "connection"),
        (TimeoutError("x"), "connection"),
        (ValueError("header"), "message"),
        (smtplib.SMTPDataError(554, b"x"), "smtp"),
    ],
)
def test_failures_are_classified_by_type_never_by_text(error: Exception, category: str) -> None:
    assert classify(error) == category


async def test_the_sender_attaches_the_category_without_changing_the_message() -> None:
    sender = SmtpEmailSender(
        host="smtp.example.com",
        port=587,
        security="starttls",
        username="u",
        password="p",
        sender="a@example.com",
    )
    with patch("app.core.email.smtplib.SMTP", side_effect=ConnectionRefusedError("secret detail")):
        with pytest.raises(EmailDeliveryError) as caught:
            await sender.send(OutboundEmail(to="b@example.com", subject="s", body="b"))
    assert caught.value.category == "connection"
    assert str(caught.value) == "E-mail delivery failed"
    assert "secret detail" not in repr(caught.value)
