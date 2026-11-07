"""Research Specialist Agent for Web Discovery and Verbatim Citations (Phase 09)."""

from __future__ import annotations

import logging
from typing import Any

from app.agents.base import BaseSpecialistAgent
from app.agents.schemas import (
    AgentExecutionStatus,
    SpecialistAgentResult,
    SpecialistAgentRole,
)
from app.mcp.client_manager import MCPClientManager
from app.mcp.connectors.brave import BraveSearchConnector, BraveSearchResult
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.tasks.queue import SubtaskJob

logger = logging.getLogger(__name__)


class ResearchAgent(BaseSpecialistAgent):
    """Specialist agent for web discovery, source verification, and verbatim citations."""

    role = SpecialistAgentRole.RESEARCH
    model_alias = "heavy"
    permission_ceiling = ToolTier.READ
    assigned_tools = frozenset({"web_search", "fetch_web_page", "file_read"})

    def __init__(
        self,
        brave_connector: BraveSearchConnector | None = None,
        permission_registry: ToolPermissionRegistry | None = None,
        client_manager: MCPClientManager | None = None,
    ) -> None:
        super().__init__(permission_registry=permission_registry, client_manager=client_manager)
        self.brave_connector = brave_connector

    async def execute_subtask(
        self,
        job: SubtaskJob,
        context: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """Execute web research subtask and compile structured citations."""
        ctx = context or {}
        query = str(ctx.get("query", "")).strip()

        if not query:
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.FAILED,
                summary="No research query specified in context.",
                verification_proof={"error": "empty_query"},
            )

        if not self.brave_connector:
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.FAILED,
                summary="BraveSearchConnector not configured on ResearchAgent.",
                verification_proof={"error": "missing_connector"},
            )

        result: BraveSearchResult = await self.brave_connector.search(query=query)

        return SpecialistAgentResult(
            agent_role=self.role,
            status=AgentExecutionStatus.SUCCESS,
            summary=result.sanitized_result.clean_text,
            verification_proof={
                "citations_count": len(result.citations),
                "sources": [c["url"] for c in result.citations],
                "has_injection_suspicions": result.sanitized_result.has_injection_suspicions,
            },
            citations=result.citations,
        )
