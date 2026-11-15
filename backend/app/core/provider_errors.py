"""Recognise provider quota / overload failures behind any wrapper, and log them safely.

Only exception types and HTTP status codes are inspected, never message text (provider text can
echo request content). The gateway wraps an exhausted retry chain in
``GatewayFallbackExhaustedError`` with the provider error as ``__cause__``; every helper here walks
that chain.
"""

from __future__ import annotations

from google.genai import errors as genai_errors

# 429 quota, 408 request timeout, 503 provider overloaded: all are "wait and try again".
RATE_LIMIT_STATUS_CODES = (408, 429, 503)


def _chain(exc: BaseException | None) -> list[BaseException]:
    seen: set[int] = set()
    out: list[BaseException] = []
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        out.append(exc)
        exc = exc.__cause__ or exc.__context__
    return out


def is_rate_limited(exc: BaseException | None) -> bool:
    """True when ``exc``, or anything it was raised from, is a provider 429/408/503 error."""
    return any(
        isinstance(e, genai_errors.APIError) and e.code in RATE_LIMIT_STATUS_CODES
        for e in _chain(exc)
    )


_TRANSPORT_NAMES = frozenset(
    {"TransportError", "NetworkError", "ConnectError", "ConnectionError", "ConnectTimeout"}
    | {"RemoteProtocolError", "ReadError", "ClientConnectionError", "ClientConnectorError"}
    | {"ClientOSError", "ServerDisconnectedError", "gaierror"}
)


def failure_cause(exc: BaseException | None) -> str:
    """A coarse, plain cause of a provider call failure, from types and status codes only.

    One of ``rate_limited`` (429/408/503), ``server`` (5xx), ``network`` (DNS, connection,
    transport), ``timeout`` or ``unknown``. Walks the whole ``__cause__`` chain, so a gateway
    wrapper that exhausted its keys on a DNS failure is still reported as ``network``, not as quota.
    """
    chain = _chain(exc)
    for e in chain:
        if isinstance(e, genai_errors.APIError) and isinstance(e.code, int):
            if e.code in RATE_LIMIT_STATUS_CODES:
                return "rate_limited"
            if 500 <= e.code < 600:
                return "server"
    for e in chain:
        names = {cls.__name__ for cls in type(e).__mro__}
        if isinstance(e, TimeoutError) or any("timeout" in n.lower() for n in names):
            return "timeout"
        if isinstance(e, OSError) or names & _TRANSPORT_NAMES:
            return "network"
    return "unknown"


def provider_error_info(exc: BaseException | None) -> tuple[str, int | None]:
    """``(error class or provider status name, HTTP status)`` of the first provider error.

    ``("ServerError", 503)`` or ``("RESOURCE_EXHAUSTED", 429)`` style values; for a non-provider
    error the exception class name and ``None``. Safe to log: never contains message text.
    """
    for e in _chain(exc):
        if isinstance(e, genai_errors.APIError):
            code = e.code if isinstance(e.code, int) else None
            status = getattr(e, "status", None)
            name = status if isinstance(status, str) and status.isupper() else type(e).__name__
            return name, code
    return (type(exc).__name__ if exc is not None else "none"), None
