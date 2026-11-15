"""Read-only status of the MCP client (ADR-0028): which servers are on and running.

No secrets, no arguments, no tool output. Starts nothing.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import get_principal
from app.auth.models import Principal

router = APIRouter(prefix="/api/v1/mcp-client", tags=["mcp-client"])


@router.get("/status")
async def mcp_client_status(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    """The client's switch, each server's state and the problems found in the servers file."""
    service = getattr(request.app.state, "mcp_client", None)
    if service is None:
        return {"enabled": False, "servers": [], "problems": []}
    return {"enabled": True, "servers": service.status(), "problems": service.problems}
