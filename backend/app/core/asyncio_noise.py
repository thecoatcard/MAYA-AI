"""Quiet the asyncio proactor tracebacks that a client closing a stream produces on Windows.

When a browser or client closes an SSE stream, the Windows proactor event loop can raise
``ConnectionAbortedError`` / ``ConnectionResetError`` (WinError 10053 / 10054) from
``_ProactorBasePipeTransport._call_connection_lost``. Nothing is wrong: the peer went away. The
default handler prints a full traceback for each (26 in one live run); this handler logs one DEBUG
line instead and leaves every other loop exception to the default handler.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_PEER_GONE_WINERRORS = frozenset({10053, 10054})


def is_peer_gone_noise(context: dict[str, Any]) -> bool:
    """True for a connection abort/reset raised by a transport callback (not by our own code)."""
    exc = context.get("exception")
    if not isinstance(exc, (ConnectionAbortedError, ConnectionResetError)):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror is not None and winerror not in _PEER_GONE_WINERRORS:
        return False
    where = (
        f"{context.get('message', '')} {context.get('handle', '')} {context.get('transport', '')}"
    )
    return "_call_connection_lost" in where or "Proactor" in where or "transport" in where.lower()


def connection_noise_handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    """Loop exception handler: a peer-gone transport error is one DEBUG line, the rest default."""
    if is_peer_gone_noise(context):
        exc = context["exception"]
        logger.debug("peer_closed_connection error=%s", type(exc).__name__)
        return
    loop.default_exception_handler(context)


def install_connection_noise_handler(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Install :func:`connection_noise_handler` on ``loop`` (default: the running loop)."""
    (loop or asyncio.get_running_loop()).set_exception_handler(connection_noise_handler)
