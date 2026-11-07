"""One JSON error shape, ``{"error": "<code>"}``, with no echo of what the client sent."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth.errors import (
    InvalidCode,
    InvalidCredentials,
    InvalidToken,
    NotAuthenticated,
    RateLimited,
    ServiceUnavailable,
)
from app.core.security import Overloaded, PasswordPolicyViolation
from app.db.repositories.chat import InvalidCursor, SessionNotFound
from app.services.chat_service import (
    ChatError,
    ChatStreamLimitReached,
    ChatUnavailable,
    ChatValidationError,
    IdempotencyKeyRequired,
    IdempotencyKeyReused,
    ModelUnavailable,
    RequestInProgress,
)

BEARER = {"WWW-Authenticate": "Bearer"}
_REQUEST_PARTS = frozenset({"body", "query", "path"})


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
    if isinstance(exc, InvalidToken):
        return _error(401, "invalid_token", BEARER)
    if isinstance(exc, NotAuthenticated):
        return _error(401, "not_authenticated", BEARER)
    if isinstance(exc, ServiceUnavailable | Overloaded):
        return _error(503, "service_unavailable")
    raise exc


async def _chat_error(request: Request, exc: Exception) -> JSONResponse:
    """The documented answers of ``/api/v1/chat`` (docs/api/CHAT.md). None carries request data."""
    if isinstance(exc, ChatValidationError):
        return JSONResponse(
            status_code=422, content={"error": "validation_error", "fields": list(exc.fields)}
        )
    if isinstance(exc, InvalidCursor):
        return JSONResponse(
            status_code=422, content={"error": "validation_error", "fields": ["cursor"]}
        )
    if isinstance(exc, SessionNotFound):
        return _error(404, "session_not_found")
    if isinstance(exc, IdempotencyKeyRequired):
        return _error(400, "idempotency_key_required")
    if isinstance(exc, RequestInProgress):
        return _error(409, "request_in_progress")
    if isinstance(exc, ChatStreamLimitReached):
        return _error(429, "concurrent_stream_limit", {"Retry-After": "1"})
    if isinstance(exc, IdempotencyKeyReused):
        return _error(422, "idempotency_key_reused")
    if isinstance(exc, ModelUnavailable):
        return _error(503, "model_unavailable")
    if isinstance(exc, ChatUnavailable):
        return _error(503, "chat_unavailable")
    raise exc


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Name the offending fields only; never repeat the submitted values (passwords, codes)."""
    fields: list[str] = []
    if isinstance(exc, RequestValidationError):
        for problem in exc.errors():
            location = [str(part) for part in problem.get("loc", ())]
            if location and location[0] in _REQUEST_PARTS:  # where it was sent, not a field name
                location = location[1:]
            name = ".".join(location) or "body"
            if name not in fields:
                fields.append(name)
    return JSONResponse(status_code=422, content={"error": "validation_error", "fields": fields})


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    headers = dict(exc.headers) if exc.headers else None
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=headers)
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    return _error(exc.status_code, code, headers)


def install_error_handlers(app: FastAPI) -> None:
    for error in (
        RateLimited,
        PasswordPolicyViolation,
        InvalidCode,
        InvalidCredentials,
        InvalidToken,
        NotAuthenticated,
        ServiceUnavailable,
        Overloaded,
    ):
        app.add_exception_handler(error, _domain_error)
    for chat_error in (ChatError, InvalidCursor, SessionNotFound):
        app.add_exception_handler(chat_error, _chat_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
