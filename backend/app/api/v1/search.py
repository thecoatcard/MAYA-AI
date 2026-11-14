"""Read-only status of the native web search: the provider order and, per engine, ``ok``,
``cooling`` (until a time) or ``blocked``. No keys, no queries, no content. Starts nothing."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import get_principal
from app.auth.models import Principal

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.get("/status")
async def search_status(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    service = getattr(request.app.state, "web_search_service", None)
    chain = getattr(service, "chain", None)
    if chain is None:
        return {"available": False, "order": [], "google_grounding": False, "providers": []}
    status: dict[str, Any] = chain.status()
    return {"available": True, **status}
