from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.resources import DependencyUnavailable

router = APIRouter(prefix="/api/v1", tags=["health"])


class HealthStatus(BaseModel):
    status: Literal["alive", "ready", "unavailable"]


@router.get("/healthz", response_model=HealthStatus)
async def liveness() -> HealthStatus:
    return HealthStatus(status="alive")


@router.get("/readyz", response_model=HealthStatus, responses={503: {"model": HealthStatus}})
async def readiness(request: Request) -> HealthStatus | JSONResponse:
    try:
        await request.app.state.resources.check()
    except DependencyUnavailable:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return HealthStatus(status="ready")
