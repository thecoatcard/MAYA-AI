"""Streamable Starlette HTTP MCP Server for Google Adapter (Phase 08)."""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.mcp.permissions import PermissionDeniedError
from mcp_servers.google_adapter.calendar import GoogleCalendarService
from mcp_servers.google_adapter.gmail import GoogleGmailService

logger = logging.getLogger(__name__)

GOOGLE_TOOLS_MANIFEST: list[dict[str, Any]] = [
    {
        "name": "gmail_search",
        "description": "Search user's Gmail mailbox using query strings",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query"},
                "max_results": {"type": "integer", "description": "Max results to return"},
            },
        },
    },
    {
        "name": "gmail_read_thread",
        "description": "Read full email thread content by thread ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "Gmail thread ID to fetch"},
            },
            "required": ["thread_id"],
        },
    },
    {
        "name": "gmail_create_draft",
        "description": "Create an email draft autonomously without sending",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Subject of the email"},
                "body": {"type": "string", "description": "Body content of the email"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail_send",
        "description": "Send an email immediately requiring human approval token",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Subject of the email"},
                "body": {"type": "string", "description": "Body content of the email"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "calendar_list_events",
        "description": "List upcoming events from user primary Google Calendar",
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "description": "Calendar ID (default primary)"},
                "time_min": {"type": "string", "description": "Start ISO datetime"},
                "time_max": {"type": "string", "description": "End ISO datetime"},
                "max_results": {"type": "integer", "description": "Max events to return"},
            },
        },
    },
    {
        "name": "calendar_get_event",
        "description": "Get detailed information for a specific calendar event",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID"},
                "calendar_id": {"type": "string", "description": "Calendar ID"},
            },
            "required": ["event_id"],
        },
    },
]


def create_google_adapter_app(access_token: str) -> Starlette:
    """Create and configure Starlette application exposing Google Adapter MCP tools."""
    gmail_service = GoogleGmailService(access_token=access_token)
    calendar_service = GoogleCalendarService(access_token=access_token)

    async def mcp_endpoint(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )

        req_id = body.get("id")
        method = body.get("method")
        params = body.get("params", {}) or {}

        if method == "ping":
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"status": "ok"}})

        if method == "tools/list":
            return JSONResponse(
                {"jsonrpc": "2.0", "id": req_id, "result": {"tools": GOOGLE_TOOLS_MANIFEST}}
            )

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            try:
                res: Any = None
                if tool_name == "gmail_search":
                    res = await gmail_service.search_emails(
                        query=arguments.get("query", ""),
                        max_results=int(arguments.get("max_results", 10)),
                    )
                elif tool_name == "gmail_read_thread":
                    res = await gmail_service.read_thread(thread_id=arguments["thread_id"])
                elif tool_name == "gmail_create_draft":
                    res = await gmail_service.create_draft(
                        to=arguments["to"],
                        subject=arguments["subject"],
                        body=arguments["body"],
                    )
                elif tool_name == "gmail_send":
                    has_token = bool(arguments.get("has_valid_approval_token", False))
                    res = await gmail_service.send_email(
                        to=arguments["to"],
                        subject=arguments["subject"],
                        body=arguments["body"],
                        has_valid_approval_token=has_token,
                    )
                elif tool_name == "calendar_list_events":
                    res = await calendar_service.list_events(
                        calendar_id=arguments.get("calendar_id", "primary"),
                        time_min=arguments.get("time_min"),
                        time_max=arguments.get("time_max"),
                        max_results=int(arguments.get("max_results", 20)),
                    )
                elif tool_name == "calendar_get_event":
                    res = await calendar_service.get_event(
                        event_id=arguments["event_id"],
                        calendar_id=arguments.get("calendar_id", "primary"),
                    )
                else:
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32601, "message": f"Method not found: {tool_name}"},
                        },
                        status_code=404,
                    )

                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "status": "ok",
                            "content": [{"type": "text", "text": json.dumps(res)}],
                            "data": res,
                        },
                    }
                )

            except PermissionDeniedError as perm_err:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32000, "message": str(perm_err)},
                    },
                    status_code=403,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Error executing tool %s: %s", tool_name, exc)
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": f"Internal error: {exc}"},
                    },
                    status_code=500,
                )

        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            },
            status_code=404,
        )

    routes = [
        Route("/mcp", mcp_endpoint, methods=["POST"]),
        Route("/", mcp_endpoint, methods=["POST"]),
    ]

    return Starlette(debug=False, routes=routes)
