from app.core.email import OutboundEmail
from app.core.security_otp import OTP_LIFETIME, OtpPurpose

_MINUTES = int(OTP_LIFETIME.total_seconds() // 60)


def code_email(to: str, purpose: OtpPurpose, code: str, *, username: str) -> OutboundEmail:
    if purpose == "email_verification":
        subject = "Your MAYA AI verification code"
        action = "verify your e-mail address"
        extra = (
            "To use it you also enter the password you chose when you registered. If that "
            "password is not accepted, this address may have been registered by someone else: "
            "use 'forgot password' to take the account over.\n\n"
        )
    else:
        subject = "Your MAYA AI password reset code"
        action = "reset your password"
        extra = ""
    body = (
        f"Your code to {action} is {code}.\n\n"
        f"The account's username is {username}.\n\n"
        f"{extra}"
        f"It expires in {_MINUTES} minutes and works once. "
        "If you did not ask for it, you can ignore this message; nobody can use it without "
        "your account details.\n"
    )
    return OutboundEmail(to=to, subject=subject, body=body)
