"""The two system mails of the scheduler. Built for MAYA's own SMTP sender only.

NEVER send these through ``app.mail`` (that is the user's own mailbox, ADR-0021).
"""

from __future__ import annotations

from datetime import date

from app.core.email import OutboundEmail
from app.core.email_templates import (
    EmailContent,
    ReportMail,
    render_html,
    render_report_html,
    render_report_text,
)

CODE_MINUTES = 10
ARTIFACT_NOTE = "A file with the full report was saved in Artifacts."


def date_label(day: date) -> str:
    return f"{day.strftime('%A')}, {day.day} {day.strftime('%B %Y')}"


def report_email(
    *,
    to: str,
    schedule_name: str,
    day: date,
    markdown: str,
    account_email: str,
    file_made: bool,
    notes: tuple[str, ...] = (),
) -> OutboundEmail:
    label = date_label(day)
    title = f"{schedule_name} — {label}"
    first_line = next(
        (
            line.strip(" #*-")
            for line in markdown.splitlines()
            if line.strip() and not line.lstrip().startswith("# ")
        ),
        "Your scheduled report from MAYA AI.",
    )
    mail = ReportMail(
        title=title,
        date_label=label,
        preheader=first_line[:140],
        markdown=markdown,
        account_email=account_email,
        artifact_note=ARTIFACT_NOTE if file_made else None,
        notes=notes,
    )
    return OutboundEmail(
        to=to,
        subject=title[:200],
        body=render_report_text(mail),
        html=render_report_html(mail),
    )


def recipient_code_email(*, to: str, code: str, account_email: str) -> OutboundEmail:
    """The one-time code that lets scheduled reports be sent to ``to``."""
    lifetime = f"It expires in {CODE_MINUTES} minutes and works once."
    safety = (
        f"The account {account_email} wants to send scheduled MAYA AI reports to this address. "
        "If you did not expect this, ignore the message: nothing is sent to you without the code."
    )
    body = (
        f"Your code to confirm {to} as a recipient of scheduled MAYA AI reports is {code}.\n\n"
        f"{safety}\n\n{lifetime}\n"
    )
    content = EmailContent(
        title="Confirm this address",
        preheader=f"Use this code to receive scheduled reports. It expires in {CODE_MINUTES} min.",
        paragraphs=("Use this code to confirm that you want scheduled MAYA AI reports here.",),
        code=code,
        notes=(f"{lifetime} {safety}",),
        footer_reason=(
            "You received this e-mail because someone asked to send scheduled reports to this "
            "address."
        ),
    )
    return OutboundEmail(
        to=to,
        subject="Your MAYA AI recipient confirmation code",
        body=body,
        html=render_html(content),
    )
