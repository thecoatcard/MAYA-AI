from app.core.email import OutboundEmail
from app.core.email_templates import EmailContent, render_html
from app.core.security_otp import OTP_LIFETIME, OtpPurpose

_MINUTES = int(OTP_LIFETIME.total_seconds() // 60)
_TAKEOVER_GUIDANCE = (
    "To use it you also enter the password you chose when you registered. If that "
    "password is not accepted, this address may have been registered by someone else: "
    "use 'forgot password' to take the account over."
)
_SECURITY_NOTE = (
    "If you did not ask for it, you can ignore this message; nobody can use it without "
    "your account details."
)


def code_email(to: str, purpose: OtpPurpose, code: str, *, username: str) -> OutboundEmail:
    subject, body, content = code_message(purpose, code, username=username)
    return OutboundEmail(to=to, subject=subject, body=body, html=render_html(content))


def code_message(purpose: OtpPurpose, code: str, *, username: str) -> tuple[str, str, EmailContent]:
    """Subject, plain-text body and HTML content for a one-time-code mail."""
    if purpose == "email_verification":
        subject = "Your MAYA AI verification code"
        action = "verify your e-mail address"
        title = "Verify your e-mail address"
        takeover: str | None = _TAKEOVER_GUIDANCE
        reason = (
            "You received this e-mail because this address was used to register a MAYA AI account."
        )
    else:
        subject = "Your MAYA AI password reset code"
        action = "reset your password"
        title = "Reset your password"
        takeover = None
        reason = (
            "You received this e-mail because a password reset was requested for a MAYA AI account."
        )
    lifetime = f"It expires in {_MINUTES} minutes and works once."
    takeover_text = f"{takeover}\n\n" if takeover else ""
    body = (
        f"Your code to {action} is {code}.\n\n"
        f"The account's username is {username}.\n\n"
        f"{takeover_text}"
        f"{lifetime} {_SECURITY_NOTE}\n"
    )
    content = EmailContent(
        title=title,
        preheader=f"Use your one-time code to {action}. It expires in {_MINUTES} minutes.",
        paragraphs=(f"Use this code to {action}.",),
        code=code,
        extra=(takeover,) if takeover else (),
        notes=(f"{lifetime} {_SECURITY_NOTE}",),
        username=username,
        footer_reason=reason,
    )
    return subject, body, content
