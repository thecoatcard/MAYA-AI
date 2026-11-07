"""Document Specialist Agent for Artifact Generation and Checksum Verification (Phase 09/15)."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from app.agents.base import BaseSpecialistAgent
from app.agents.schemas import (
    AgentExecutionStatus,
    SpecialistAgentResult,
    SpecialistAgentRole,
)
from app.automation.pdf_engine import create_pdf
from app.automation.pptx_engine import create_presentation
from app.automation.verifiers.pdf_verifier import verify_pdf
from app.automation.verifiers.pptx_verifier import verify_pptx
from app.automation.verifiers.xlsx_verifier import verify_xlsx
from app.automation.xlsx_engine import create_spreadsheet
from app.mcp.client_manager import MCPClientManager
from app.mcp.connectors.filesystem import FilesystemConnector
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.tasks.queue import SubtaskJob

logger = logging.getLogger(__name__)


class DocumentAgent(BaseSpecialistAgent):
    """Specialist agent for structured document artifact generation and checksum verification."""

    role = SpecialistAgentRole.DOCUMENT
    model_alias = "orchestrator"
    permission_ceiling = ToolTier.WRITE
    assigned_tools = frozenset({"file_read", "file_write_artifact", "generate_document"})

    def __init__(
        self,
        artifacts_base_dir: Path | str = "/artifacts",
        permission_registry: ToolPermissionRegistry | None = None,
        client_manager: MCPClientManager | None = None,
    ) -> None:
        super().__init__(permission_registry=permission_registry, client_manager=client_manager)
        self.artifacts_base_dir = Path(artifacts_base_dir).resolve()

    async def execute_subtask(
        self,
        job: SubtaskJob,
        context: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """Generate document artifact inside workspace directory and calculate checksum."""
        ctx = context or {}
        filename = str(ctx.get("filename", "document.md")).strip()
        doc_type = str(ctx.get("file_type") or ctx.get("doc_type") or "").lower()

        workspace_dir = self.artifacts_base_dir / str(job.workspace_id)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        target_file = workspace_dir / filename

        verification_proof: dict[str, Any] = {}

        if doc_type == "pdf" or filename.lower().endswith(".pdf"):
            title = str(ctx.get("title", "Document Report"))
            subtitle = str(ctx.get("subtitle", ""))
            sections = ctx.get("sections")
            if not sections:
                content = str(ctx.get("content", ""))
                sections = [{"type": "paragraph", "text": content}] if content else []
            out = create_pdf(
                title=title, subtitle=subtitle, sections=sections, output_path=target_file
            )
            report = verify_pdf(out)
            verification_proof = {
                "filename": filename,
                "file_type": "pdf",
                "status": report.status,
                "total_pages": report.metadata.get("total_pages", 0),
                "checks_passed": report.checks_passed,
                "file_checksum": report.sha256,
                "size_bytes": report.size_bytes,
            }
        elif doc_type == "xlsx" or filename.lower().endswith(".xlsx"):
            sheets = ctx.get("sheets", [])
            out = create_spreadsheet(sheets=sheets, output_path=target_file)
            report = verify_xlsx(out)
            verification_proof = {
                "filename": filename,
                "file_type": "xlsx",
                "status": report.status,
                "formulas_checked": report.metadata.get("formulas_checked", 0),
                "checks_passed": report.checks_passed,
                "file_checksum": report.sha256,
                "size_bytes": report.size_bytes,
            }
        elif doc_type == "pptx" or filename.lower().endswith(".pptx"):
            slides = ctx.get("slides", [])
            out = create_presentation(slides_data=slides, output_path=target_file)
            report = verify_pptx(out)
            verification_proof = {
                "filename": filename,
                "file_type": "pptx",
                "status": report.status,
                "total_slides": report.metadata.get("total_slides", 0),
                "checks_passed": report.checks_passed,
                "file_checksum": report.sha256,
                "size_bytes": report.size_bytes,
            }
        else:
            content = str(ctx.get("content", ""))
            fs = FilesystemConnector(
                base_dir=self.artifacts_base_dir, workspace_id=job.workspace_id
            )
            fs.write_file(filename, content)
            file_bytes = target_file.read_bytes()
            checksum = hashlib.sha256(file_bytes).hexdigest()
            verification_proof = {
                "filename": filename,
                "file_checksum": checksum,
                "size_bytes": len(file_bytes),
            }

        return SpecialistAgentResult(
            agent_role=self.role,
            status=AgentExecutionStatus.SUCCESS,
            summary=f"Document '{filename}' successfully generated and verified.",
            verification_proof=verification_proof,
            artifact_paths=[str(target_file)],
        )
