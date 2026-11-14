"""Transport-level guards that apply before any route code runs."""

from collections.abc import Mapping

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Authentication bodies are a few hundred bytes. Everything is read into memory before
# validation, so an unbounded body is a cheap way to exhaust it.
MAX_BODY_BYTES = 65_536
# A chat message may be long; the model's context budget is the real bound on its text. This
# ceiling only stops abuse. Large documents go through attachments, not the message body.
CHAT_MAX_BODY_BYTES = 4 * 1024 * 1024


class _TooLarge(Exception):
    pass


class MaxBodySizeMiddleware:
    """Reject request bodies over the limit: by Content-Length, or while streaming.

    ``max_bytes`` applies to every route unless ``path_limits`` names a POST path with its own
    limit (for example the chat message route or the file upload route).
    """

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int = MAX_BODY_BYTES,
        *,
        path_limits: Mapping[str, int] | None = None,
    ) -> None:
        self._app = app
        self._max_bytes = max_bytes
        self._path_limits = dict(path_limits or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        limit = self._max_bytes
        if scope.get("method") == "POST":
            limit = self._path_limits.get(str(scope.get("path", "")), self._max_bytes)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._reject(scope, receive, send, limit)
            return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self._app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if started:
                raise
            await self._reject(scope, receive, send, limit)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        kib = limit // 1024
        content = {
            "error": "payload_too_large",
            "message": f"The request body is larger than the {kib} KiB limit for this route.",
        }
        response = JSONResponse(status_code=413, content=content)
        await response(scope, receive, send)
