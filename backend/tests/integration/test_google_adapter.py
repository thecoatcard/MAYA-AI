"""Integration tests for In-House Google Adapter (Phase 08)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app.mcp.permissions import PermissionDeniedError
from mcp_servers.google_adapter.calendar import GoogleCalendarService
from mcp_servers.google_adapter.gmail import GoogleGmailService
from mcp_servers.google_adapter.server import create_google_adapter_app

# ============================================================================
# 1. Gmail Service Unit / Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_gmail_search_and_draft_creation() -> None:
    service = GoogleGmailService(access_token="fake-token-sample-123")

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "messages": [{"id": "m1", "threadId": "t1"}],
                "resultSizeEstimate": 1,
            },
        )
        results = await service.search_emails(query="is:unread", max_results=5)
        assert len(results) == 1
        assert results[0]["id"] == "m1"

    # Create draft (autonomous WRITE)
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "draft-999", "message": {"id": "m-draft"}},
        )
        draft = await service.create_draft(
            to="recipient@example.com",
            subject="Status Update",
            body="Here is the report.",
        )
        assert draft["id"] == "draft-999"


@pytest.mark.asyncio
async def test_gmail_send_approval_token_enforcement() -> None:
    service = GoogleGmailService(access_token="fake-token-sample-123")

    # Send without approval token must raise PermissionDeniedError
    with pytest.raises(PermissionDeniedError) as exc_info:
        await service.send_email(
            to="recipient@example.com",
            subject="Approved Email",
            body="Important action",
            has_valid_approval_token=False,
        )
    assert "forbidden without valid approval token" in str(exc_info.value).lower()

    # Send with approval token succeeds
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "sent-msg-777", "threadId": "t-777"},
        )
        sent = await service.send_email(
            to="recipient@example.com",
            subject="Approved Email",
            body="Important action",
            has_valid_approval_token=True,
        )
        assert sent["id"] == "sent-msg-777"


# ============================================================================
# 2. Calendar Service Tests
# ============================================================================


@pytest.mark.asyncio
async def test_calendar_list_and_get_events() -> None:
    service = GoogleCalendarService(access_token="fake-token-sample-123")

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "items": [
                    {
                        "id": "event-1",
                        "summary": "Sprint Planning",
                        "start": {"dateTime": "2026-11-07T10:00:00Z"},
                        "end": {"dateTime": "2026-11-07T11:00:00Z"},
                    }
                ]
            },
        )
        events = await service.list_events()
        assert len(events) == 1
        assert events[0]["summary"] == "Sprint Planning"


# ============================================================================
# 3. Google Adapter MCP Server App Tests
# ============================================================================


def test_google_adapter_mcp_server_protocol() -> None:
    app = create_google_adapter_app(access_token="fake-token-sample-123")
    client = TestClient(app)

    # 1. tools/list request
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("jsonrpc") == "2.0"
    tools = data["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "gmail_search" in tool_names
    assert "gmail_create_draft" in tool_names
    assert "gmail_send" in tool_names
    assert "calendar_list_events" in tool_names

    # 2. tools/call request for create_draft
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "draft-101", "message": {"id": "m-101"}},
        )
        call_resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "gmail_create_draft",
                    "arguments": {
                        "to": "dev@maya.ai",
                        "subject": "PR review",
                        "body": "Looks great!",
                    },
                },
            },
        )
        assert call_resp.status_code == 200
        call_data = call_resp.json()
        assert call_data.get("result", {}).get("status") == "ok"
        assert "draft-101" in str(call_data["result"])
