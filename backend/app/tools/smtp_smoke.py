"""User-run check that the configured SMTP account can really deliver one message.

**Agents must not run this.** It contacts a real mail server with the credentials in a private
environment file that agents are not allowed to read. The owner runs it by hand:

    uv run --directory backend python -m app.tools.smtp_smoke --env-file ..\\.env

It sends exactly one fixed test message (no one-time code) to an address you confirm, prints only
whether the server accepted it and a short failure category, and never prints a credential.
Exit status: 0 delivered, 1 delivery failed, 2 not run (bad configuration or you declined).
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.auth.identifiers import normalize_email
from app.core.config import SENDER_ADDRESS
from app.core.email import EmailDeliveryError, OutboundEmail, SmtpEmailSender

SUBJECT = "MAYA AI SMTP check"
BODY = (
    "This is a one-off test message from the MAYA AI SMTP check.\n"
    "It contains no code and no account information; you can delete it.\n"
)


class SmtpSmokeSettings(BaseSettings):
    """Only the SMTP settings; the database, Redis and key settings are not needed here."""

    model_config = SettingsConfigDict(
        env_prefix="MAYA_", env_file=None, extra="ignore", hide_input_in_errors=True
    )

    smtp_host: str = Field(min_length=1, max_length=253)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: str = "starttls"
    smtp_username: str | None = Field(default=None, min_length=1, max_length=255)
    smtp_password: SecretStr | None = None
    smtp_from_address: str = Field(max_length=320)

    @field_validator("smtp_from_address")
    @classmethod
    def _sender(cls, value: str) -> str:
        if SENDER_ADDRESS.fullmatch(value) is None:
            raise ValueError("invalid")
        return value

    @field_validator("smtp_security")
    @classmethod
    def _security(cls, value: str) -> str:
        if value not in ("starttls", "ssl"):
            raise ValueError("invalid")
        return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send one SMTP test message (run by the owner)")
    parser.add_argument("--env-file", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if not arguments.env_file.is_file():
        print("The environment file does not exist.")
        return 2
    try:
        settings = SmtpSmokeSettings(_env_file=arguments.env_file)
    except ValidationError as error:
        names = sorted({str(problem["loc"][0]).upper() for problem in error.errors()})
        print("SMTP settings are missing or invalid: " + ", ".join(f"MAYA_{n}" for n in names))
        return 2
    if (settings.smtp_username is None) != (settings.smtp_password is None):
        print("Set MAYA_SMTP_USERNAME and MAYA_SMTP_PASSWORD together.")
        return 2

    suggestion = settings.smtp_from_address
    answer = input(f"Send the test message to which address? [{suggestion}] ").strip()
    try:
        recipient = normalize_email(answer or suggestion)
    except ValueError:
        print("That is not a usable e-mail address.")
        return 2
    print(
        f"About to send ONE test message via {settings.smtp_host}:{settings.smtp_port} "
        f"({settings.smtp_security}) to {recipient}."
    )
    if input("Type 'yes' to send: ").strip().lower() != "yes":
        print("Cancelled; nothing was sent.")
        return 2

    sender = SmtpEmailSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        security="ssl" if settings.smtp_security == "ssl" else "starttls",
        username=settings.smtp_username,
        password=None
        if settings.smtp_password is None
        else settings.smtp_password.get_secret_value(),
        sender=settings.smtp_from_address,
    )
    try:
        asyncio.run(sender.send(OutboundEmail(to=recipient, subject=SUBJECT, body=BODY)))
    except EmailDeliveryError as error:
        print(
            f"Delivery failed ({error.category}). Check host, port, security mode and credentials."
        )
        return 1
    print("The SMTP server accepted the message. Check the inbox (and spam) to confirm it arrived.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
