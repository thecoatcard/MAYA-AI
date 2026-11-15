"""Unit tests for BaseSpecialistAgent and Tool Allowlist Guardrails (Phase 09)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.agents.base import (
    BaseSpecialistAgent,
    PermissionCeilingExceededError,
    UnauthorizedToolError,
)
from app.agents.schemas import (
    AgentExecutionStatus,
    SpecialistAgentResult,
    SpecialistAgentRole,
)
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.mcp.sanitizer import SanitizationResult
from app.tasks.queue import SubtaskJob


class DummySpecialistAgent(BaseSpecialistAgent):
    """Concrete dummy agent for testing base class invariants."""

    role = SpecialistAgentRole.RESEARCH
    model_alias = "heavy"
    permission_ceiling = ToolTier.READ
    assigned_tools = frozenset({"web_search", "file_read"})

    async def execute_subtask(
        self,
        job: SubtaskJob,
        context: dict[str, object] | None = None,
    ) -> SpecialistAgentResult:
        return SpecialistAgentResult(
            agent_role=self.role,
            status=AgentExecutionStatus.SUCCESS,
            summary="Done",
            verification_proof={"checked": True},
        )


@pytest.mark.asyncio
async def test_base_agent_assigned_tools_allowlist() -> None:
    registry = ToolPermissionRegistry()
    registry.register_tool("web_search", ToolTier.READ)
    registry.register_tool("file_read", ToolTier.READ)
    registry.register_tool("file_write_artifact", ToolTier.WRITE)

    client_manager = MagicMock()
    agent = DummySpecialistAgent(permission_registry=registry, client_manager=client_manager)

    # Attempt to invoke a tool NOT in assigned_tools
    with pytest.raises(UnauthorizedToolError) as exc_info:
        await agent.invoke_tool(
            server_id=uuid4(),
            tool_name="file_write_artifact",
            arguments={"path": "out.txt", "content": "data"},
        )

    assert "is not permitted to invoke tool 'file_write_artifact'" in str(exc_info.value)
    assert client_manager.invoke_tool.call_count == 0


@pytest.mark.asyncio
async def test_base_agent_permission_ceiling_enforcement() -> None:
    registry = ToolPermissionRegistry()
    registry.register_tool("web_search", ToolTier.READ)
    registry.register_tool("gmail_send", ToolTier.APPROVAL_REQUIRED)

    class OverreachingAgent(BaseSpecialistAgent):
        role = SpecialistAgentRole.RESEARCH
        model_alias = "heavy"
        permission_ceiling = ToolTier.READ  # Ceiling is READ!
        # Attempts to assign APPROVAL_REQUIRED tool
        assigned_tools = frozenset({"web_search", "gmail_send"})

        async def execute_subtask(
            self,
            job: SubtaskJob,
            context: dict[str, object] | None = None,
        ) -> SpecialistAgentResult:
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.SUCCESS,
                summary="Done",
                verification_proof={},
            )

    client_manager = MagicMock()
    agent = OverreachingAgent(permission_registry=registry, client_manager=client_manager)

    # Invoking gmail_send must be rejected by permission ceiling
    with pytest.raises(PermissionCeilingExceededError) as exc_info:
        await agent.invoke_tool(
            server_id=uuid4(),
            tool_name="gmail_send",
            arguments={"to": "a@b.com", "subject": "Hi", "body": "Msg"},
        )

    assert "exceeds agent 'research' permission ceiling READ" in str(exc_info.value)
    assert client_manager.invoke_tool.call_count == 0


@pytest.mark.asyncio
async def test_base_agent_permitted_tool_invocation_succeeds() -> None:
    registry = ToolPermissionRegistry()
    registry.register_tool("web_search", ToolTier.READ)

    client_manager = MagicMock()
    client_manager.invoke_tool = AsyncMock(
        return_value=SanitizationResult(
            clean_text="Search results",
            wrapped_output=(
                "<<<UNTRUSTED_EXTERNAL_INPUT>>>Search results<<<END_UNTRUSTED_EXTERNAL_INPUT>>>"
            ),
            has_injection_suspicions=False,
            suspicions=(),
        )
    )

    agent = DummySpecialistAgent(permission_registry=registry, client_manager=client_manager)

    srv_id = uuid4()
    result = await agent.invoke_tool(
        server_id=srv_id,
        tool_name="web_search",
        arguments={"query": "Maya AI"},
    )

    assert "Search results" in result.clean_text
    client_manager.invoke_tool.assert_awaited_once_with(
        server_id=srv_id,
        tool_name="web_search",
        arguments={"query": "Maya AI"},
        has_valid_approval_token=False,
        server_hints=None,
    )
