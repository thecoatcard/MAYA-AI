"""Domain errors. Each maps to one generic HTTP answer; none carries account details."""


class RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Rate limited")
        self.retry_after = retry_after


class ServiceUnavailable(Exception):
    """E-mail is not configured or the rate limiter is down; the request fails closed."""

    def __init__(self) -> None:
        super().__init__("Service unavailable")


class InvalidCode(Exception):
    """Wrong, expired, used or unknown one-time code: one answer for all of them."""

    def __init__(self) -> None:
        super().__init__("Invalid or expired code")


class InvalidCredentials(Exception):
    def __init__(self) -> None:
        super().__init__("Invalid credentials")


class EmailNotVerified(Exception):
    def __init__(self) -> None:
        super().__init__("E-mail address not verified")


class InvalidToken(Exception):
    def __init__(self) -> None:
        super().__init__("Invalid token")


class NotAuthenticated(Exception):
    def __init__(self) -> None:
        super().__init__("Not authenticated")
