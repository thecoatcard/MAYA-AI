"""Voice Tool Registry and Decoupled Task Handlers (Decisions D3 & D5).

Enforces:
1. Zero MCP tools in voice context (Decision D3). Voice model only holds
   submit_task and get_task_status.
2. Voice Approval Shielding (Decision D5). Any attempt to approve an action via voice is blocked.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID


class MCPToolRegistrationBlockedError(Exception):
    """Raised when an attempt is made to register MCP tools into the voice context."""


class VoiceApprovalProhibitedError(Exception):
    """Raised when an attempt is made to approve an action via voice (Decision D5)."""


@dataclass(frozen=True)
class VoiceExecutionContext:
    """Contextual metadata passed to voice tool executions."""

    workspace_id: UUID
    user_id: UUID
    session_id: str | None = None


# Types for external hook handlers
TaskSubmitter = Callable[[str, dict[str, Any], VoiceExecutionContext], Awaitable[dict[str, Any]]]
StatusGetter = Callable[[str, VoiceExecutionContext], Awaitable[dict[str, Any]]]


class VoiceToolRegistry:
    """Registry managing the strictly allowed voice tools for Gemini Live."""

    APPROVAL_PROHIBITED_MESSAGE = "Approvals must be confirmed in the secure web interface."

    def __init__(self) -> None:
        self._task_submitter: TaskSubmitter | None = None
        self._status_getter: StatusGetter | None = None

    def set_task_submitter(self, submitter: TaskSubmitter) -> None:
        """Inject the handler for submit_task."""
        self._task_submitter = submitter

    def set_status_getter(self, getter: StatusGetter) -> None:
        """Inject the handler for get_task_status."""
        self._status_getter = getter

    def get_tool_declarations(self) -> list[dict[str, Any]]:
        """Return the function declarations allowed for the voice model.

        Strictly limited to `submit_task` and `get_task_status`.
        """
        return [
            {
                "name": "submit_task",
                "description": (
                    "Submit a user instruction or goal as an asynchronous background task."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "intent": {
                            "type": "STRING",
                            "description": "The user's goal, instruction, or request.",
                        },
                        "params": {
                            "type": "OBJECT",
                            "description": (
                                "Optional parameters or structured constraints for the task."
                            ),
                        },
                    },
                    "required": ["intent"],
                },
            },
            {
                "name": "get_task_status",
                "description": (
                    "Retrieve current status, progress, and output summary for an active "
                    "or completed background task."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "task_id": {
                            "type": "STRING",
                            "description": "The unique identifier of the task.",
                        },
                    },
                    "required": ["task_id"],
                },
            },
        ]

    def register_tool(self, name: str, handler: Any) -> None:
        """Block registration of arbitrary or MCP tools (Decision D3)."""
        raise MCPToolRegistrationBlockedError(
            f"Zero MCP tools allowed in voice context (Decision D3). Attempted: {name}"
        )

    def register_mcp_tool(self, name: str) -> None:
        """Block registration of MCP tools (Decision D3)."""
        raise MCPToolRegistrationBlockedError(
            f"Zero MCP tools allowed in voice context (Decision D3). Attempted: {name}"
        )

    def approve_action(self, approval_id: str, context: VoiceExecutionContext) -> None:
        """Block approval actions from voice channel (Decision D5)."""
        raise VoiceApprovalProhibitedError(self.APPROVAL_PROHIBITED_MESSAGE)

    def execute_tool(
        self, name: str, args: dict[str, Any], context: VoiceExecutionContext
    ) -> dict[str, Any]:
        """Synchronously execute a tool or raise if approval / blocked."""
        return asyncio.run(self.execute_tool_async(name, args, context))

    async def execute_tool_async(
        self, name: str, args: dict[str, Any], context: VoiceExecutionContext
    ) -> dict[str, Any]:
        """Asynchronously execute a voice tool."""
        # Decision D5 enforcement: block any approval-related tool names
        lower_name = name.lower()
        if "approve" in lower_name or "confirm_approval" in lower_name or "totp" in lower_name:
            raise VoiceApprovalProhibitedError(self.APPROVAL_PROHIBITED_MESSAGE)

        if name == "submit_task":
            intent = str(args.get("intent", ""))
            params = args.get("params", {})
            if not isinstance(params, dict):
                params = {}
            if self._task_submitter is not None:
                return await self._task_submitter(intent, params, context)
            return {
                "task_id": f"task-mock-{context.workspace_id}",
                "status": "pending",
                "message": f"Task submitted: {intent}",
            }

        elif name == "get_task_status":
            task_id = str(args.get("task_id", ""))
            if self._status_getter is not None:
                return await self._status_getter(task_id, context)
            return {
                "task_id": task_id,
                "status": "in_progress",
                "progress": 0.0,
            }

        else:
            raise ValueError(
                f"Unsupported voice tool: '{name}'. "
                "Only submit_task and get_task_status are permitted."
            )
