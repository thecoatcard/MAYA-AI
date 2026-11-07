"""Artifact Storage and Verification Service."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.verifiers import (
    VerificationReport,
    compute_file_sha256,
    verify_docx,
    verify_pdf,
    verify_pptx,
    verify_xlsx,
)
from app.db.models import FileArtifact

DEFAULT_STORAGE_ROOT = Path("storage/artifacts")

MIME_TYPE_MAP = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
}


def resolve_mime_type(file_name: str, file_type: str) -> str:
    """Resolve standard MIME type for an artifact."""
    if file_type.lower() in MIME_TYPE_MAP:
        return MIME_TYPE_MAP[file_type.lower()]
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"


class ArtifactService:
    """Service for storing, verifying, and retrieving file artifacts."""

    def __init__(self, storage_root: Path | str = DEFAULT_STORAGE_ROOT) -> None:
        self.storage_root = Path(storage_root).resolve()

    def _resolve_storage_path(self, workspace_id: UUID, task_id: UUID, file_name: str) -> Path:
        """Resolve workspace-isolated path on disk."""
        target_dir = self.storage_root / str(workspace_id) / str(task_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        # Sanitize file name against path traversal
        clean_name = Path(file_name).name
        return target_dir / clean_name

    def verify_file(
        self,
        file_path: Path | str,
        file_type: str,
        **verifier_kwargs: Any,
    ) -> VerificationReport:
        """Run deterministic programmatic verifier on a file based on its type."""
        ft = file_type.lower().strip().lstrip(".")
        if ft == "xlsx":
            return verify_xlsx(file_path)
        elif ft == "docx":
            return verify_docx(file_path)
        elif ft == "pptx":
            expected_slides = verifier_kwargs.get("expected_slides")
            return verify_pptx(file_path, expected_slides=expected_slides)
        elif ft == "pdf":
            min_pages = verifier_kwargs.get("min_pages", 1)
            return verify_pdf(file_path, min_pages=min_pages)
        else:
            # Fallback for unrecognized file types
            path = Path(file_path).resolve()
            size = os.path.getsize(path) if path.is_file() else 0
            sha = compute_file_sha256(path) if path.is_file() else ""
            report_ft: Any = ft if ft in ("xlsx", "docx", "pptx", "pdf") else "pdf"
            return VerificationReport(
                file_type=report_ft,
                status="verified" if path.is_file() else "failed",
                sha256=sha,
                size_bytes=size,
                checks_passed=["file_exists"] if path.is_file() else [],
                errors=[] if path.is_file() else ["File not found"],
            )

    async def store_artifact(
        self,
        session: AsyncSession,
        *,
        workspace_id: UUID,
        task_id: UUID,
        user_id: UUID,
        source_path: Path | str,
        file_type: str,
        file_name: str | None = None,
        run_verifier: bool = True,
        verifier_kwargs: dict[str, Any] | None = None,
    ) -> FileArtifact:
        """Store an artifact, verify its integrity, and persist the record in PostgreSQL."""
        src = await asyncio.to_thread(lambda: Path(source_path).resolve())
        is_src = await asyncio.to_thread(src.is_file)
        if not is_src:
            raise FileNotFoundError(f"Source artifact not found: {src}")

        clean_name = file_name or src.name
        dest_path = self._resolve_storage_path(workspace_id, task_id, clean_name)

        # Copy to storage
        if src != dest_path:
            await asyncio.to_thread(shutil.copy2, src, dest_path)

        size_bytes = await asyncio.to_thread(os.path.getsize, dest_path)
        sha256 = await asyncio.to_thread(compute_file_sha256, dest_path)

        # Execute deterministic verifier
        verification_status: str
        report_dict: dict[str, Any] | None
        if run_verifier:
            kwargs = verifier_kwargs or {}
            report = await asyncio.to_thread(self.verify_file, dest_path, file_type, **kwargs)
            verification_status = report.status
            report_dict = report.model_dump()
        else:
            verification_status = "pending"
            report_dict = None

        artifact = FileArtifact(
            id=uuid4(),
            workspace_id=workspace_id,
            task_id=task_id,
            user_id=user_id,
            file_name=clean_name,
            file_type=file_type.lower().strip().lstrip("."),
            storage_uri=str(dest_path),
            size_bytes=size_bytes,
            sha256=sha256,
            verification_status=verification_status,
            verification_report=report_dict,
        )

        session.add(artifact)
        await session.flush()
        return artifact

    async def get_artifact(
        self,
        session: AsyncSession,
        artifact_id: UUID,
        workspace_id: UUID,
    ) -> FileArtifact | None:
        """Retrieve an artifact by ID, strictly enforcing workspace tenant boundaries."""
        stmt = select(FileArtifact).where(
            FileArtifact.id == artifact_id,
            FileArtifact.workspace_id == workspace_id,
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()
