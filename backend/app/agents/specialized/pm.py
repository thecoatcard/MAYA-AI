"""Project Management Specialist Agent for Workflow 1 (Daily Briefing Synthesis) (Phase 09)."""

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
from app.mcp.connectors.notion import NotionConnector
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.tasks.queue import SubtaskJob
from mcp_servers.google_adapter.calendar import GoogleCalendarService

logger = logging.getLogger(__name__)


class PMAgent(BaseSpecialistAgent):
    """Specialist agent for Workflow 1 (Daily Briefing synthesis from Calendar and Notion)."""

    role = SpecialistAgentRole.PM
    model_alias = "orchestrator"
    permission_ceiling = ToolTier.READ
    assigned_tools = frozenset(
        {
            "read_calendar",
            "calendar_list_events",
            "calendar_get_event",
            "notion_query_database",
            "notion_get_page",
            "file_read",
        }
    )

    def __init__(
        self,
        calendar_service: GoogleCalendarService | None = None,
        notion_connector: NotionConnector | None = None,
        permission_registry: ToolPermissionRegistry | None = None,
        client_manager: MCPClientManager | None = None,
    ) -> None:
        super().__init__(permission_registry=permission_registry, client_manager=client_manager)
        self.calendar_service = calendar_service
        self.notion_connector = notion_connector

    async def execute_subtask(
        self,
        job: SubtaskJob,
        context: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """Synthesize executive Daily Briefing from calendar and project tasks."""
        ctx = context or {}
        date_str = str(ctx.get("date", "today"))

        events: list[dict[str, Any]] = []
        if self.calendar_service:
            events = await self.calendar_service.list_events()

        tasks: list[dict[str, Any]] = []
        notion_notice: str | None = None
        if self.notion_connector:
            try:
                db_id = str(ctx.get("notion_database_id", "default_db"))
                tasks = await self.notion_connector.query_database(database_id=db_id)
            except Exception as exc:  # noqa: BLE001
                notion_notice = f"Notion tasks unavailable: {exc}"
                logger.warning("Workflow 1 partial failure: %s", notion_notice)

        # Build Daily Briefing Summary
        lines = [f"# Daily Briefing — {date_str}\n"]
        lines.append("## Scheduled Events & Meetings")
        if events:
            for ev in events:
                summary = ev.get("summary", "Untitled Event")
                start = ev.get("start", {}).get("dateTime", "")
                lines.append(f"- **{summary}** ({start})")
        else:
            lines.append("- No events scheduled.")

        lines.append("\n## Active Priorities & Tasks")
        if tasks:
            for t in tasks:
                title = "Task"
                props = t.get("properties", {})
                for _k, v in props.items():
                    if isinstance(v, dict) and "title" in v:
                        title_parts = v["title"]
                        if title_parts and isinstance(title_parts, list):
                            title = str(title_parts[0].get("plain_text", title))
                lines.append(f"- [ ] {title}")
        elif notion_notice:
            lines.append(f"- *({notion_notice})*")
        else:
            lines.append("- No active tasks retrieved.")

        briefing_text = "\n".join(lines)

        return SpecialistAgentResult(
            agent_role=self.role,
            status=AgentExecutionStatus.SUCCESS,
            summary=briefing_text,
            verification_proof={
                "events_reviewed": len(events),
                "tasks_reviewed": len(tasks),
            },
        )
