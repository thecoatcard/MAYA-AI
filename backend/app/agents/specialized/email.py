"""Email Specialist Agent with autonomous drafting and approval-gated sending (Phase 09)."""

from __future__ import annotations

import logging
from typing import Any

from app.agents.base import BaseSpecialistAgent
from app.agents.schemas import (
    AgentExecutionStatus,
    SpecialistAgentResult,
    SpecialistAgentRole,
)
from app.mcp.client_manager import MCPClientManager
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.tasks.queue import SubtaskJob
from mcp_servers.google_adapter.gmail import GoogleGmailService

logger = logging.getLogger(__name__)


class EmailAgent(BaseSpecialistAgent):
    """Specialist agent for email triage, autonomous draft creation, and gated delivery."""

    role = SpecialistAgentRole.EMAIL
    model_alias = "orchestrator"
    permission_ceiling = ToolTier.APPROVAL_REQUIRED
    assigned_tools = frozenset(
        {
            "read_emails",
            "gmail_search",
            "gmail_read_thread",
            "draft_email",
            "gmail_create_draft",
            "gmail_send",
        }
    )

    def __init__(
        self,
        gmail_service: GoogleGmailService | None = None,
        permission_registry: ToolPermissionRegistry | None = None,
        client_manager: MCPClientManager | None = None,
    ) -> None:
        super().__init__(permission_registry=permission_registry, client_manager=client_manager)
        self.gmail_service = gmail_service

    async def execute_subtask(
        self,
        job: SubtaskJob,
        context: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """Execute email subtask: draft autonomously, send only when approval token is present."""
        ctx = context or {}
        to = str(ctx.get("to", "")).strip()
        subject = str(ctx.get("subject", "")).strip()
        body = str(ctx.get("body", "")).strip()
        has_approval = bool(ctx.get("has_valid_approval_token", False))

        if not self.gmail_service:
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.FAILED,
                summary="GoogleGmailService is required for EmailAgent execution.",
                verification_proof={"error": "missing_gmail_service"},
            )

        # 1. Create draft autonomously (WRITE tier) only if not already drafted
        draft_id = str(ctx.get("draft_id", "")).strip()
        if not draft_id:
            draft = await self.gmail_service.create_draft(to=to, subject=subject, body=body)
            draft_id = draft.get("id", "")

        # 2. Check for human approval to send externally
        if not has_approval:
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.PARTIAL,
                summary=(
                    f"Draft email to '{to}' created successfully. Awaiting human approval to send."
                ),
                verification_proof={"draft_id": draft_id},
                requires_human_approval=True,
                approval_context={
                    "to": to,
                    "subject": subject,
                    "body": body,
                    "draft_id": draft_id,
                },
            )

        # 3. Send email with valid approval token (APPROVAL_REQUIRED tier)
        sent = await self.gmail_service.send_email(
            to=to,
            subject=subject,
            body=body,
            has_valid_approval_token=True,
        )

        return SpecialistAgentResult(
            agent_role=self.role,
            status=AgentExecutionStatus.SUCCESS,
            summary=f"Email successfully sent to '{to}'.",
            verification_proof={
                "draft_id": draft_id,
                "message_id": sent.get("id", ""),
            },
        )
