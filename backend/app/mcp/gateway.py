"""MCP gateway: lazy, supervised tool servers behind the permission gate.

The task worker calls :meth:`McpGateway.invoke` for every tool step. The gateway

* resolves the server that provides the tool (built-in specs first, then the workspace's
  ``mcp_servers`` rows),
* starts that server lazily on first use through :class:`MCPClientManager` (schema-hash pinned,
  supervised by :class:`ProcessSupervisor`),
* injects the server's credentials into the server's own environment/closure, so a secret never
  appears in tool arguments, model context, logs or audit rows,
* lets :class:`ToolPermissionRegistry` authorize the call (deny by default; mutating tools need a
  valid approval token) before any request goes over the transport.

A missing credential or an unreachable server raises :class:`ToolUnavailableError`, which the
worker records as a real, non-retryable failure. Nothing here fabricates a result.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connections.service import ConnectionService
from app.db.models import McpServer
from app.files import services as file_services
from app.files.access import FileContext, context_from
from app.mail.config import MailboxConfig
from app.mail.errors import MailError
from app.mail.service import MailService
from app.mail.tools import MAIL_EXECUTION_TOOLS, execute_send, mail_write_mcp_manifest
from app.mcp.client_manager import MCPClientManager, MCPServerInstanceConfig
from app.mcp.connectors.github import GitHubConnector, GitHubRepoError
from app.mcp.connectors.google_search import (
    GoogleSearchConnector,
    GoogleSearchError,
    with_sources,
)
from app.mcp.connectors.notion import NotionConnector
from app.mcp.file_tools import FILE_HANDLERS, FILE_MANIFEST
from app.mcp.github_repo_tools import (
    REPO_TOOL_NAMES,
    REVIEW_TOOL_NAME,
    RepoToolkit,
    repo_mcp_manifest,
    review_mcp_entry,
)
from app.mcp.normalizer import compute_server_schema_hash
from app.mcp.notion_book import (
    NOTION_BOOK_TOOL_NAMES,
    BookWriteError,
    book_write_mcp_manifest,
    execute_book_write,
)
from app.mcp.notion_tools import NOT_CONNECTED_MESSAGE as _NOTION_NOT_CONNECTED
from app.mcp.notion_tools import (
    NOTION_TOOL_NAMES,
    NotionToolError,
    NotionToolkit,
    notion_mcp_manifest,
)
from app.mcp.notion_write import (
    NOTION_WRITE_TOOL_NAMES,
    NotionWriteError,
    execute_write,
    notion_write_mcp_manifest,
)
from app.mcp.permissions import PermissionDeniedError
from app.mcp.sanitizer import SanitizationResult
from app.mcp.scope import current_scope, current_user
from app.mcp.show_code import ShowCodeError, ShowCodeToolkit, show_code_mcp_entry
from app.mcp.transports.base import BaseTransport, MCPError
from app.mcp.transports.http import StreamableHttpTransport
from app.mcp.transports.stdio import StdioTransport
from app.runtime_tools.chat import INSTALL_TOOL, execute_install, runtime_tool_mcp_manifest
from app.runtime_tools.registry import RegistryError
from app.runtime_tools.service import RuntimeToolService

logger = logging.getLogger(__name__)

_NAMESPACE = UUID("6f1c3a52-8f0e-4c1d-9a55-2d7c4b6e1a90")
_UNPINNED = re.compile(r"(@latest\b|\bnpx\b.*\s-y\b|^npx$)")


class ToolUnavailableError(Exception):
    """The tool cannot run (no server, missing credential, server down). Not retryable."""


ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]

_GITHUB_NOT_CONNECTED = (
    "GitHub is not connected. Open Settings > Connections, add your GitHub token and press "
    "Test connection."
)


class InProcessToolTransport(BaseTransport):
    """An MCP server living in this process: ``tools/list`` and ``tools/call`` over handlers.

    Used for the built-in credentialed tools, and by tests as a fake MCP server. The handlers
    close over their credentials, which therefore never cross the transport.
    """

    def __init__(self, manifest: list[dict[str, Any]], handlers: dict[str, ToolHandler]) -> None:
        self.manifest = manifest
        self.handlers = handlers
        self._alive = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self) -> None:
        self._alive = True

    async def stop(self) -> None:
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109
    ) -> dict[str, Any]:
        if not self._alive:
            raise MCPError("in-process MCP server is not running")
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.manifest}
        if method == "tools/call":
            call = params or {}
            name = str(call.get("name", ""))
            args = dict(call.get("arguments") or {})
            handler = self.handlers.get(name)
            if handler is None:
                raise MCPError(f"unknown tool '{name}'")
            self.calls.append((name, args))
            text = await asyncio.wait_for(handler(args), timeout=timeout)
            return {"content": [{"type": "text", "text": text}]}
        raise MCPError(f"unsupported method '{method}'")


@dataclass
class ToolServerSpec:
    """How to start one tool server and which tools it provides."""

    name: str
    vendor: str
    tools: frozenset[str]
    build: Callable[[], Awaitable[MCPServerInstanceConfig]]
    server_id: UUID = field(default_factory=lambda: UUID(int=0))
    # Required argument names per tool (from the tool manifest); used to repair or explain a
    # call that lacks one before the permission gate rejects it.
    required_args: dict[str, frozenset[str]] = field(default_factory=dict)


def _tool(
    name: str, description: str, props: dict[str, str], required: list[str]
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {k: {"type": v} for k, v in props.items()},
            "required": required,
        },
    }


BUILTIN_MANIFEST: list[dict[str, Any]] = [
    _tool("web_search", "Search the web and return cited results.", {"query": "string"}, ["query"]),
    _tool(
        "github_create_pull_request",
        "Open a GitHub pull request (requires human approval).",
        {"repo": "string", "title": "string", "head": "string", "base": "string", "body": "string"},
        ["repo", "title", "head", "base"],
    ),
    # Read-only repository tools (app/mcp/github_repo_tools.py).
    *repo_mcp_manifest(),
    review_mcp_entry(),
    # Connected-folder file tools (ADR-0022); the handlers live in app/mcp/file_tools.py.
    *FILE_MANIFEST,
    # Read-only Notion tools (app/mcp/notion_tools.py) and the read-only show_code tool.
    *notion_mcp_manifest(),
    show_code_mcp_entry(),
]
# What the built-in server serves: the tools above plus the Notion writes (APPROVAL_REQUIRED;
# app/mcp/notion_write.py). The writes are NOT in BUILTIN_MANIFEST, which is also the list of tools
# the planner may name: a write step exists only when a chat tool call built and validated its
# exact payload, so a planner-invented write step is dropped like any unknown tool.
SERVED_MANIFEST: list[dict[str, Any]] = [
    *BUILTIN_MANIFEST,
    *notion_write_mcp_manifest(),
    # A whole book page from a finished job (APPROVAL_REQUIRED; app/mcp/notion_book.py).
    *book_write_mcp_manifest(),
    # Mailbox sends (APPROVAL_REQUIRED; app/mail/tools.py), served like the Notion writes.
    *mail_write_mcp_manifest(),
    # Approval of one runtime tool version (app/runtime_tools); served, not planner-visible.
    *runtime_tool_mcp_manifest(),
]


def _required_from_manifest(manifest: list[Any]) -> dict[str, frozenset[str]]:
    out: dict[str, frozenset[str]] = {}
    for tool in manifest:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        schema = tool.get("inputSchema")
        required = schema.get("required") if isinstance(schema, dict) else None
        if isinstance(required, list) and required:
            out[str(tool["name"])] = frozenset(str(r) for r in required)
    return out


def builtin_spec(
    *,
    github_token: str | None,
    google_search: GoogleSearchConnector | None = None,
    connections: ConnectionService | None = None,
    mailbox: MailboxConfig | None = None,
    runtime_tools: RuntimeToolService | None = None,
    artifacts_dir: Path | str | None = None,
) -> ToolServerSpec:
    """The built-in ``maya-tools`` server. Credentials are captured here, never in arguments.

    With ``connections`` the GitHub token is the REQUESTING user's own (resolved from the tool
    call scope, which the worker sets from the task owner), falling back to ``github_token``.
    """
    fixed_github = GitHubConnector(token=github_token) if github_token else None

    async def github_for_caller() -> GitHubConnector | None:
        if connections is None:
            return fixed_github
        workspace_id, _task = current_scope()
        user_id = current_user()
        if workspace_id is None or user_id is None:
            return None  # no caller identity: never guess whose token to use
        return await connections.github_connector(workspace_id, user_id)

    async def notion_for_caller() -> tuple[NotionConnector, str | None] | None:
        """The caller's own Notion connection and tasks database (never another user's)."""
        if connections is None:
            return None
        workspace_id, _task = current_scope()
        user_id = current_user()
        if workspace_id is None or user_id is None:
            return None
        return await connections.notion_connector(workspace_id, user_id)

    async def files_for_caller() -> FileContext:
        """The caller's connected folder and uploads; raises FileAccessError without them."""
        workspace_id, task_id = current_scope()
        user_id = current_user()
        return context_from(file_services.current(), workspace_id, user_id, task_id)

    def notion_handler(tool_name: str) -> ToolHandler:
        async def handle(args: dict[str, Any]) -> str:
            toolkit = NotionToolkit(notion_for_caller)
            try:
                return await toolkit.run(tool_name, args)
            except NotionToolError as err:
                raise MCPError(str(err)) from None

        return handle

    def notion_write_handler(tool_name: str) -> ToolHandler:
        async def handle(args: dict[str, Any]) -> str:
            # The flag is set by the client manager only for a call the permission gate passed
            # with a verified approval; ``execute_write`` refuses without it.
            approved = args.get("has_valid_approval_token") is True
            try:
                connection = await notion_for_caller() if approved else None
                if approved and connection is None:
                    raise NotionWriteError(_NOTION_NOT_CONNECTED)
                connector = connection[0] if connection is not None else None
                return await execute_write(tool_name, args, connector, approved=approved)
            except NotionWriteError as err:
                raise MCPError(str(err)) from None

        return handle

    def notion_book_handler() -> ToolHandler:
        async def handle(args: dict[str, Any]) -> str:
            # Same rule as the other Notion writes: only a call the worker verified and claimed.
            approved = args.get("has_valid_approval_token") is True
            try:
                connection = await notion_for_caller() if approved else None
                if approved and connection is None:
                    raise NotionWriteError(_NOTION_NOT_CONNECTED)
                workspace_id, task_id = current_scope()
                return await execute_book_write(
                    args,
                    connection[0] if connection is not None else None,
                    approved=approved,
                    artifacts_root=Path(artifacts_dir or "storage/artifacts"),
                    workspace_id=workspace_id,
                    task_id=task_id,
                )
            except (NotionWriteError, BookWriteError) as err:
                raise MCPError(str(err)) from None

        return handle

    fixed_mail = MailService(mailbox) if mailbox is not None else None

    async def mail_for_caller() -> MailService | None:
        """The REQUESTING user's mailbox (saved in Settings, else the server's), never another
        user's. Without a caller identity nothing is guessed."""
        if connections is None:
            return fixed_mail
        workspace_id, _task = current_scope()
        user_id = current_user()
        if workspace_id is None or user_id is None:
            return None
        return await connections.mail_service(workspace_id, user_id)

    def mail_send_handler() -> ToolHandler:
        async def handle(args: dict[str, Any]) -> str:
            # The flag is set by the client manager only for a call the permission gate passed
            # with a verified approval; ``execute_send`` refuses without it.
            approved = args.get("has_valid_approval_token") is True
            try:
                service = await mail_for_caller() if approved else None
                return await execute_send(args, service, approved=approved)
            except MailError as err:
                raise MCPError(str(err)) from None

        return handle

    async def runtime_tool_install(args: dict[str, Any]) -> str:
        # Records the grant only; ``approved`` is True only for a call the permission gate passed
        # with a verified approval token. No tool code runs here.
        workspace_id, _task = current_scope()
        try:
            return execute_install(
                runtime_tools,
                workspace_id,
                args,
                approved=args.get("has_valid_approval_token") is True,
            )
        except RegistryError as err:
            raise MCPError(str(err)) from None

    async def show_code(args: dict[str, Any]) -> str:
        toolkit = ShowCodeToolkit(github_for_caller, files_for_caller)
        try:
            return await toolkit.run(args)
        except ShowCodeError as err:
            raise MCPError(str(err)) from None

    async def web_search(args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            raise MCPError("query is required")
        if google_search is None:
            raise MCPError("web_search has no backend: configure the Gemini API key")
        workspace_id, task_id = current_scope()
        try:
            grounded = await google_search.search(query, workspace_id=workspace_id, task_id=task_id)
        except GoogleSearchError as err:
            # Chained to the provider error so the worker still recognises a 429/503 and
            # pauses and retries the step instead of failing it for good.
            prefix = f"{err.outcome}: " if err.search_unavailable else ""
            raise MCPError(f"{prefix}{err}") from err
        # The answer plus the numbered grounded sources: a result without its sources is a
        # claim nobody can check.
        return with_sources(
            grounded.sanitized_result.clean_text, list(getattr(grounded, "citations", None) or [])
        )

    async def create_pr(args: dict[str, Any]) -> str:
        github = await github_for_caller()
        if github is None:
            raise MCPError(_GITHUB_NOT_CONNECTED)
        pr = await github.create_pull_request(
            repo=str(args["repo"]),
            title=str(args["title"]),
            head=str(args["head"]),
            base=str(args["base"]),
            body=str(args.get("body", "")),
            has_valid_approval_token=bool(args.get("has_valid_approval_token")),
        )
        return json.dumps({"html_url": pr.get("html_url"), "number": pr.get("number")})

    def repo_handler(tool_name: str) -> ToolHandler:
        async def handle(args: dict[str, Any]) -> str:
            # The caller's own token when connected, else an unauthenticated public-only read.
            toolkit = RepoToolkit(github_for_caller)
            try:
                return await toolkit.run(tool_name, args)
            except GitHubRepoError as err:
                raise MCPError(str(err)) from None

        return handle

    handlers: dict[str, ToolHandler] = {
        **{tool: repo_handler(tool) for tool in (*REPO_TOOL_NAMES, REVIEW_TOOL_NAME)},
        "web_search": web_search,
        "github_create_pull_request": create_pr,
        **{tool: notion_handler(tool) for tool in NOTION_TOOL_NAMES},
        **{tool: notion_write_handler(tool) for tool in NOTION_WRITE_TOOL_NAMES},
        **{tool: notion_book_handler() for tool in NOTION_BOOK_TOOL_NAMES},
        **{tool: mail_send_handler() for tool in MAIL_EXECUTION_TOOLS},
        INSTALL_TOOL: runtime_tool_install,
        "show_code": show_code,
        **FILE_HANDLERS,
    }
    name = "maya-tools"

    async def build() -> MCPServerInstanceConfig:
        return MCPServerInstanceConfig(
            server_id=uuid5(_NAMESPACE, name),
            tenant_id=UUID(int=0),
            name=name,
            vendor="maya-tools",
            transport=InProcessToolTransport(SERVED_MANIFEST, handlers),
            expected_schema_hash=compute_server_schema_hash(SERVED_MANIFEST),
        )

    return ToolServerSpec(
        name=name,
        vendor="maya-tools",
        tools=frozenset(handlers),
        build=build,
        server_id=uuid5(_NAMESPACE, name),
        required_args=_required_from_manifest(SERVED_MANIFEST),
    )


class McpGateway:
    """Resolves a tool to its server, starts it lazily, and invokes it behind the gate."""

    def __init__(self, manager: MCPClientManager | None = None) -> None:
        self.manager = manager or MCPClientManager()
        self._specs: dict[str, ToolServerSpec] = {}
        self._tool_to_server: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._loaded_workspaces: set[UUID] = set()

    @property
    def permission_registry(self) -> Any:
        return self.manager.permission_registry

    def register_spec(self, spec: ToolServerSpec) -> None:
        self._specs[spec.name] = spec
        for tool in spec.tools:
            self._tool_to_server.setdefault(tool, spec.name)

    def server_name_for(self, tool_name: str) -> str | None:
        name = self._tool_to_server.get(tool_name)
        return name.split(":")[-1] if name else None

    def required_args(self, tool_name: str) -> frozenset[str]:
        """Argument names the tool's manifest requires (empty when none or unknown)."""
        server = self._tool_to_server.get(tool_name)
        spec = self._specs.get(server) if server else None
        return spec.required_args.get(tool_name, frozenset()) if spec else frozenset()

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._tool_to_server

    async def load_workspace_servers(self, session: AsyncSession, workspace_id: UUID) -> None:
        """Register this workspace's active, pinned ``mcp_servers`` rows (once per process)."""
        if workspace_id in self._loaded_workspaces:
            return
        rows = (
            (
                await session.execute(
                    select(McpServer).where(
                        McpServer.workspace_id == workspace_id, McpServer.is_active.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            names = frozenset(
                str(t.get("name")) for t in (row.tools_manifest or []) if isinstance(t, dict)
            )
            reason = self._db_row_rejection(row)
            if reason:
                logger.warning("mcp_server_skipped name=%s reason=%s", row.name, reason)
                continue
            self.register_spec(self._db_spec(row, names))
        self._loaded_workspaces.add(workspace_id)

    @staticmethod
    def _db_row_rejection(row: McpServer) -> str | None:
        if not row.schema_hash or not row.pinned_version:
            return "needs a pinned version and schema hash"
        joined = " ".join([row.command or "", *map(str, row.args or [])])
        if _UNPINNED.search(joined):
            return "unpinned launcher (@latest / npx -y) is not allowed"
        if row.transport == "stdio" and not row.command:
            return "stdio server has no command"
        if row.transport == "streamable_http" and not row.url:
            return "http server has no url"
        if row.transport not in ("stdio", "streamable_http"):
            return f"unsupported transport {row.transport}"
        return None

    def _db_spec(self, row: McpServer, names: frozenset[str]) -> ToolServerSpec:
        server_id = uuid5(_NAMESPACE, f"{row.workspace_id}:{row.name}")

        async def build() -> MCPServerInstanceConfig:
            transport: BaseTransport
            if row.transport == "stdio":
                transport = StdioTransport(row.command or "", [str(a) for a in row.args or []])
            else:
                transport = StreamableHttpTransport(row.url or "")
            return MCPServerInstanceConfig(
                server_id=server_id,
                tenant_id=row.workspace_id,
                name=row.name,
                vendor=row.name,
                transport=transport,
                expected_schema_hash=row.schema_hash or "",
            )

        return ToolServerSpec(
            name=f"{row.workspace_id}:{row.name}",
            vendor=row.name,
            tools=names,
            build=build,
            server_id=server_id,
            required_args=_required_from_manifest(list(row.tools_manifest or [])),
        )

    async def _ensure_connected(self, spec: ToolServerSpec) -> UUID:
        lock = self._locks.setdefault(spec.name, asyncio.Lock())
        async with lock:
            config: MCPServerInstanceConfig | None = None
            existing = self.manager.get_server(spec.server_id)
            if existing is not None and existing.config.transport.is_alive():
                return spec.server_id
            try:
                config = await spec.build()
                await self.manager.connect_server(config)
            except ToolUnavailableError:
                raise
            except Exception as err:  # noqa: BLE001
                raise ToolUnavailableError(
                    f"Tool server '{spec.name}' could not be started: {err}"
                ) from err
            return config.server_id

    async def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        has_valid_approval_token: bool = False,
        session: AsyncSession | None = None,
        workspace_id: UUID | None = None,
    ) -> SanitizationResult:
        """Run one tool call. Raises ToolUnavailableError / PermissionDeniedError on refusal."""
        if tool_name not in self._tool_to_server and session is not None and workspace_id:
            await self.load_workspace_servers(session, workspace_id)
        server_name = self._tool_to_server.get(tool_name)
        if server_name is None:
            raise ToolUnavailableError(f"No tool server provides '{tool_name}'")
        server_id = await self._ensure_connected(self._specs[server_name])
        try:
            return await self.manager.invoke_tool(
                server_id=server_id,
                tool_name=tool_name,
                arguments=arguments,
                has_valid_approval_token=has_valid_approval_token,
            )
        except PermissionDeniedError:
            raise
        except MCPError as err:
            raise ToolUnavailableError(f"Tool '{tool_name}' failed: {err}") from err

    async def close(self) -> None:
        await self.manager.disconnect_all()
