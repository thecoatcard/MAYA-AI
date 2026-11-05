"""One JSON error shape, ``{"error": "<code>"}``, with no echo of what the client sent."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth.errors import (
    EmailNotVerified,
    InvalidCode,
    InvalidCredentials,
    InvalidToken,
    NotAuthenticated,
    RateLimited,
    ServiceUnavailable,
)
from app.core.security import Overloaded, PasswordPolicyViolation

BEARER = {"WWW-Authenticate": "Bearer"}


def _error(status: int, code: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code}, headers=headers)


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, RateLimited):
        return _error(429, "rate_limited", {"Retry-After": str(exc.retry_after)})
    if isinstance(exc, PasswordPolicyViolation):
        return _error(422, exc.code)
    if isinstance(exc, InvalidCode):
        return _error(400, "invalid_or_expired_code")
    if isinstance(exc, InvalidCredentials):
        return _error(401, "invalid_credentials")
    if isinstance(exc, EmailNotVerified):
        return _error(403, "email_not_verified")
    if isinstance(exc, InvalidToken):
        return _error(401, "invalid_token", BEARER)
    if isinstance(exc, NotAuthenticated):
        return _error(401, "not_authenticated", BEARER)
    if isinstance(exc, ServiceUnavailable | Overloaded):
        return _error(503, "service_unavailable")
    raise exc


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Name the offending fields only; never repeat the submitted values (passwords, codes)."""
    fields: list[str] = []
    if isinstance(exc, RequestValidationError):
        for problem in exc.errors():
            location = [str(part) for part in problem.get("loc", ()) if part != "body"]
            name = ".".join(location) or "body"
            if name not in fields:
                fields.append(name)
    return JSONResponse(status_code=422, content={"error": "validation_error", "fields": fields})


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    return _error(exc.status_code, code, dict(exc.headers) if exc.headers else None)


def install_error_handlers(app: FastAPI) -> None:
    for error in (
        RateLimited,
        PasswordPolicyViolation,
        InvalidCode,
        InvalidCredentials,
        EmailNotVerified,
        InvalidToken,
        NotAuthenticated,
        ServiceUnavailable,
        Overloaded,
    ):
        app.add_exception_handler(error, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
