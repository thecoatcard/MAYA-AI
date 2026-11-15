"""Unit tests for VoiceToolRegistry, Decision D3, and Decision D5."""

from typing import Any
from uuid import uuid4

import pytest

from app.voice.tools import (
    MCPToolRegistrationBlockedError,
    VoiceApprovalProhibitedError,
    VoiceExecutionContext,
    VoiceToolRegistry,
)


def test_voice_tool_declarations_strictly_limited_to_two_tools() -> None:
    registry = VoiceToolRegistry()
    declarations = registry.get_tool_declarations()

    tool_names = [decl["name"] for decl in declarations]
    assert sorted(tool_names) == ["get_task_status", "submit_task"]
    assert len(declarations) == 2


def test_mcp_tool_registration_is_strictly_prohibited_decision_d3() -> None:
    registry = VoiceToolRegistry()

    with pytest.raises(
        MCPToolRegistrationBlockedError, match="Zero MCP tools allowed in voice context"
    ):
        registry.register_tool("filesystem_write", lambda: None)

    with pytest.raises(
        MCPToolRegistrationBlockedError, match="Zero MCP tools allowed in voice context"
    ):
        registry.register_mcp_tool("github_create_pull_request")


def test_voice_approval_prohibited_decision_d5() -> None:
    registry = VoiceToolRegistry()
    context = VoiceExecutionContext(workspace_id=uuid4(), user_id=uuid4())

    with pytest.raises(
        VoiceApprovalProhibitedError,
        match="Approvals must be confirmed in the secure web interface.",
    ):
        registry.approve_action("approval-123", context=context)

    # Calling an approval tool via generic dispatch must also fail with Decision D5 message
    with pytest.raises(
        VoiceApprovalProhibitedError,
        match="Approvals must be confirmed in the secure web interface.",
    ):
        registry.execute_tool("approve_action", {"approval_id": "approval-123"}, context=context)

    with pytest.raises(
        VoiceApprovalProhibitedError,
        match="Approvals must be confirmed in the secure web interface.",
    ):
        registry.execute_tool("confirm_approval", {"pin": "1234"}, context=context)


@pytest.mark.asyncio
async def test_submit_task_execution() -> None:
    registry = VoiceToolRegistry()
    task_id = str(uuid4())

    async def mock_task_submitter(
        intent: str, params: dict[str, Any], ctx: VoiceExecutionContext
    ) -> dict[str, Any]:
        return {"task_id": task_id, "status": "pending", "message": f"Task submitted: {intent}"}

    registry.set_task_submitter(mock_task_submitter)
    context = VoiceExecutionContext(workspace_id=uuid4(), user_id=uuid4())

    result = await registry.execute_tool_async(
        "submit_task",
        {"intent": "Prepare daily briefing", "params": {"format": "audio"}},
        context=context,
    )

    assert result["task_id"] == task_id
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_get_task_status_execution() -> None:
    registry = VoiceToolRegistry()
    task_id = str(uuid4())

    async def mock_status_getter(t_id: str, ctx: VoiceExecutionContext) -> dict[str, Any]:
        return {"task_id": t_id, "status": "executing", "progress": 0.5}

    registry.set_status_getter(mock_status_getter)
    context = VoiceExecutionContext(workspace_id=uuid4(), user_id=uuid4())

    result = await registry.execute_tool_async(
        "get_task_status",
        {"task_id": task_id},
        context=context,
    )

    assert result["task_id"] == task_id
    assert result["status"] == "executing"


@pytest.mark.asyncio
async def test_unsupported_tool_execution_raises() -> None:
    registry = VoiceToolRegistry()
    context = VoiceExecutionContext(workspace_id=uuid4(), user_id=uuid4())

    with pytest.raises(ValueError, match="Unsupported voice tool"):
        await registry.execute_tool_async("delete_file", {"path": "/tmp/a"}, context=context)
