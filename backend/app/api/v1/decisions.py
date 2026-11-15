"""Read-only status of the decision model (ADR-0029): switch, circuit state, counters per purpose.

No keys, no content. Starts nothing.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import get_principal
from app.auth.models import Principal

router = APIRouter(prefix="/api/v1/decisions", tags=["decisions"])


@router.get("/status")
async def decisions_status(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    service = getattr(request.app.state, "decision_service", None)
    settings = request.app.state.settings
    if service is None:
        return {
            "enabled": False,
            "model": settings.decisions_model,
            "circuit": "closed",
            "disabled_purposes": [],
            "purposes": {},
            "startup_check": {"state": "failed", "reason": "no decision service"},
        }
    status: dict[str, Any] = service.status()
    return status
