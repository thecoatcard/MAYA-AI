"""GitHub repository tools for chat: when they are offered and how one turn runs them.

The tools are offered when the caller has a GitHub connection (Settings > Connections or the
server default) or the message names a github.com repository URL (public repositories need no
token). Every turn gets its own :class:`RepoToolkit`, so the call and read budgets are per turn and
the caller's own token is used.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING
from uuid import UUID

from app.files import services as file_services
from app.files.access import FileContext, context_from
from app.mcp.connectors.github import GitHubConnector, find_profile_login, find_repo_urls
from app.mcp.connectors.notion import NotionConnector
from app.mcp.github_repo_tools import RepoToolkit
from app.mcp.notion_tools import NotionToolkit
from app.mcp.show_code import ShowCodeToolkit

if TYPE_CHECKING:
    from app.connections.service import ConnectionService
    from app.mail.service import MailService

logger = logging.getLogger(__name__)

# A message that names Notion offers the Notion tools even before a connection is checked; the
# tools then answer "not connected" with the fix, instead of the model guessing.
_NOTION_NAMED = re.compile(r"\bnotion\b", re.IGNORECASE)


class ChatRepoTools:
    """Availability check and toolkit factory for one authenticated caller."""

    def __init__(
        self, connections: ConnectionService | None, workspace_id: UUID, user_id: UUID
    ) -> None:
        self._connections = connections
        self._workspace_id = workspace_id
        self._user_id = user_id

    async def available(self, text: str) -> bool:
        """True when the message names a GitHub repo URL or the caller has GitHub connected."""
        if find_repo_urls(text) or find_profile_login(text):
            return True
        # "my repositories" is offered even without a connection: the tool then answers with the
        # plain "connect GitHub" message instead of the model guessing a repository name.
        from app.agents.capabilities import is_repo_listing_request  # local: avoids a cycle

        if is_repo_listing_request(text):
            return True
        if self._connections is None:
            return False
        try:
            return await self._connections.is_connected(self._workspace_id, self._user_id, "github")
        except Exception as err:  # noqa: BLE001 - availability must never fail a chat turn
            logger.warning("chat_repo_availability_failed error=%s", type(err).__name__)
            return False

    def new_toolkit(self, **limits: int) -> RepoToolkit:
        async def connector() -> GitHubConnector | None:
            if self._connections is None:
                return None
            return await self._connections.github_connector(self._workspace_id, self._user_id)

        return RepoToolkit(connector, **limits)

    @property
    def has_connections(self) -> bool:
        return self._connections is not None

    async def mail_service(self) -> MailService | None:
        """The caller's mailbox (their own saved one, else the server's), else ``None``. Never
        another user's and never raises: a failure means no mailbox for this turn."""
        if self._connections is None:
            return None
        try:
            return await self._connections.mail_service(self._workspace_id, self._user_id)
        except Exception as err:  # noqa: BLE001 - availability must never fail a chat turn
            logger.warning("chat_mailbox_failed error=%s", type(err).__name__)
            return None

    async def notion_available(self, text: str) -> bool:
        """True when the message names Notion or the caller has Notion connected. Never raises."""
        if _NOTION_NAMED.search(text or ""):
            return True
        if self._connections is None:
            return False
        try:
            return await self._connections.is_connected(self._workspace_id, self._user_id, "notion")
        except Exception as err:  # noqa: BLE001 - availability must never fail a chat turn
            logger.warning("chat_notion_availability_failed error=%s", type(err).__name__)
            return False

    def new_notion_toolkit(self, **limits: int) -> NotionToolkit:
        async def connection() -> tuple[NotionConnector, str | None] | None:
            if self._connections is None:
                return None
            return await self._connections.notion_connector(self._workspace_id, self._user_id)

        return NotionToolkit(connection, **limits)

    def new_show_code_toolkit(self) -> ShowCodeToolkit:
        async def github() -> GitHubConnector | None:
            if self._connections is None:
                return None
            return await self._connections.github_connector(self._workspace_id, self._user_id)

        async def files() -> FileContext:
            return context_from(file_services.current(), self._workspace_id, self._user_id, None)

        return ShowCodeToolkit(github, files)
