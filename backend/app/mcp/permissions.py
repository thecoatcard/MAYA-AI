"""Four-tier permission classification with deny-by-default behavior (Decision D4)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ToolTier(StrEnum):
    """Execution permission tiers for tools in Maya AI."""

    READ = "READ"
    WRITE = "WRITE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    RESTRICTED = "RESTRICTED"

    @property
    def rank(self) -> int:
        ranks = {
            ToolTier.READ: 1,
            ToolTier.WRITE: 2,
            ToolTier.APPROVAL_REQUIRED: 3,
            ToolTier.RESTRICTED: 4,
        }
        return ranks[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ToolTier):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, ToolTier):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, ToolTier):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, ToolTier):
            return NotImplemented
        return self.rank >= other.rank


class PermissionDeniedError(Exception):
    """Raised when an unrecognized tool is denied by default or execution is forbidden."""

    def __init__(self, message: str, tool_name: str | None = None) -> None:
        super().__init__(message)
        self.tool_name = tool_name


DEFAULT_TOOL_PERMISSIONS: dict[str, ToolTier] = {
    # 🟢 READ — Read-only, no side effects
    "file_read": ToolTier.READ,
    "file_list": ToolTier.READ,
    "git_status": ToolTier.READ,
    "git_diff": ToolTier.READ,
    "git_log": ToolTier.READ,
    "sql_select": ToolTier.READ,
    "web_search": ToolTier.READ,
    "github_list_repositories": ToolTier.READ,
    "github_get_repo": ToolTier.READ,
    "github_list_tree": ToolTier.READ,
    "github_read_file": ToolTier.READ,
    "github_list_commits": ToolTier.READ,
    "github_list_pull_requests": ToolTier.READ,
    "github_list_issues": ToolTier.READ,
    "github_review_repo": ToolTier.READ,
    "read_emails": ToolTier.READ,
    "read_calendar": ToolTier.READ,
    "fetch_web_page": ToolTier.READ,
    "gmail_search": ToolTier.READ,
    "gmail_read_thread": ToolTier.READ,
    "notion_get_page": ToolTier.READ,
    "notion_query_database": ToolTier.READ,
    # Read-only mailbox tools (app/mail/tools.py): EXAMINE + BODY.PEEK, nothing is changed.
    "mail_list_recent": ToolTier.READ,
    "mail_search": ToolTier.READ,
    "mail_read": ToolTier.READ,
    # Read-only Notion tools for chat, voice and the gateway (app/mcp/notion_tools.py).
    "notion_search": ToolTier.READ,
    "notion_list_tasks": ToolTier.READ,
    "notion_list_databases": ToolTier.READ,
    "notion_get_database": ToolTier.READ,
    "notion_find_page": ToolTier.READ,
    # Shows a file's text (GitHub, connected folder or upload); never writes (app/mcp/show_code.py).
    "show_code": ToolTier.READ,
    "calendar_get_event": ToolTier.READ,
    "calendar_list_events": ToolTier.READ,
    "browser_navigate": ToolTier.READ,
    "browser_screenshot": ToolTier.READ,
    "browser_get_dom": ToolTier.READ,
    # 🔵 WRITE — Sandboxed mutations strictly confined to /artifacts or sandbox
    "file_write_artifact": ToolTier.WRITE,
    # Connected-folder tools (ADR-0022): they only create NEW files; there is no delete tool.
    "file_write_new": ToolTier.WRITE,
    "file_edit_as_new_version": ToolTier.WRITE,
    "git_commit_local": ToolTier.WRITE,
    "git_branch": ToolTier.WRITE,
    "draft_email": ToolTier.WRITE,
    "gmail_create_draft": ToolTier.WRITE,
    "generate_document": ToolTier.WRITE,
    "run_sandbox_command": ToolTier.WRITE,
    # 🟡 APPROVAL_REQUIRED — External side effects, mutations outside sandbox
    "gmail_send": ToolTier.APPROVAL_REQUIRED,
    # Mailbox sends over SMTP (app/mail/tools.py): each runs only after the user approved its
    # exact recipients, subject and body on the task card.
    "mail_send": ToolTier.APPROVAL_REQUIRED,
    "mail_reply": ToolTier.APPROVAL_REQUIRED,
    "git_push": ToolTier.APPROVAL_REQUIRED,
    "open_pr": ToolTier.APPROVAL_REQUIRED,
    "github_create_pull_request": ToolTier.APPROVAL_REQUIRED,
    "delete_file_outside_artifacts": ToolTier.APPROVAL_REQUIRED,
    "file_replace_original": ToolTier.APPROVAL_REQUIRED,
    # Notion writes (app/mcp/notion_write.py): each runs only after the user approved its exact
    # payload on the task card.
    "notion_create_page": ToolTier.APPROVAL_REQUIRED,
    "notion_append_blocks": ToolTier.APPROVAL_REQUIRED,
    "notion_add_row": ToolTier.APPROVAL_REQUIRED,
    "notion_update_row": ToolTier.APPROVAL_REQUIRED,
    # Edits of existing Notion content (app/mcp/notion_edit.py); deletes go to the Notion trash.
    "notion_update_page": ToolTier.APPROVAL_REQUIRED,
    "notion_archive_page": ToolTier.APPROVAL_REQUIRED,
    "notion_update_block": ToolTier.APPROVAL_REQUIRED,
    "notion_delete_block": ToolTier.APPROVAL_REQUIRED,
    "notion_add_comment": ToolTier.APPROVAL_REQUIRED,
    "notion_replace_section": ToolTier.APPROVAL_REQUIRED,
    # A whole book from a finished job (app/mcp/notion_book.py, ADR-0031): the approval is bound to
    # the exact Markdown (SHA-256) and is never auto-approved.
    "notion_create_page_from_job": ToolTier.APPROVAL_REQUIRED,
    # Runtime tools (app/runtime_tools): records the user's approval of one tool version's exact
    # source and capabilities. It runs no code; execution happens in the sandbox via chat.
    "runtime_tool_install": ToolTier.APPROVAL_REQUIRED,
    # MCP client (app/mcp_client, ADR-0028): runs ONE call of a third-party MCP server after the
    # user approved its exact server, tool and arguments. Third-party tools are never classified
    # here by name: a listed read-only tool runs in chat, every other tool reaches this entry.
    "mcp_client_call": ToolTier.APPROVAL_REQUIRED,
    "browser_click": ToolTier.APPROVAL_REQUIRED,
    "browser_fill_form": ToolTier.APPROVAL_REQUIRED,
    # 🔴 RESTRICTED — High-risk, destructive actions requiring step-up auth
    "database_ddl": ToolTier.RESTRICTED,
    "bulk_delete": ToolTier.RESTRICTED,
    "git_force_push": ToolTier.RESTRICTED,
    "execute_payment": ToolTier.RESTRICTED,
    "create_api_token": ToolTier.RESTRICTED,
    "browser_submit_payment": ToolTier.RESTRICTED,
}


# Obvious synonyms a planner may invent, mapped to the classified tool they mean. An alias only
# renames: the tier is always the target's own, and a target above WRITE is never aliased, so a
# synonym can never turn an APPROVAL_REQUIRED or RESTRICTED action into an automatic one.
TOOL_ALIASES: dict[str, str] = {
    "save_file": "file_write_artifact",
    "write_file": "file_write_artifact",
    "export_file": "file_write_artifact",
    "store_file": "file_write_artifact",
    "save_document": "file_write_artifact",
    "save_artifact": "file_write_artifact",
    "write_artifact": "file_write_artifact",
}
SAVE_TOOL_NAMES: frozenset[str] = frozenset(TOOL_ALIASES)


def resolve_tool_alias(tool_name: str) -> str:
    """The classified name a synonym stands for (``tool_name`` itself when it is not one)."""
    return TOOL_ALIASES.get(tool_name, tool_name)


class ToolPermissionRegistry:
    """Registry maintaining tool-tier mappings with deny-by-default enforcement."""

    def __init__(self, initial_permissions: dict[str, ToolTier] | None = None) -> None:
        self._permissions: dict[str, ToolTier] = (
            dict(initial_permissions)
            if initial_permissions is not None
            else dict(DEFAULT_TOOL_PERMISSIONS)
        )
        self._schemas: dict[str, set[str]] = {}
        self._frozen: bool = False

    def freeze(self) -> None:
        """Freeze the registry to make all tool-tier mappings immutable at runtime."""
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def register_tool(
        self,
        tool_name: str,
        tier: ToolTier,
        required_args: set[str] | None = None,
    ) -> None:
        """Register or update a tool's permission tier and optional required argument schema."""
        if self._frozen:
            raise PermissionDeniedError(
                "ToolPermissionRegistry is frozen and immutable during runtime execution.",
                tool_name=tool_name,
            )
        self._permissions[tool_name] = tier
        if required_args is not None:
            self._schemas[tool_name] = set(required_args)

    def unregister_tool(self, tool_name: str) -> None:
        """Remove a tool from the registry."""
        if self._frozen:
            raise PermissionDeniedError(
                "ToolPermissionRegistry is frozen and immutable during runtime execution.",
                tool_name=tool_name,
            )
        self._permissions.pop(tool_name, None)
        self._schemas.pop(tool_name, None)

    def validate_arguments(self, tool_name: str, args: dict[str, Any]) -> None:
        """Validate tool invocation arguments against registered schema constraints."""
        required = self._schemas.get(tool_name)
        if required:
            missing = required - set(args.keys())
            if missing:
                raise PermissionDeniedError(
                    f"Tool '{tool_name}' missing required arguments: {sorted(missing)}",
                    tool_name=tool_name,
                )

    def authorize_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        has_valid_approval_token: bool = False,
        server_hints: dict[str, Any] | None = None,
    ) -> ToolTier:
        """Perform an independent pre-execution authorization check."""
        tier = self.classify(tool_name, server_hints)
        self.validate_arguments(self.canonical_name(tool_name), args)

        if tier in (ToolTier.APPROVAL_REQUIRED, ToolTier.RESTRICTED):
            if not has_valid_approval_token:
                raise PermissionDeniedError(
                    f"Execution of {tier.value} tool '{tool_name}' "
                    "forbidden without valid approval token",
                    tool_name=tool_name,
                )

        return tier

    def canonical_name(self, tool_name: str) -> str:
        """Resolve a synonym to its classified tool; any other name is returned unchanged.

        A registered name always wins over an alias, and an alias is followed only when its
        target is registered at READ or WRITE tier.
        """
        if tool_name in self._permissions:
            return tool_name
        target = TOOL_ALIASES.get(tool_name)
        if target is not None and self._permissions.get(target) in (ToolTier.READ, ToolTier.WRITE):
            return target
        return tool_name

    def is_registered(self, tool_name: str) -> bool:
        """Check if a tool is explicitly registered."""
        return tool_name in self._permissions

    def classify(
        self,
        tool_name: str,
        server_hints: dict[str, Any] | None = None,
    ) -> ToolTier:
        """Classify a tool's tier.

        Deny-by-default: Unregistered tools raise PermissionDeniedError.
        Server hints policy: Hints can ONLY elevate a tier, never lower it.
        A known synonym (``TOOL_ALIASES``) is classified as the tool it stands for.
        """
        tool_name = self.canonical_name(tool_name)
        if tool_name not in self._permissions:
            raise PermissionDeniedError(
                f"Tool '{tool_name}' is not registered and is denied by default.",
                tool_name=tool_name,
            )

        assigned_tier = self._permissions[tool_name]

        if server_hints is None:
            return assigned_tier

        # Destructive hint elevates at least to APPROVAL_REQUIRED, or preserves RESTRICTED
        if server_hints.get("destructiveHint") is True:
            if assigned_tier < ToolTier.APPROVAL_REQUIRED:
                return ToolTier.APPROVAL_REQUIRED

        return assigned_tier

    def is_allowed_autonomous(
        self,
        tool_name: str,
        server_hints: dict[str, Any] | None = None,
    ) -> bool:
        """Check if a tool is permitted to execute autonomously (READ or WRITE)."""
        try:
            tier = self.classify(tool_name, server_hints)
            return tier in (ToolTier.READ, ToolTier.WRITE)
        except PermissionDeniedError:
            return False

    def requires_human_approval(
        self,
        tool_name: str,
        server_hints: dict[str, Any] | None = None,
    ) -> bool:
        """Check if a tool requires human approval before execution."""
        try:
            tier = self.classify(tool_name, server_hints)
            return tier in (ToolTier.APPROVAL_REQUIRED, ToolTier.RESTRICTED)
        except PermissionDeniedError:
            return True  # Unknown tools cannot run without explicit review

    def requires_step_up_auth(
        self,
        tool_name: str,
        server_hints: dict[str, Any] | None = None,
    ) -> bool:
        """Check if a tool requires step-up authentication (TOTP)."""
        try:
            tier = self.classify(tool_name, server_hints)
            return tier == ToolTier.RESTRICTED
        except PermissionDeniedError:
            return True
