"""Project Management Specialist Agent for Workflow 1 (Daily Briefing Synthesis) (Phase 09)."""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx

from app.agents.base import BaseSpecialistAgent
from app.agents.schemas import (
    AgentExecutionStatus,
    SpecialistAgentResult,
    SpecialistAgentRole,
)
from app.connections.providers import ConnectionInputError
from app.connections.service import ConnectionService
from app.mcp.client_manager import MCPClientManager
from app.mcp.connectors.notion import NotionConnector
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.tasks.queue import SubtaskJob
from mcp_servers.google_adapter.calendar import GoogleCalendarService

logger = logging.getLogger(__name__)

NO_SOURCE_CONNECTED = (
    "Neither Google Calendar nor Notion is connected, so there is nothing to build a briefing "
    "from. Open Settings > Connections and add your Notion token and database "
    "(Google Calendar is set up by the server administrator)."
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


NOTION_DB_MISSING = (
    "Notion: add the database URL or id of your tasks database in Settings > Connections, and "
    "share that database with your Notion integration (the ... menu > Connections)."
)
NOTION_DB_NOT_SHARED = (
    "Notion cannot see that database. Share it with your integration (the ... menu > "
    "Connections in Notion), or check the database id in Settings > Connections."
)
CALENDAR_TOKEN_EXPIRED = (
    "Google Calendar: token expired or rejected (HTTP {code}); refresh the access token by "
    "setting a fresh MAYA_GOOGLE_ACCESS_TOKEN and restarting the backend (no automatic refresh "
    "flow exists yet)."
)


def _source_failure(source: str, exc: Exception) -> str:
    """One actionable line for a failed source (never a URL, token or stack trace)."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            if source == "Google Calendar":
                return CALENDAR_TOKEN_EXPIRED.format(code=code)
            return (
                f"{source} rejected the credentials (HTTP {code}); open Settings > Connections, "
                "press Test connection and replace the token if needed."
            )
        if code in (400, 404):
            if source == "Notion":
                return f"Notion could not use the database (HTTP {code}). {NOTION_DB_NOT_SHARED}"
            return f"{source} could not find the requested data (HTTP {code})."
        if code == 429:
            return f"{source} is rate limiting requests (HTTP 429); try again shortly."
        return f"{source} is unavailable (HTTP {code})."
    if isinstance(exc, _NotionDatabaseMissingError | ConnectionInputError):
        return str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return f"{source} did not answer in time."
    return f"{source} is unavailable ({exc.__class__.__name__})."


class _NotionDatabaseMissingError(Exception):
    """No Notion database id is configured (MAYA_NOTION_DATABASE_ID)."""


def _briefing_date(raw: Any) -> date:
    """The requested YYYY-MM-DD date, else today (UTC)."""
    text = str(raw or "").strip()
    if _DATE.match(text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    return datetime.now(UTC).date()


class PMAgent(BaseSpecialistAgent):
    """Specialist agent for Workflow 1 (Daily Briefing synthesis from Calendar and Notion)."""

    role = SpecialistAgentRole.PM
    permission_ceiling = ToolTier.READ
    execution_timeout_seconds = 60.0
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
        notion_database_id: str | None = None,
        permission_registry: ToolPermissionRegistry | None = None,
        client_manager: MCPClientManager | None = None,
        connections: ConnectionService | None = None,
    ) -> None:
        super().__init__(permission_registry=permission_registry, client_manager=client_manager)
        self.calendar_service = calendar_service
        self.notion_connector = notion_connector
        self.notion_database_id = (notion_database_id or "").strip() or None
        # When set, Notion credentials are resolved per requesting user at run time (their own
        # saved connection, else the server default); the fixed connector above is then unused.
        self.connections = connections

    def availability_error(self) -> str | None:
        if self.connections is not None:
            return None  # decided per user in availability_error_for
        if self.calendar_service is None and self.notion_connector is None:
            return NO_SOURCE_CONNECTED
        return None

    async def availability_error_for(self, job: SubtaskJob) -> str | None:
        """Like ``availability_error`` but for the user who asked (their Notion connection)."""
        if self.connections is None:
            return self.availability_error()
        if await self._calendar_for(job) is not None:
            return None
        if await self.connections.is_connected(job.workspace_id, job.user_id, "notion"):
            return None
        return NO_SOURCE_CONNECTED

    async def _calendar_for(self, job: SubtaskJob) -> GoogleCalendarService | None:
        """The server's Google Calendar belongs to the deployment owner: with a connection service
        only that account may use it (``MAYA_ENV_CONNECTIONS_USERS``)."""
        if self.calendar_service is None:
            return None
        if self.connections is None:
            return self.calendar_service
        if await self.connections.is_deployment_owner(job.workspace_id, job.user_id):
            return self.calendar_service
        return None

    async def _notion_for(self, job: SubtaskJob) -> tuple[NotionConnector | None, str | None]:
        """The Notion connector and database id for the user who asked."""
        if self.connections is None:
            return self.notion_connector, self.notion_database_id
        resolved = await self.connections.notion_connector(job.workspace_id, job.user_id)
        if resolved is None:
            return None, None
        return resolved

    async def execute_subtask(
        self,
        job: SubtaskJob,
        context: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """Synthesize an executive Daily Briefing from the calendar and project tasks.

        Each source is read independently: one failing source becomes a visible notice and the
        other still contributes. If every connected source fails the result is FAILED, never a
        briefing that pretends nothing is scheduled.
        """
        ctx = context or {}
        notion_connector, notion_database_id = await self._notion_for(job)
        calendar_service = await self._calendar_for(job)
        if calendar_service is None and notion_connector is None:
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.FAILED,
                summary=NO_SOURCE_CONNECTED,
                verification_proof={"error": "no_sources_connected"},
            )
        day = _briefing_date(ctx.get("date"))
        date_str = day.isoformat()
        day_start = datetime.combine(day, time.min, tzinfo=UTC)

        events: list[dict[str, Any]] = []
        calendar_notice: str | None = None
        if calendar_service:
            try:
                events = await calendar_service.list_events(
                    time_min=day_start.isoformat(),
                    time_max=(day_start + timedelta(days=1)).isoformat(),
                )
            except Exception as exc:  # noqa: BLE001
                calendar_notice = _source_failure("Google Calendar", exc)
                logger.warning("pm_calendar_failed: %s", calendar_notice)

        tasks: list[dict[str, Any]] = []
        notion_notice: str | None = None
        if notion_connector:
            try:
                db_id = str(ctx.get("notion_database_id") or notion_database_id or "")
                if not db_id:
                    # Querying a made-up id only produces a Notion 404: say what to set instead.
                    raise _NotionDatabaseMissingError(NOTION_DB_MISSING)
                tasks = await notion_connector.query_database(database_id=db_id)
            except Exception as exc:  # noqa: BLE001
                notion_notice = f"Notion tasks unavailable: {_source_failure('Notion', exc)}"
                logger.warning("Workflow 1 partial failure: %s", notion_notice)

        attempted = (calendar_service is not None) + (notion_connector is not None)
        failed = (calendar_notice is not None) + (notion_notice is not None)
        if failed == attempted:
            reasons = " ".join(n for n in (calendar_notice, notion_notice) if n)
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.FAILED,
                summary=f"The daily briefing could not be built. {reasons}",
                verification_proof={
                    "error": "all_sources_failed",
                    "calendar_error": calendar_notice,
                    "notion_error": notion_notice,
                },
            )

        lines = [f"# Daily Briefing — {date_str}\n"]
        lines.append("## Scheduled Events & Meetings")
        if calendar_notice:
            lines.append(f"- *({calendar_notice})*")
        elif events:
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
                "calendar_error": calendar_notice,
                "notion_error": notion_notice,
            },
        )
