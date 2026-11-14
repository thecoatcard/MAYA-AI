"""Agent Fleet Registry for managing and dispatching specialist agents (Phase 09)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.agents.base import BaseSpecialistAgent
from app.agents.schemas import SpecialistAgentRole
from app.agents.specialized.coding import CodingAgent
from app.agents.specialized.document import DocumentAgent
from app.agents.specialized.email import EmailAgent
from app.agents.specialized.pm import PMAgent
from app.agents.specialized.research import ResearchAgent
from app.mail.config import MailboxConfig
from app.mail.service import MailService
from app.mcp.client_manager import MCPClientManager
from app.mcp.connectors.github import GitHubConnector
from app.mcp.connectors.google_search import GoogleSearchConnector
from app.mcp.connectors.notion import NotionConnector
from app.mcp.permissions import ToolPermissionRegistry
from app.sandbox.runner import SandboxRunner
from mcp_servers.google_adapter.calendar import GoogleCalendarService

logger = logging.getLogger(__name__)


class AgentFleetRegistry:
    """Registry maintaining initialized specialist agents available for task execution."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseSpecialistAgent] = {}

    def _normalize_key(self, name_or_role: str | SpecialistAgentRole) -> str:
        if isinstance(name_or_role, SpecialistAgentRole):
            return name_or_role.value.lower()
        return str(name_or_role).lower().strip()

    def register_agent(
        self,
        name_or_role: str | SpecialistAgentRole,
        agent: BaseSpecialistAgent,
    ) -> None:
        """Register a specialist agent in the fleet."""
        key = self._normalize_key(name_or_role)
        self._agents[key] = agent
        logger.info(
            "Registered specialist agent '%s' (%s) in fleet.", key, agent.__class__.__name__
        )

    def get_agent(self, name_or_role: str | SpecialistAgentRole) -> BaseSpecialistAgent | None:
        """Retrieve specialist agent by role or alias."""
        key = self._normalize_key(name_or_role)
        return self._agents.get(key)

    def has_agent(self, name_or_role: str | SpecialistAgentRole) -> bool:
        """Check if an agent is registered in the fleet."""
        key = self._normalize_key(name_or_role)
        return key in self._agents

    def list_agents(self) -> list[str]:
        """List all registered agent keys."""
        return sorted(list(self._agents.keys()))

    def unified_view(self) -> list[dict[str, Any]]:
        """The three unified agents (ADR-0023) with whether any member is registered."""
        from app.agents.unified import catalogue

        return catalogue({key: True for key in self._agents})


def create_default_fleet(
    client_manager: MCPClientManager | None = None,
    permission_registry: ToolPermissionRegistry | None = None,
    sandbox_runner: SandboxRunner | None = None,
    artifacts_base_dir: Path | str = "/artifacts",
    github_token: str | None = None,
    notion_token: str | None = None,
    notion_database_id: str | None = None,
    connections: Any | None = None,
    google_access_token: str | None = None,
    google_search_connector: GoogleSearchConnector | None = None,
    model_gateway: Any | None = None,
    native_search: Any | None = None,
    deep_research: Any | None = None,
    mailbox: MailboxConfig | None = None,
    research_backend: str = "native",
) -> AgentFleetRegistry:
    """Factory creating fully wired agent fleet with all 5 specialist agents."""
    fleet = AgentFleetRegistry()
    registry = permission_registry or ToolPermissionRegistry()

    # 1. ResearchAgent - Gemini Google Search grounding is the only search backend
    g_search = google_search_connector
    if g_search is None and model_gateway is not None:
        g_search = GoogleSearchConnector(gateway=model_gateway)

    research_agent = ResearchAgent(
        google_search_connector=g_search,
        model_gateway=model_gateway,
        permission_registry=registry,
        client_manager=client_manager,
        native_search=native_search,
        deep_research=deep_research,
        research_backend=research_backend,
    )
    fleet.register_agent(SpecialistAgentRole.RESEARCH, research_agent)

    # 2. CodingAgent
    gh_connector = GitHubConnector(token=github_token) if github_token else None
    runner = sandbox_runner or SandboxRunner()
    coding_agent = CodingAgent(
        sandbox_runner=runner,
        github_connector=gh_connector,
        connections=connections,
        permission_registry=registry,
        client_manager=client_manager,
    )
    fleet.register_agent(SpecialistAgentRole.CODING, coding_agent)

    # 3. DocumentAgent
    document_agent = DocumentAgent(
        artifacts_base_dir=artifacts_base_dir,
        permission_registry=registry,
        client_manager=client_manager,
    )
    fleet.register_agent(SpecialistAgentRole.DOCUMENT, document_agent)

    # 4. EmailAgent. Mail goes ONLY through the user's own mailbox (Settings > Connections, or the
    # server's MAYA_MAIL_* mailbox): the Google access token is never used to draft or send mail.
    email_agent = EmailAgent(
        gmail_service=None,
        mail_service=MailService(mailbox) if mailbox is not None else None,
        connections=connections,
        permission_registry=registry,
        client_manager=client_manager,
    )
    fleet.register_agent(SpecialistAgentRole.EMAIL, email_agent)

    # 5. PMAgent
    calendar_service = (
        GoogleCalendarService(access_token=google_access_token) if google_access_token else None
    )
    notion_conn = NotionConnector(token=notion_token) if notion_token else None
    pm_agent = PMAgent(
        calendar_service=calendar_service,
        notion_connector=notion_conn,
        notion_database_id=notion_database_id,
        connections=connections,
        permission_registry=registry,
        client_manager=client_manager,
    )
    fleet.register_agent(SpecialistAgentRole.PM, pm_agent)

    return fleet
