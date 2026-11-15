"""``/api/v1/workspaces``: Multi-workspace management API.

Allows users to list, register, get details, disconnect, and switch default
project workspaces.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.files.folder import FolderError
from app.files.services import FileServices
from app.files.workspace_service import ProjectWorkspaceService

logger = logging.getLogger("maya")

router = APIRouter()

NO_STORE: Final = {"Cache-Control": "no-store"}


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "message": message, **extra},
        headers=NO_STORE,
    )


async def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


async def get_file_services(request: Request) -> FileServices:
    existing = getattr(request.app.state, "file_services", None)
    if isinstance(existing, FileServices):
        return existing
    settings = request.app.state.settings
    engine = getattr(getattr(request.app.state, "resources", None), "engine", None)
    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.files import services as file_services

    built = file_services.build_file_services(
        settings, engine if isinstance(engine, AsyncEngine) else None
    )
    request.app.state.file_services = built
    return built


async def get_workspace_service(request: Request) -> ProjectWorkspaceService:
    existing = getattr(request.app.state, "workspace_service", None)
    if isinstance(existing, ProjectWorkspaceService):
        return existing
    fs = getattr(request.app.state, "file_services", None)
    if isinstance(fs, FileServices) and fs.workspaces is not None:
        return fs.workspaces
    built = await get_file_services(request)
    return built.workspace_service


Caller = Annotated[Principal, Depends(get_principal)]
WorkspaceSvc = Annotated[ProjectWorkspaceService, Depends(get_workspace_service)]
Services = Annotated[FileServices, Depends(get_file_services)]
router.dependencies.append(Depends(_no_store))


class WorkspaceRegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    name: str | None = None


# --------------------------------------------------------------------------- routes


@router.get("", response_model=None)
@router.get("/", response_model=None, include_in_schema=False)
async def list_workspaces(
    principal: Caller,
    service: WorkspaceSvc,
) -> dict[str, Any]:
    """List all project workspaces for the current user."""
    items = await service.list(principal.workspace_id, principal.user_id)
    return {"workspaces": [ws.as_dict() for ws in items]}


@router.post("", status_code=201, response_model=None)
@router.post("/", status_code=201, response_model=None, include_in_schema=False)
async def register_workspace(
    body: WorkspaceRegisterBody,
    principal: Caller,
    service: WorkspaceSvc,
    services: Services,
) -> dict[str, Any] | JSONResponse:
    """Register a new project workspace directory."""
    try:
        ws = await service.register(
            principal.workspace_id,
            principal.user_id,
            path=body.path,
            name=body.name,
        )
    except FolderError as err:
        return _error(422, "invalid_folder", err.message, reason=err.code)

    await services.audit(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        tool="workspace_register",
        level="WRITE",
        detail={"id": ws.id, "name": ws.name, "path": ws.path},
    )
    return ws.as_dict()


@router.get("/{id}", response_model=None)
async def get_workspace(
    id: str,
    principal: Caller,
    service: WorkspaceSvc,
) -> dict[str, Any] | JSONResponse:
    """Get project workspace details and dynamic status."""
    ws = await service.get(principal.workspace_id, principal.user_id, id)
    if ws is None:
        return _error(404, "not_found", f"Workspace '{id}' not found.")
    return ws.as_dict()


@router.delete("/{id}", response_model=None)
async def disconnect_workspace(
    id: str,
    principal: Caller,
    service: WorkspaceSvc,
    services: Services,
) -> Response:
    """Disconnect a project workspace."""
    success = await service.disconnect(principal.workspace_id, principal.user_id, id)
    if not success:
        return _error(404, "not_found", f"Workspace '{id}' not found.")

    await services.audit(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        tool="workspace_disconnect",
        level="WRITE",
        detail={"id": id},
    )
    return Response(status_code=204, headers=NO_STORE)


@router.post("/{id}/default", response_model=None)
async def set_default_workspace(
    id: str,
    principal: Caller,
    service: WorkspaceSvc,
    services: Services,
) -> dict[str, Any] | JSONResponse:
    """Set a project workspace as the active/default workspace."""
    try:
        ws = await service.set_default(principal.workspace_id, principal.user_id, id)
    except FolderError as err:
        status_code = 404 if err.code == "not_found" else 422
        return _error(status_code, "workspace_error", err.message, reason=err.code)

    await services.audit(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        tool="workspace_set_default",
        level="WRITE",
        detail={"id": ws.id, "name": ws.name, "path": ws.path},
    )
    return ws.as_dict()
