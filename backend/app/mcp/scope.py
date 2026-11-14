"""Who a tool call runs for (workspace and task), carried in a context variable.

A tool handler only receives the model's arguments, and secrets or identifiers must never be put
in them. The worker sets this scope around ``McpGateway.invoke`` so a handler that makes a billable
model call (``web_search``) can pass the workspace and task to the gateway for cost tracking and
budget admission. asyncio copies the context into the tasks it creates, so the handler sees it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_SCOPE: ContextVar[tuple[Any, Any] | None] = ContextVar("mcp_tool_scope", default=None)
_USER: ContextVar[Any] = ContextVar("mcp_tool_user", default=None)


@contextmanager
def tool_call_scope(workspace_id: Any, task_id: Any, user_id: Any = None) -> Iterator[None]:
    token = _SCOPE.set((workspace_id, task_id))
    user_token = _USER.set(user_id)
    try:
        yield
    finally:
        _SCOPE.reset(token)
        _USER.reset(user_token)


def current_scope() -> tuple[Any, Any]:
    """``(workspace_id, task_id)`` of the running tool call, ``(None, None)`` outside one."""
    return _SCOPE.get() or (None, None)


def current_user() -> Any:
    """The user the running tool call acts for (``None`` outside one). File tools resolve the
    caller's connected folder from it; it is never taken from model arguments."""
    return _USER.get()
