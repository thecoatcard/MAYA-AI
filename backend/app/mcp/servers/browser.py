"""Browser MCP Server and Permission Gating (Phase 13, Decisions D4, D5, D14).

Exposes browser interaction tools mapped to Maya's 4-tier permission model:
- browser_navigate: ToolTier.READ
- browser_screenshot: ToolTier.READ
- browser_get_dom: ToolTier.READ
- browser_click: ToolTier.APPROVAL_REQUIRED
- browser_fill_form: ToolTier.APPROVAL_REQUIRED
- browser_submit_payment: ToolTier.RESTRICTED

Enforces cryptographic payload binding and single-use approval token verification (Decision D5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection

from app.mcp.approvals import compute_payload_hash
from app.mcp.permissions import (
    PermissionDeniedError,
    ToolPermissionRegistry,
    ToolTier,
)
from app.mcp.tokens import (
    ApprovalTokenError,
    consume_approval_atomically,
    verify_approval_token,
)
from app.sandbox.browser_runner import BrowserRunner
from app.sandbox.browser_schemas import BrowserActionRequest

logger = logging.getLogger(__name__)

BROWSER_TOOLS: list[dict[str, Any]] = [
    {
        "name": "browser_navigate",
        "description": "Navigate the sandboxed browser to an allowed URL under egress security.",
        "tier": ToolTier.READ,
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Target HTTP or HTTPS URL to load"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_screenshot",
        "description": "Capture a screenshot of the current page viewport in base64 format.",
        "tier": ToolTier.READ,
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_get_dom",
        "description": "Extract the current page DOM text content or HTML summary.",
        "tier": ToolTier.READ,
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_click",
        "description": "Click an interactive element by CSS selector (requires approval).",
        "tier": ToolTier.APPROVAL_REQUIRED,
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector or element identifier"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_fill_form",
        "description": "Fill a form input field identified by selector (requires approval).",
        "tier": ToolTier.APPROVAL_REQUIRED,
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of input field"},
                "value": {"type": "string", "description": "Text value to fill into field"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_submit_payment",
        "description": "Submit a payment checkout form (RESTRICTED, requires step-up auth).",
        "tier": ToolTier.RESTRICTED,
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "Payment amount"},
            },
        },
    },
]


def register_browser_tools(
    registry: ToolPermissionRegistry | None = None,
) -> ToolPermissionRegistry:
    """Register all browser automation tools and schemas into the ToolPermissionRegistry."""
    target_registry = registry if registry is not None else ToolPermissionRegistry()

    target_registry.register_tool("browser_navigate", ToolTier.READ, required_args={"url"})
    target_registry.register_tool("browser_screenshot", ToolTier.READ, required_args=set())
    target_registry.register_tool("browser_get_dom", ToolTier.READ, required_args=set())
    target_registry.register_tool(
        "browser_click", ToolTier.APPROVAL_REQUIRED, required_args={"selector"}
    )
    target_registry.register_tool(
        "browser_fill_form", ToolTier.APPROVAL_REQUIRED, required_args={"selector"}
    )
    target_registry.register_tool(
        "browser_submit_payment", ToolTier.RESTRICTED, required_args=set()
    )

    return target_registry


@dataclass(frozen=True, slots=True)
class BrowserToolContext:
    """Authentication and approval context for dispatching a browser tool."""

    user_id: UUID
    workspace_id: UUID
    approval_token: str | None = None
    idempotency_key: str | None = None
    session_id: str | None = None
    db_connection: AsyncConnection | None = None


class BrowserToolHandler:
    """Dispatches browser tool invocations through Maya's 4-tier permission gate."""

    def __init__(
        self,
        runner: BrowserRunner | None = None,
        registry: ToolPermissionRegistry | None = None,
        secret_key: str | None = None,
    ) -> None:
        self.runner = runner or BrowserRunner()
        self.registry = registry or register_browser_tools()
        self.secret_key = secret_key or "maya-default-secret-key-for-local-testing-hs256!"

    async def dispatch_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: BrowserToolContext | None = None,
    ) -> dict[str, Any]:
        """Validate permissions and execute a browser tool invocation."""
        # 1. Permission classification & schema argument validation
        tier = self.registry.classify(tool_name)
        self.registry.validate_arguments(tool_name, args)

        session_id = context.session_id if context else None

        # 2. RESTRICTED check (Step-up auth required, autonomous execution blocked)
        if tier == ToolTier.RESTRICTED:
            raise PermissionDeniedError(
                f"Execution of RESTRICTED tool '{tool_name}' forbidden. "
                "Step-up authentication (TOTP) is required.",
                tool_name=tool_name,
            )

        # 3. APPROVAL_REQUIRED check (Requires single-use cryptographic approval token)
        if tier == ToolTier.APPROVAL_REQUIRED:
            if not context or not context.approval_token:
                raise PermissionDeniedError(
                    f"Execution of APPROVAL_REQUIRED tool '{tool_name}' "
                    "forbidden without valid human approval token.",
                    tool_name=tool_name,
                )

            # Compute RFC 8785 canonical hash of arguments bound to execution context
            payload_hash = compute_payload_hash(
                tool=tool_name,
                args=args,
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                idempotency_key=context.idempotency_key or "default",
            )

            # Verify cryptographic token signature and claim bindings
            claims = verify_approval_token(
                context.approval_token,
                self.secret_key,
                expected_workspace_id=context.workspace_id,
                expected_user_id=context.user_id,
                expected_tool_name=tool_name,
                expected_payload_hash=payload_hash,
            )

            # Consume token atomically in database if connection provided
            if context.db_connection is not None:
                consumed = await consume_approval_atomically(
                    context.db_connection,
                    request_id=claims.request_id,
                    workspace_id=claims.workspace_id,
                    payload_hash=claims.payload_hash,
                )
                if not consumed:
                    raise ApprovalTokenError("Approval token has already been consumed or expired.")

        # 4. Tool execution via BrowserRunner
        if tool_name == "browser_navigate":
            result = await self.runner.execute_action(
                BrowserActionRequest(
                    action_type="navigate",
                    url=str(args["url"]),
                    session_id=session_id,
                )
            )
        elif tool_name == "browser_screenshot":
            result = await self.runner.execute_action(
                BrowserActionRequest(
                    action_type="screenshot",
                    session_id=session_id,
                )
            )
        elif tool_name == "browser_get_dom":
            result = await self.runner.execute_action(
                BrowserActionRequest(
                    action_type="get_dom",
                    session_id=session_id,
                )
            )
        elif tool_name == "browser_click":
            result = await self.runner.execute_action(
                BrowserActionRequest(
                    action_type="click",
                    selector=str(args["selector"]),
                    session_id=session_id,
                )
            )
        elif tool_name == "browser_fill_form":
            val = str(args.get("value") or args.get("input_text") or "")
            result = await self.runner.execute_action(
                BrowserActionRequest(
                    action_type="fill",
                    selector=str(args["selector"]),
                    input_text=val,
                    session_id=session_id,
                )
            )
        else:
            raise PermissionDeniedError(
                f"Unhandled tool '{tool_name}' in browser tool handler.",
                tool_name=tool_name,
            )

        return {
            "success": result.success,
            "current_url": result.current_url,
            "dom_summary": result.dom_summary,
            "screenshot_base64": result.screenshot_base64,
            "bot_detected": result.bot_detected,
            "bot_type": result.bot_type,
            "error_message": result.error_message,
        }
