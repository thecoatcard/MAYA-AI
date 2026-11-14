"""Artifact Delivery and Download API."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.dependencies import (
    TenantDatabase,
    get_file_link_principal,
    get_file_link_tenant_database,
    get_principal,
    get_tenant_database,
)
from app.auth.models import Principal
from app.db.models import FileArtifact
from app.db.tenant import tenant_session
from app.services.artifact_service import ArtifactService, resolve_mime_type

router = APIRouter(prefix="/api/v1/artifacts", tags=["artifacts"])
artifact_service = ArtifactService()


class ArtifactMetadataResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    task_id: UUID
    user_id: UUID
    file_name: str
    file_type: str
    size_bytes: int
    sha256: str
    verification_status: str
    verification_report: dict[str, Any] | None
    created_at: str
    # True when the file lives outside MAYA's own artifact storage (the user's connected folder).
    # MAYA never deletes such a file from disk.
    in_connected_folder: bool = False


def is_in_managed_storage(storage_uri: str, storage_root: Path) -> bool:
    """True when the stored path resolves inside MAYA's artifact storage root (no disk access)."""
    try:
        resolved = Path(storage_uri).resolve()
        resolved.relative_to(storage_root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _managed_path(storage_uri: str, storage_root: Path) -> Path | None:
    """The resolved file path when it is inside MAYA's storage, otherwise None (never delete)."""
    if not is_in_managed_storage(storage_uri, storage_root):
        return None
    return Path(storage_uri).resolve()


def _to_response(a: FileArtifact) -> ArtifactMetadataResponse:
    return ArtifactMetadataResponse(
        id=a.id,
        workspace_id=a.workspace_id,
        task_id=a.task_id,
        user_id=a.user_id,
        file_name=a.file_name,
        file_type=a.file_type,
        size_bytes=a.size_bytes,
        sha256=a.sha256,
        verification_status=a.verification_status,
        verification_report=a.verification_report,
        created_at=a.created_at.isoformat(),
        in_connected_folder=not is_in_managed_storage(a.storage_uri, artifact_service.storage_root),
    )


@router.get("", response_model=list[ArtifactMetadataResponse])
async def list_artifacts(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
    task_id: Annotated[UUID | None, Query()] = None,
) -> list[ArtifactMetadataResponse]:
    """List artifacts belonging to the authenticated tenant."""
    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        query = select(FileArtifact).where(FileArtifact.workspace_id == principal.workspace_id)
        if task_id is not None:
            query = query.where(FileArtifact.task_id == task_id)
        query = query.order_by(FileArtifact.created_at.desc())

        res = await session.execute(query)
        artifacts = res.scalars().all()

        return [_to_response(a) for a in artifacts]


@router.get("/{artifact_id}/metadata", response_model=ArtifactMetadataResponse)
async def get_artifact_metadata(
    artifact_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> ArtifactMetadataResponse:
    """Retrieve metadata and verification report for a specific artifact."""
    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        artifact = await artifact_service.get_artifact(session, artifact_id, principal.workspace_id)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "artifact_not_found"},
            )

        return _to_response(artifact)


def _locate_artifact_file(
    storage_uri: str,
    storage_root: Path,
    workspace_id: UUID,
    task_id: UUID,
    file_name: str,
) -> Path | None:
    """Locate an artifact file on disk across root variations and relative paths."""
    primary = Path(storage_uri).resolve()
    if primary.is_file():
        return primary

    candidates = [
        Path(storage_uri),
        Path("backend") / storage_uri,
        storage_root / str(workspace_id) / str(task_id) / file_name,
        Path(__file__).resolve().parents[3]
        / "backend"
        / "storage"
        / "artifacts"
        / str(workspace_id)
        / str(task_id)
        / file_name,
        Path(__file__).resolve().parents[2]
        / "storage"
        / "artifacts"
        / str(workspace_id)
        / str(task_id)
        / file_name,
    ]
    for cand in candidates:
        resolved = cand.resolve()
        if resolved.is_file():
            return resolved
    return None


