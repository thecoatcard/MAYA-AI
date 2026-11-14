"""Integration tests for Browser Tool Suite and 4-Tier Gate (Phase 13, Decisions D4, D5)."""

from __future__ import annotations

import uuid

import pytest

from app.mcp.approvals import compute_payload_hash
from app.mcp.permissions import (
    PermissionDeniedError,
    ToolPermissionRegistry,
    ToolTier,
)
from app.mcp.servers.browser import (
    BROWSER_TOOLS,
    BrowserToolContext,
    BrowserToolHandler,
    register_browser_tools,
)
from app.mcp.tokens import (
    ApprovalTokenError,
    mint_approval_token,
)
from app.sandbox.browser_runner import BrowserRunner
from app.sandbox.egress_filter import EgressSecurityFilter


@pytest.fixture
def test_secret_key() -> str:
    return "test-secret-key-32-bytes-minimum-length-for-hs256!"


@pytest.fixture
def permission_registry() -> ToolPermissionRegistry:
    registry = ToolPermissionRegistry()
    register_browser_tools(registry)
    return registry


@pytest.fixture
def browser_handler(
    test_secret_key: str, permission_registry: ToolPermissionRegistry
) -> BrowserToolHandler:
    runner = BrowserRunner(
        egress_filter=EgressSecurityFilter(dns_resolver=lambda h: ["93.184.216.34"]),
        use_simulation=True,
    )
    return BrowserToolHandler(
        runner=runner,
        registry=permission_registry,
        secret_key=test_secret_key,
    )


