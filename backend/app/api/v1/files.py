"""``/api/v1/files``: the connected folder and uploaded files (contract: docs/api/FILES.md).

Every route is bearer-authenticated and scoped to the caller's workspace (and user for the
folder). Responses carry ``Cache-Control: no-store``. The folder path in a request body is the
only place a client names a path on the server's disk, and it is validated by ``FolderService``;
listing inside the folder takes a RELATIVE path that must pass the SafePath rules.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.files import services as file_services
from app.files.folder import FolderError
from app.files.multipart import MultipartError, parse_multipart
from app.files.safepath import SafePathError, is_within, relative_to_root
from app.files.services import FileServices
from app.files.uploads import MAX_FILES_PER_REQUEST, UploadError

logger = logging.getLogger("maya")

router = APIRouter(prefix="/api/v1/files", tags=["files"])

NO_STORE: Final = {"Cache-Control": "no-store"}
BODY_OVERHEAD_BYTES: Final = 1024 * 1024  # multipart framing around the file bytes
TREE_MAX_LIMIT: Final = 1000


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": code, "message": message, **extra}, headers=NO_STORE
    )


async def get_file_services(request: Request) -> FileServices:
    """The file services of this app (built once from its settings and database engine)."""
    existing = getattr(request.app.state, "file_services", None)
    if isinstance(existing, FileServices):
        return existing
    settings = request.app.state.settings
    engine = getattr(getattr(request.app.state, "resources", None), "engine", None)
    from sqlalchemy.ext.asyncio import AsyncEngine

    built = file_services.build_file_services(
        settings, engine if isinstance(engine, AsyncEngine) else None
    )
    request.app.state.file_services = built
    return built


async def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


Caller = Annotated[Principal, Depends(get_principal)]
Services = Annotated[FileServices, Depends(get_file_services)]
router.dependencies.append(Depends(_no_store))


class FolderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str


# ------------------------------------------------------------------------------- folder


@router.get("/folder")
async def get_folder(principal: Caller, services: Services) -> dict[str, object]:
    info = await services.folders.get(principal.workspace_id, principal.user_id)
    return info.as_dict()


@router.put("/folder", response_model=None)
async def put_folder(
    body: FolderBody, principal: Caller, services: Services
) -> dict[str, object] | JSONResponse:
    try:
        info = await services.folders.set(principal.workspace_id, principal.user_id, body.path)
    except FolderError as err:
        return _error(422, "invalid_folder", err.message, reason=err.code)
    await services.audit(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        tool="folder_connect",
        level="WRITE",
        detail={"name": info.name},
    )
    return info.as_dict()


@router.delete("/folder", status_code=204)
async def delete_folder(principal: Caller, services: Services) -> Response:
    await services.folders.clear(principal.workspace_id, principal.user_id)
    await services.audit(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        tool="folder_disconnect",
        level="WRITE",
        detail={},
    )
    return Response(status_code=204, headers=NO_STORE)


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _list_directory(root: Path, directory: Path, limit: int) -> tuple[list[dict[str, Any]], bool]:
    entries: list[dict[str, Any]] = []
    with os.scandir(directory) as scan:
        listed = sorted(scan, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    for entry in listed:
        if entry.name.startswith(".maya-write-test-"):
            continue
        try:
            if entry.is_symlink() and not is_within(Path(entry.path).resolve(), root):
                continue  # a link that leaves the folder is not shown
            stat = entry.stat()
            is_dir = entry.is_dir()
        except OSError:
            continue
        entries.append(
            {
                "name": entry.name,
                "type": "dir" if is_dir else "file",
                "size": None if is_dir else stat.st_size,
                "modified_at": _iso(stat.st_mtime),
            }
        )
        if len(entries) >= limit:
            return entries, len(listed) > len(entries)
    return entries, False


@router.get("/folder/tree", response_model=None)
async def folder_tree(
    principal: Caller,
    services: Services,
    path: Annotated[str, Query(max_length=1024)] = "",
    limit: Annotated[int, Query(ge=1, le=TREE_MAX_LIMIT)] = 200,
) -> dict[str, object] | JSONResponse:
    root = await services.folders.usable_root(principal.workspace_id, principal.user_id)
    if root is None:
        return _error(409, "no_folder", "No usable folder is connected.")
    try:
        directory = services.folders.resolve(root, path, allow_root=True)
    except SafePathError as err:
        return _error(400, "invalid_path", str(err), reason=err.code)
    if not directory.is_dir():
        return _error(404, "not_found", "That folder does not exist.")
    entries, truncated = await asyncio.to_thread(_list_directory, root, directory, limit)
    return {
        "path": relative_to_root(root, directory),
        "entries": entries,
        "truncated": truncated,
    }


# ------------------------------------------------------------------------------ uploads


@router.post("/uploads", status_code=201, response_model=None)
async def upload_files(
    request: Request, principal: Caller, services: Services
) -> list[dict[str, object]] | JSONResponse:
    store = services.uploads
    cap = store.total_max_bytes + BODY_OVERHEAD_BYTES
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > cap:
        return _error(413, "payload_too_large", "The upload is larger than the allowed total.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > cap:
            return _error(413, "payload_too_large", "The upload is larger than the allowed total.")
    try:
        parts = parse_multipart(request.headers.get("content-type", ""), bytes(body))
    except MultipartError:
        return _error(400, "bad_multipart", "The upload body is not valid multipart form data.")
    del body
    files = [p for p in parts if p.name == "files" and p.filename is not None]
    if not 1 <= len(files) <= MAX_FILES_PER_REQUEST:
        return _error(
            400, "file_count", f"Send between 1 and {MAX_FILES_PER_REQUEST} files in 'files'."
        )
    if sum(len(p.data) for p in files) > store.total_max_bytes:
        return _error(
            413,
            "total_too_large",
            f"The files together are larger than {store.total_max_bytes // (1024 * 1024)} MB.",
        )
    # Validate every file before saving any, so a refused file leaves nothing behind.
    for part in files:
        try:
            store.validate(part.filename or "", part.data)
        except UploadError as err:
            return _error(err.status, err.code, err.message, file=(part.filename or "")[:120])
    saved: list[dict[str, object]] = []
    for part in files:
        try:
            record = await asyncio.to_thread(
                store.save,
                principal.workspace_id,
                principal.user_id,
                part.filename or "",
                part.data,
            )
        except UploadError as err:
            return _error(err.status, err.code, err.message, file=(part.filename or "")[:120])
        except OSError:
            logger.error("upload_store_failed")
            return _error(500, "storage_failed", "The file could not be stored.")
        saved.append(record.public())
        await services.audit(
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            tool="file_upload",
            level="WRITE",
            detail={"id": record.id, "name": record.name, "kind": record.kind, "size": record.size},
        )
    return saved


@router.get("/uploads", response_model=None)
async def list_uploads(
    principal: Caller,
    services: Services,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, object] | JSONResponse:
    try:
        records, next_cursor = await asyncio.to_thread(
            services.uploads.list, principal.workspace_id, limit, cursor
        )
    except UploadError as err:
        return _error(err.status, err.code, err.message)
    return {"items": [r.public() for r in records], "next_cursor": next_cursor}


@router.get("/uploads/{upload_id}", response_model=None)
async def get_upload(
    upload_id: str, principal: Caller, services: Services
) -> dict[str, object] | JSONResponse:
    record = await asyncio.to_thread(services.uploads.get, principal.workspace_id, upload_id)
    if record is None:
        return _error(404, "upload_not_found", "That file was not found.")
    return record.public()


@router.get("/uploads/{upload_id}/download", response_model=None)
async def download_upload(
    upload_id: str, principal: Caller, services: Services
) -> FileResponse | JSONResponse:
    record = await asyncio.to_thread(services.uploads.get, principal.workspace_id, upload_id)
    path = services.uploads.path_of(record) if record is not None else None
    if record is None or path is None:
        return _error(404, "upload_not_found", "That file was not found.")
    return FileResponse(
        path=str(path),
        media_type=record.content_type,
        filename=record.name,
        content_disposition_type="attachment",
        headers={
            **NO_STORE,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "X-Upload-Sha256": record.sha256,
        },
    )


@router.delete("/uploads/{upload_id}", status_code=204, response_model=None)
async def delete_upload(upload_id: str, principal: Caller, services: Services) -> Response:
    removed = await asyncio.to_thread(services.uploads.delete, principal.workspace_id, upload_id)
    if not removed:
        return _error(404, "upload_not_found", "That file was not found.")
    await services.audit(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        tool="file_upload_delete",
        level="WRITE",
        detail={"id": upload_id[:36]},
    )
    return Response(status_code=204, headers=NO_STORE)