@router.get("/{artifact_id}")
async def download_artifact(
    artifact_id: UUID,
    # GET file link: a browser opens it directly, so ``?token=`` is accepted here and only here.
    principal: Annotated[Principal, Depends(get_file_link_principal)],
    db: Annotated[TenantDatabase, Depends(get_file_link_tenant_database)],
    inline: Annotated[
        bool,
        Query(description="Open file inline rather than forcing attachment download"),
    ] = False,
) -> FileResponse:
    """Download an artifact file with tenant RLS isolation and verification checksums."""
    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        artifact = await artifact_service.get_artifact(session, artifact_id, principal.workspace_id)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "artifact_not_found"},
            )

        file_path = await asyncio.to_thread(
            _locate_artifact_file,
            artifact.storage_uri,
            artifact_service.storage_root,
            artifact.workspace_id,
            artifact.task_id,
            artifact.file_name,
        )
        if file_path is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "artifact_file_missing_from_storage"},
            )

        mime = resolve_mime_type(artifact.file_name, artifact.file_type)

        return FileResponse(
            path=str(file_path),
            media_type=mime,
            filename=artifact.file_name,
            content_disposition_type="inline" if inline else "attachment",
            headers={
                "X-Artifact-Sha256": artifact.sha256,
                "X-Verification-Status": artifact.verification_status,
            },
        )


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    artifact_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> None:
    """Delete an artifact record and MAYA's own stored copy.

    A file that lives outside MAYA's artifact storage (the user's connected folder) is never
    removed from disk: only the record goes, and that is not offered by the UI.
    """
    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        artifact = await artifact_service.get_artifact(session, artifact_id, principal.workspace_id)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "artifact_not_found"},
            )
        file_path = await asyncio.to_thread(_managed_file_for, artifact)
        await session.delete(artifact)
        await session.commit()

    if file_path is not None:
        await asyncio.to_thread(_remove_file, file_path)


def _managed_file_for(artifact: FileArtifact) -> Path | None:
    """The artifact's file when it is found inside MAYA's storage; never a path outside it."""
    located = _locate_artifact_file(
        artifact.storage_uri,
        artifact_service.storage_root,
        artifact.workspace_id,
        artifact.task_id,
        artifact.file_name,
    )
    if located is None:
        return None
    return _managed_path(str(located), artifact_service.storage_root)


def _remove_file(path: Path) -> None:
    """Best effort: the record is already gone, so a missing or busy file is not an error."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


class ArtifactBulkDeleteRequest(BaseModel):
    """Delete several files at once (at most 100 per request)."""

    ids: list[UUID] = Field(..., min_length=1, max_length=100)


class ArtifactBulkDeleteResponse(BaseModel):
    deleted: list[UUID] = Field(default_factory=list)
    not_found: list[UUID] = Field(default_factory=list)


@router.post("/delete", response_model=ArtifactBulkDeleteResponse)
async def delete_artifacts_bulk(
    payload: ArtifactBulkDeleteRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> ArtifactBulkDeleteResponse:
    """Delete the caller's files: records and MAYA's own stored copies. A copy in the user's
    connected folder stays. Ids that are unknown, foreign or already deleted are ``not_found``."""
    result = ArtifactBulkDeleteResponse()
    paths: list[Path] = []
    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        for artifact_id in dict.fromkeys(payload.ids):
            artifact = await artifact_service.get_artifact(
                session, artifact_id, principal.workspace_id
            )
            if artifact is None:
                result.not_found.append(artifact_id)
                continue
            found = await asyncio.to_thread(_managed_file_for, artifact)
            if found is not None:
                paths.append(found)
            await session.delete(artifact)
            result.deleted.append(artifact_id)
        await session.commit()
    for path in paths:
        await asyncio.to_thread(_remove_file, path)
    return result
