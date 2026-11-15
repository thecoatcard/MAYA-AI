"""One MCP server child process behind the ``mcp`` SDK (stdio).

This is the only module that imports the SDK, and it does so when a server is started, never at
import time: with the client switched off the SDK is not loaded. The stdio client and the
session are entered and left by ONE long-lived task, because their cancel scopes must be exited
by the task that entered them; calls from other tasks are ordinary awaits on the session.

The child gets exactly the environment it is handed (see ``child_environment``): the SDK adds
only a short default list (PATH and similar), so no other variable of MAYA's process reaches it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, TextIO

logger = logging.getLogger(__name__)

STOP_GRACE_SECONDS: Final = 8.0


class RunnerError(Exception):
    """A server problem, in words that are safe to show. ``crashed`` is True when the child (or
    its pipe) is gone and the next use needs a restart; ``timed_out`` for a call that ran long."""

    def __init__(self, message: str, *, crashed: bool = False, timed_out: bool = False) -> None:
        super().__init__(message)
        self.crashed = crashed
        self.timed_out = timed_out


@dataclass(frozen=True, slots=True)
class ToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]
    hints: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RawResult:
    text: str
    is_error: bool
    truncated: bool


def child_environment(
    parent: Mapping[str, str], allow: tuple[str, ...], injected: Mapping[str, str] | None = None
) -> dict[str, str]:
    """ONLY the allowlisted variables that exist in ``parent``, plus the injected credentials."""
    env = {name: parent[name] for name in allow if name in parent}
    env.update(injected or {})
    return env


def _render(content: list[Any], structured: object, limit: int) -> tuple[str, bool]:
    """Text of a tool result. Images and other binary content are named, never inlined."""
    parts: list[str] = []
    total = 0
    truncated = False
    for block in content:
        kind = getattr(block, "type", "")
        if kind == "text":
            piece = str(getattr(block, "text", ""))
        elif kind == "image":
            piece = f"[image omitted: {getattr(block, 'mime_type', 'image')}]"
        elif kind == "audio":
            piece = "[audio omitted]"
        elif kind == "resource":
            resource = getattr(block, "resource", None)
            text = getattr(resource, "text", None)
            piece = str(text) if isinstance(text, str) else "[binary resource omitted]"
        else:
            piece = f"[{kind or 'content'} omitted]"
        if total + len(piece) > limit:
            parts.append(piece[: max(limit - total, 0)])
            truncated = True
            break
        parts.append(piece)
        total += len(piece) + 1
    if not parts and structured is not None:
        try:
            dumped = json.dumps(structured, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            dumped = ""
        if len(dumped) > limit:
            dumped, truncated = dumped[:limit], True
        parts.append(dumped)
    return "\n".join(parts), truncated


class ServerRunner:
    """Start lazily, list tools, call tools, stop. One child process."""

    def __init__(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        env: dict[str, str],
        start_timeout: float,  # noqa: ASYNC109 - a plain number of seconds, not an asyncio timeout
        max_output_chars: int,
    ) -> None:
        self._command = command
        self._args = list(args)
        self._env = env
        self._start_timeout = start_timeout
        self._max_output = max_output_chars
        self._session: Any = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._failure: str | None = None
        self._errlog: TextIO | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._session is not None and self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start the child and finish the MCP handshake. Safe to call when already running."""
        async with self._lock:
            if self.running:
                return
            await self._cleanup()
            self._ready = asyncio.Event()
            self._stop = asyncio.Event()
            self._failure = None
            self._task = asyncio.create_task(self._serve(), name="mcp-client-server")
            try:
                await asyncio.wait_for(self._ready.wait(), self._start_timeout + 5.0)
            except TimeoutError:
                await self._cleanup()
                raise RunnerError("the tool server did not start in time", crashed=True) from None
            if self._session is None:
                reason = self._failure or "the tool server could not be started"
                await self._cleanup()
                raise RunnerError(reason, crashed=True)

    async def _serve(self) -> None:
        # The SDK is imported here, when a server is really started.
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        try:
            self._errlog = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115, ASYNC230
            params = StdioServerParameters(command=self._command, args=self._args, env=self._env)
            async with (
                stdio_client(params, errlog=self._errlog) as (read, write),
                ClientSession(read, write) as session,
            ):
                await asyncio.wait_for(session.initialize(), self._start_timeout)
                self._session = session
                self._ready.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as error:  # noqa: BLE001 - reported plainly, never with a message
            logger.warning("mcp_client_server_failed error=%s", type(error).__name__)
            if self._failure is None:
                self._failure = (
                    "the tool server did not answer in time"
                    if isinstance(error, TimeoutError)
                    else "the tool server could not be started or stopped unexpectedly"
                )
        finally:
            self._session = None
            self._ready.set()

    async def list_tools(self, timeout: float) -> list[ToolInfo]:  # noqa: ASYNC109
        from mcp_types import PaginatedRequestParams

        session = self._require()
        tools: list[ToolInfo] = []
        cursor: str | None = None
        try:
            for _page in range(10):
                params = PaginatedRequestParams(cursor=cursor) if cursor else None
                result = await asyncio.wait_for(session.list_tools(params=params), timeout)
                for tool in result.tools:
                    hints: dict[str, Any] = {}
                    annotations = getattr(tool, "annotations", None)
                    if annotations is not None:
                        hints = {
                            "destructiveHint": getattr(annotations, "destructive_hint", None),
                            "readOnlyHint": getattr(annotations, "read_only_hint", None),
                        }
                    tools.append(
                        ToolInfo(
                            name=str(tool.name),
                            description=str(tool.description or ""),
                            input_schema=dict(tool.input_schema or {}),
                            hints=hints,
                        )
                    )
                cursor = result.next_cursor
                if not cursor or len(tools) >= 200:
                    break
        except TimeoutError:
            await self.stop()
            raise RunnerError(
                "the tool server took too long to list its tools", timed_out=True
            ) from None
        except Exception as error:  # noqa: BLE001
            await self.stop()
            raise RunnerError(
                "the tool server stopped while listing its tools", crashed=True
            ) from error
        return tools[:200]

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        timeout: float,  # noqa: ASYNC109
    ) -> RawResult:
        session = self._require()
        try:
            result = await asyncio.wait_for(
                session.call_tool(name, dict(arguments), read_timeout_seconds=timeout), timeout
            )
        except (TimeoutError, asyncio.CancelledError) as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            # A call that ran long may leave the server half-way through it: restart it.
            await self.stop()
            raise RunnerError(
                f"the tool took longer than {timeout:g} seconds and was stopped", timed_out=True
            ) from None
        except Exception as error:  # noqa: BLE001
            from mcp import MCPError
            from mcp_types import CONNECTION_CLOSED, REQUEST_TIMEOUT

            if isinstance(error, MCPError) and error.code not in (
                CONNECTION_CLOSED,
                REQUEST_TIMEOUT,
            ):
                # The server answered with a protocol error (bad arguments, unknown tool).
                raise RunnerError(
                    f"the tool server refused the call: {str(error.message)[:200]}"
                ) from None
            await self.stop()
            raise RunnerError(
                "the tool server stopped while running the tool", crashed=True
            ) from None
        content = list(getattr(result, "content", None) or [])
        text, truncated = _render(
            content, getattr(result, "structured_content", None), self._max_output
        )
        return RawResult(
            text=text, is_error=bool(getattr(result, "is_error", False)), truncated=truncated
        )

    def _require(self) -> Any:
        if not self.running:
            raise RunnerError("the tool server is not running", crashed=True)
        return self._session

    async def stop(self) -> None:
        """Close the session and the child. Never raises."""
        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), STOP_GRACE_SECONDS)
            except (TimeoutError, Exception):  # noqa: BLE001
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        await self._cleanup()

    async def _cleanup(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        self._task = None
        self._session = None
        if self._errlog is not None:
            with contextlib.suppress(OSError):
                self._errlog.close()
            self._errlog = None
