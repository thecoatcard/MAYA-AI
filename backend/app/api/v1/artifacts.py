"""Artifact Delivery and Download API."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
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

        return [
            ArtifactMetadataResponse(
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
            )
            for a in artifacts
        ]


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

        return ArtifactMetadataResponse(
            id=artifact.id,
            workspace_id=artifact.workspace_id,
            task_id=artifact.task_id,
            user_id=artifact.user_id,
            file_name=artifact.file_name,
            file_type=artifact.file_type,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            verification_status=artifact.verification_status,
            verification_report=artifact.verification_report,
            created_at=artifact.created_at.isoformat(),
        )


@router.get("/{artifact_id}")
async def download_artifact(
    artifact_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> FileResponse:
    """Download an artifact file with tenant RLS isolation and verification checksums."""
    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        artifact = await artifact_service.get_artifact(session, artifact_id, principal.workspace_id)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "artifact_not_found"},
            )

        file_path = await asyncio.to_thread(lambda: Path(artifact.storage_uri).resolve())
        is_file = await asyncio.to_thread(file_path.is_file)
        if not is_file:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "artifact_file_missing_from_storage"},
            )

        mime = resolve_mime_type(artifact.file_name, artifact.file_type)

        return FileResponse(
            path=str(file_path),
            media_type=mime,
            filename=artifact.file_name,
            headers={
                "X-Artifact-Sha256": artifact.sha256,
                "X-Verification-Status": artifact.verification_status,
            },
        )
