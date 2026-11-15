import email
import re
from email import policy
from typing import Any
from unittest.mock import patch

import pytest

from app.auth.messages import code_email
from app.core.email import OutboundEmail, SmtpEmailSender
from app.core.email_templates import DARK, LIGHT, EmailContent, Palette, render_html
from app.core.security_otp import OTP_LIFETIME

MINUTES = int(OTP_LIFETIME.total_seconds() // 60)
SMTP_OPTIONS: dict[str, Any] = {
    "host": "smtp.example.com",
    "port": 587,
    "security": "starttls",
    "username": None,
    "password": None,
    "sender": "MAYA AI <noreply@example.com>",
}


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_every_palette_pair_meets_aa(palette: Palette) -> None:
    for label, fg, bg in palette.contrast_pairs():
        assert contrast(fg, bg) >= 4.5, label


def test_verification_html_has_the_expected_parts() -> None:
    html = code_email("u@example.com", "email_verification", "042917", username="alice").html
    assert html is not None
    assert "042917" in html and "Account username: " in html and "alice" in html
    assert f"{MINUTES} minutes" in html
    assert "color-scheme" in html and "prefers-color-scheme:dark" in html
    assert "display:none" in html  # preheader
    assert "forgot password" in html  # takeover guidance preserved
    assert "Sent by MAYA AI" in html
    assert "042917" not in html.split("<body")[0]  # never in head or title


def test_reset_html_has_no_takeover_guidance() -> None:
    html = code_email("u@example.com", "password_reset", "518304", username="bob").html
    assert html is not None
    assert "518304" in html and "forgot password" not in html


def test_plain_text_body_is_unchanged() -> None:
    message = code_email("u@example.com", "password_reset", "518304", username="bob")
    assert message.body == (
        "Your code to reset your password is 518304.\n\n"
        "The account's username is bob.\n\n"
        "It expires in 10 minutes and works once. "
        "If you did not ask for it, you can ignore this message; nobody can use it without "
        "your account details.\n"
    )
    verification = code_email("u@example.com", "email_verification", "042917", username="a")
    assert "To use it you also enter the password you chose when you registered." in (
        verification.body
    )


@pytest.mark.parametrize(
    "name", ["<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "a&b'c"]
)
def test_hostile_values_are_escaped(name: str) -> None:
    html = code_email("u@example.com", "password_reset", "1<2>3", username=name).html
    assert html is not None
    assert "<script" not in html and "<img" not in html and "onerror=alert(1)>" not in html
    assert "1&lt;2&gt;3" in html
    page = render_html(EmailContent(title=name, preheader=name, paragraphs=(name,), notes=(name,)))
    assert "<script" not in page and "<img" not in page


@pytest.mark.parametrize("purpose", ["email_verification", "password_reset"])
def test_html_is_self_contained_and_small(purpose: Any) -> None:
    html = code_email("u@example.com", purpose, "042917", username="alice").html
    assert html is not None
    assert "http" not in html and "<img" not in html and "<a " not in html
    assert "url(" not in html and "@import" not in html
    assert len(html.encode()) < 30_000
    assert "display:flex" not in html and "display:grid" not in html


def test_forced_dark_drops_the_media_wrapper() -> None:
    content = EmailContent(title="t", preheader="p")
    dark = render_html(content, force_scheme="dark")
    assert "@media (prefers-color-scheme" not in dark
    assert f"background-color:{DARK.page_bg} !important" in dark
    assert "!important" not in render_html(content, force_scheme="light").replace(
        "max-width:600px){.pad{padding:24px 20px !important}}", ""
    )


async def test_smtp_sends_multipart_alternative_with_headers() -> None:
    message = code_email("u@example.com", "email_verification", "042917", username="alice")
    with patch("app.core.email.smtplib.SMTP") as smtp_class:
        smtp = smtp_class.return_value.__enter__.return_value
        await SmtpEmailSender(**SMTP_OPTIONS).send(message)
    sent = smtp.send_message.call_args.args[0]
    parsed = email.message_from_bytes(sent.as_bytes(), policy=policy.default)
    assert parsed.get_content_type() == "multipart/alternative"
    parts = list(parsed.iter_parts())
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert all(p.get_content_charset() == "utf-8" for p in parts)
    assert parts[0].get_content().strip() == message.body.strip()
    assert "042917" in parts[1].get_content()
    assert parsed["Auto-Submitted"] == "auto-generated"
    assert parsed["X-Auto-Response-Suppress"] == "All"
    assert re.fullmatch(r"<.+@example\.com>", parsed["Message-ID"])
    assert parsed["Date"] and parsed["Subject"] == message.subject
    assert parsed["From"] == "MAYA AI <noreply@example.com>"


async def test_plain_only_message_stays_single_part() -> None:
    with patch("app.core.email.smtplib.SMTP") as smtp_class:
        smtp = smtp_class.return_value.__enter__.return_value
        await SmtpEmailSender(**SMTP_OPTIONS).send(
            OutboundEmail(to="u@example.com", subject="s", body="b")
        )
    assert not smtp.send_message.call_args.args[0].is_multipart()
