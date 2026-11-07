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
from app.mcp.connectors.google_search import GoogleSearchConnector, GoogleSearchResult
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.tasks.queue import SubtaskJob

logger = logging.getLogger(__name__)


class ResearchAgent(BaseSpecialistAgent):
    """Specialist agent for web discovery, source verification, and verbatim citations."""

    role = SpecialistAgentRole.RESEARCH
    model_alias = "heavy"
    permission_ceiling = ToolTier.READ
    assigned_tools = frozenset({"web_search", "google_search", "fetch_web_page", "file_read"})

    def __init__(
        self,
        google_search_connector: GoogleSearchConnector | None = None,
        brave_connector: BraveSearchConnector | None = None,
        model_gateway: Any | None = None,
        permission_registry: ToolPermissionRegistry | None = None,
        client_manager: MCPClientManager | None = None,
    ) -> None:
        super().__init__(permission_registry=permission_registry, client_manager=client_manager)
        self.google_search_connector = google_search_connector
        self.brave_connector = brave_connector
        self.model_gateway = model_gateway

    async def execute_subtask(
        self,
        job: SubtaskJob,
        context: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """Execute web research subtask using Google Search / Gemini Live grounding."""
        ctx = context or {}
        query = str(
            ctx.get("query")
            or ctx.get("instruction")
            or ctx.get("description")
            or ctx.get("user_instruction")
            or ""
        ).strip()

        if not query:
            # Fallback to job assigned agent description
            query = f"Technical research for {job.assigned_agent} subtask"

        logger.info(
            "research_agent_executing query=%s task_id=%s subtask_id=%s",
            query[:60],
            job.task_id,
            job.subtask_id,
        )

        citations: list[dict[str, str]] = []
        clean_summary = ""
        has_injection = False
        search_engine = "google_search"

        # 1. Primary: Google Search Connector (powered by Gemini Live search grounding)
        if self.google_search_connector is not None:
            g_res: GoogleSearchResult = await self.google_search_connector.search(query=query)
            clean_summary = g_res.sanitized_result.clean_text
            citations = g_res.citations
            has_injection = g_res.sanitized_result.has_injection_suspicions

        # 2. Secondary: ModelGateway with Google Search grounding
        elif self.model_gateway is not None:
            prompt = (
                f"You are the Research Specialist Agent in MAYA AI.\n"
                f"Topic: {query}\n"
                "Search Google and provide comprehensive technical findings, "
                "architecture patterns, and practical code implementations with citations."
            )
            try:
                gw_res = await self.model_gateway.generate_text(
                    alias="orchestrator",
                    contents=[prompt],
                    enable_google_search=True,
                    workspace_id=job.workspace_id,
                    task_id=job.task_id,
                )
                clean_summary = gw_res.text
                citations = gw_res.citations
            except Exception as gw_err:
                logger.warning("model_gateway_research_failed: %s", gw_err)

        # 3. Tertiary fallback: Brave Search Connector (if explicitly configured and needed)
        elif self.brave_connector is not None:
            search_engine = "brave"
            b_res: BraveSearchResult = await self.brave_connector.search(query=query)
            clean_summary = b_res.sanitized_result.clean_text
            citations = b_res.citations
            has_injection = b_res.sanitized_result.has_injection_suspicions

        # 4. Fallback: Default GoogleSearchConnector offline synthesis
        if not clean_summary:
            fallback_conn = GoogleSearchConnector(client=None)
            g_fallback = await fallback_conn.search(query=query)
            clean_summary = g_fallback.sanitized_result.clean_text
            citations = g_fallback.citations

        return SpecialistAgentResult(
            agent_role=self.role,
            status=AgentExecutionStatus.SUCCESS,
            summary=clean_summary,
            verification_proof={
                "search_engine": search_engine,
                "citations_count": len(citations),
                "sources": [c.get("url", "") for c in citations if c.get("url")],
                "has_injection_suspicions": has_injection,
            },
            citations=citations,
        )