class TestBrowserToolPermissions:
    def test_browser_tool_tier_classifications(
        self, permission_registry: ToolPermissionRegistry
    ) -> None:
        assert permission_registry.classify("browser_navigate") == ToolTier.READ
        assert permission_registry.classify("browser_screenshot") == ToolTier.READ
        assert permission_registry.classify("browser_get_dom") == ToolTier.READ
        assert permission_registry.classify("browser_click") == ToolTier.APPROVAL_REQUIRED
        assert permission_registry.classify("browser_fill_form") == ToolTier.APPROVAL_REQUIRED
        assert permission_registry.classify("browser_submit_payment") == ToolTier.RESTRICTED

    def test_registered_tool_metadata(self) -> None:
        tool_names = {t["name"] for t in BROWSER_TOOLS}
        assert "browser_navigate" in tool_names
        assert "browser_screenshot" in tool_names
        assert "browser_get_dom" in tool_names
        assert "browser_click" in tool_names
        assert "browser_fill_form" in tool_names
        assert "browser_submit_payment" in tool_names

    @pytest.mark.asyncio
    async def test_read_tools_execute_autonomously_without_token(
        self, browser_handler: BrowserToolHandler
    ) -> None:
        browser_handler.runner.set_simulated_page(
            "https://example.com/info",
            html="<html><body><h1>Test Page</h1></body></html>",
        )

        # 1. browser_navigate
        nav_res = await browser_handler.dispatch_tool_call(
            "browser_navigate",
            {"url": "https://example.com/info"},
        )
        assert nav_res["success"] is True
        assert nav_res["current_url"] == "https://example.com/info"

        # 2. browser_screenshot
        shot_res = await browser_handler.dispatch_tool_call(
            "browser_screenshot",
            {},
        )
        assert shot_res["success"] is True
        assert shot_res["screenshot_base64"] is not None

        # 3. browser_get_dom
        dom_res = await browser_handler.dispatch_tool_call(
            "browser_get_dom",
            {},
        )
        assert dom_res["success"] is True
        assert "Test Page" in dom_res["dom_summary"]

    @pytest.mark.asyncio
    async def test_mutating_tools_rejected_without_approval_token(
        self, browser_handler: BrowserToolHandler
    ) -> None:
        with pytest.raises(PermissionDeniedError) as exc_info:
            await browser_handler.dispatch_tool_call(
                "browser_click",
                {"selector": "#submit-btn"},
            )
        assert "approval" in str(exc_info.value).lower()

        with pytest.raises(PermissionDeniedError) as exc_info:
            await browser_handler.dispatch_tool_call(
                "browser_fill_form",
                {"selector": "#username", "value": "testuser"},
            )
        assert "approval" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_mutating_tool_succeeds_with_valid_approval_token(
        self, browser_handler: BrowserToolHandler, test_secret_key: str
    ) -> None:
        workspace_id = uuid.uuid4()
        user_id = uuid.uuid4()
        request_id = uuid.uuid4()
        idempotency_key = "idem-key-123"

        browser_handler.runner.set_simulated_page(
            "https://example.com/form",
            html="<html><body><button id='action-btn'>Click</button></body></html>",
        )
        await browser_handler.dispatch_tool_call(
            "browser_navigate",
            {"url": "https://example.com/form"},
        )

        args = {"selector": "#action-btn"}
        payload_hash = compute_payload_hash(
            tool="browser_click",
            args=args,
            workspace_id=workspace_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
        )

        valid_token, _ = mint_approval_token(
            test_secret_key,
            user_id=user_id,
            workspace_id=workspace_id,
            request_id=request_id,
            tool_name="browser_click",
            payload_hash=payload_hash,
        )

        context = BrowserToolContext(
            user_id=user_id,
            workspace_id=workspace_id,
            approval_token=valid_token,
            idempotency_key=idempotency_key,
        )

        # Dispatch with valid token
        res = await browser_handler.dispatch_tool_call(
            "browser_click",
            args,
            context=context,
        )
        assert res["success"] is True

    @pytest.mark.asyncio
    async def test_mutating_tool_fails_with_tampered_token_or_payload_mismatch(
        self, browser_handler: BrowserToolHandler, test_secret_key: str
    ) -> None:
        workspace_id = uuid.uuid4()
        user_id = uuid.uuid4()
        request_id = uuid.uuid4()
        idempotency_key = "idem-key-456"

        args = {"selector": "#delete-all"}
        payload_hash = compute_payload_hash(
            tool="browser_click",
            args=args,
            workspace_id=workspace_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
        )

        valid_token, _ = mint_approval_token(
            test_secret_key,
            user_id=user_id,
            workspace_id=workspace_id,
            request_id=request_id,
            tool_name="browser_click",
            payload_hash=payload_hash,
        )

        # 1. Payload arguments mismatch
        mismatched_context = BrowserToolContext(
            user_id=user_id,
            workspace_id=workspace_id,
            approval_token=valid_token,
            idempotency_key=idempotency_key,
        )
        with pytest.raises(ApprovalTokenError):
            await browser_handler.dispatch_tool_call(
                "browser_click",
                {"selector": "#different-selector"},  # Mismatch from approved args!
                context=mismatched_context,
            )

        # 2. Tampered token string
        tampered_context = BrowserToolContext(
            user_id=user_id,
            workspace_id=workspace_id,
            approval_token=valid_token + "tampered",
            idempotency_key=idempotency_key,
        )
        with pytest.raises(ApprovalTokenError):
            await browser_handler.dispatch_tool_call(
                "browser_click",
                args,
                context=tampered_context,
            )

    @pytest.mark.asyncio
    async def test_restricted_payment_tool_rejected_by_default(
        self, browser_handler: BrowserToolHandler
    ) -> None:
        with pytest.raises(PermissionDeniedError) as exc_info:
            await browser_handler.dispatch_tool_call(
                "browser_submit_payment",
                {"amount": 100.0, "currency": "USD"},
            )
        assert "restricted" in str(exc_info.value).lower()


def test_browser_handler_has_no_default_signing_key() -> None:
    """A handler without an explicit key must refuse to exist (no forgeable built-in default)."""
    for missing in (None, "", "   "):
        with pytest.raises(ValueError, match="signing key"):
            BrowserToolHandler(secret_key=missing)
